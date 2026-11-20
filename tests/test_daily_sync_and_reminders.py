"""Tests for Daily Due-Date Scheduler, Cross-Port Space Sync Version/SSE, and Task Reminder Defaults."""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
import tempfile
import unittest

from trace_memory.api.app import ApiDispatcher
from trace_memory.config import TraceMemoryConfig
from trace_memory.container import AppContainer
from trace_memory.domain.notes import BEIJING_TZ
from trace_memory.llm.protocol import ExtractedCandidate, ExtractionBatch


class TimedTaskLLM:
    provider_name = "timed_task_mock"

    def extract_candidates(self, note_text: str, default_event_time: str, existing_memories=None):
        if "下午3点" in note_text:
            due = "2026-11-20T15:00:00+08:00"
        else:
            due = "2026-11-20T23:59:59+08:00"
        return ExtractionBatch(
            candidates=(
                ExtractedCandidate(
                    memory_type="task",
                    title=note_text[:20],
                    summary=note_text,
                    canonical_key=f"task:{abs(hash(note_text)) % 100000}",
                    payload={
                        "task_status": "todo",
                        "importance": 4,
                        "urgency": 3,
                        "priority_reason": "测试抽取",
                        "due_at": due,
                        "assignee": "self",
                    },
                    mutation_hint="upsert",
                    event_time=default_event_time,
                    quote=note_text,
                ),
            ),
            llm_calls=1,
            tokens_used=20,
        )

    def synthesize_dream_cluster(self, topic, memories):
        raise NotImplementedError

    def synthesize_answer(self, question, query_mode, memories, notes, history_versions):
        raise NotImplementedError

    def decompose_task(self, task_title, task_summary="", context_memories=None):
        raise NotImplementedError


class TestDailySyncAndReminders(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        self.cfg = TraceMemoryConfig(
            data_dir=root / "data",
            knowledge_dir=root / "knowledge",
            auth_required=False,
        )
        self.app = AppContainer.build(self.cfg)
        self.api = ApiDispatcher(self.app)

    def tearDown(self) -> None:
        self.app.close()
        self._tmp.cleanup()

    def test_daily_scheduler_rolls_over_and_persists_due_escalation_respecting_user_lock(self) -> None:
        now_bj = datetime(2026, 11, 20, 0, 5, 0, tzinfo=BEIJING_TZ)

        # 1. Yesterday's unfinished task with auto_rollover=True
        r_roll = self.app.task_matrix_service.save_task_card(
            space_id="s_daily",
            data={
                "title": "昨日未完成自动顺延任务",
                "due_at": "2026-11-19T23:59:59+08:00",
                "auto_rollover": True,
                "is_all_day": True,
                "importance": 4,
                "urgency": 2,
            },
        )
        assert r_roll.memory is not None

        # 2. Q2 task now within 12 hours of deadline (not locked) -> should persist escalation to Q1
        r_esc = self.app.task_matrix_service.save_task_card(
            space_id="s_daily",
            data={
                "title": "12小时内到期的Q2任务",
                "due_at": (now_bj + timedelta(hours=10)).isoformat(),
                "auto_rollover": False,
                "is_all_day": False,
                "importance": 5,
                "urgency": 2,
            },
        )
        assert r_esc.memory is not None
        # Force stored quadrant back to Q2 to simulate task created days ago before deadline approached
        with self.app.db.transaction() as conn:
            import json

            p = dict(r_esc.memory.payload)
            p["quadrant"] = "Q2_schedule"
            p["priority_score"] = 70.0
            conn.execute(
                "UPDATE memories SET payload_json = ? WHERE memory_id = ?",
                (json.dumps(p, ensure_ascii=False), r_esc.memory.memory_id),
            )

        # 3. User-locked Q2 task near deadline -> quadrant must remain locked Q2
        r_locked_init = self.app.task_matrix_service.save_task_card(
            space_id="s_daily",
            data={
                "title": "用户手动锁定的Q2任务",
                "due_at": (now_bj + timedelta(hours=8)).isoformat(),
                "auto_rollover": False,
                "importance": 5,
                "urgency": 2,
            },
        )
        assert r_locked_init.memory is not None
        r_locked = self.app.task_matrix_service.update_task_quadrant_or_priority(
            space_id="s_daily",
            memory_id=r_locked_init.memory.memory_id,
            quadrant="Q2_schedule",
        )
        assert r_locked.memory is not None
        self.assertTrue(r_locked.memory.user_locked)

        v_before = self.app.sync_tracker.get_state("s_daily")["sync_version"]
        report = self.app.daily_scheduler.run_once(now=now_bj)
        v_after = self.app.sync_tracker.get_state("s_daily")["sync_version"]

        self.assertEqual(report["beijing_date"], "2026-11-20")
        self.assertGreaterEqual(report["rolled_count"], 1)
        self.assertGreaterEqual(report["refreshed_count"], 1)
        self.assertGreater(v_after, v_before)

        # Direct DB read (without calling /api/tasks/matrix) sees persisted rollover + Q1 escalation
        m_roll = self.app.memory_repo.get("s_daily", r_roll.memory.memory_id)
        assert m_roll is not None
        self.assertEqual(m_roll.payload["last_rollover_date"], "2026-11-20")
        self.assertEqual(m_roll.payload["rollover_count"], 1)

        m_esc = self.app.memory_repo.get("s_daily", r_esc.memory.memory_id)
        assert m_esc is not None
        self.assertEqual(m_esc.payload["quadrant"], "Q1_do_first")

        m_locked = self.app.memory_repo.get("s_daily", r_locked.memory.memory_id)
        assert m_locked is not None
        self.assertEqual(m_locked.payload["quadrant"], "Q2_schedule")

    def test_cross_port_sync_version_bumps_per_space_and_refresh_status_api(self) -> None:
        c0, s0 = self.api.dispatch("GET", "/api/sync/state", {"space_id": ["space_a"]})
        self.assertEqual(c0, 200)
        self.assertEqual(s0["sync_version"], 0)
        self.assertTrue(s0["server_time"].endswith("+08:00"))

        # Another client writes a task into space_a
        c_save, _ = self.api.dispatch(
            "POST",
            "/api/tasks/save",
            body={
                "space_id": "space_a",
                "title": "来自Web端的新待办",
                "importance": 4,
                "urgency": 4,
            },
        )
        self.assertEqual(c_save, 200)

        c1, s1 = self.api.dispatch("GET", "/api/sync/state", {"space_id": ["space_a"]})
        self.assertEqual(c1, 200)
        self.assertGreater(s1["sync_version"], s0["sync_version"])

        # space_b remains untouched (strict per-space isolation)
        _, sb = self.api.dispatch("GET", "/api/sync/state", {"space_id": ["space_b"]})
        self.assertEqual(sb["sync_version"], 0)

        # Manual status refresh endpoint returns cleanly
        c_ref, rep = self.api.dispatch(
            "POST", "/api/tasks/refresh-status", body={"space_id": "space_a"}
        )
        self.assertEqual(c_ref, 200)
        self.assertIn("sync_version", rep)
        self.assertEqual(rep["checked_spaces"], 1)

    def test_ai_extracted_timed_task_gets_default_15_min_reminder(self) -> None:
        tmp2 = tempfile.TemporaryDirectory()
        try:
            cfg2 = TraceMemoryConfig(
                data_dir=Path(tmp2.name) / "data",
                knowledge_dir=Path(tmp2.name) / "knowledge",
            )
            app2 = AppContainer.build(cfg2, llm_override=TimedTaskLLM())
            try:
                res_timed = app2.ingest_service.record_note(
                    "rem_space", "明天下午3点参加产品评审会"
                )
                timed_mem = res_timed.changes[0].memory
                assert timed_mem is not None
                self.assertEqual(timed_mem.payload["is_all_day"], False)
                self.assertEqual(timed_mem.payload["reminder_offset_minutes"], 15)

                res_allday = app2.ingest_service.record_note(
                    "rem_space", "明天整理季度文档"
                )
                allday_mem = res_allday.changes[0].memory
                assert allday_mem is not None
                self.assertIsNone(allday_mem.payload.get("reminder_offset_minutes"))
            finally:
                app2.close()
        finally:
            tmp2.cleanup()

    def test_web_and_apple_sync_notification_and_default_env_markers(self) -> None:
        root = Path(__file__).resolve().parents[1]
        html = (root / "src/trace_memory/api/dashboard.html").read_text(encoding="utf-8")
        self.assertIn("toggleWebNotifications", html)
        self.assertIn("checkRemoteSyncState", html)
        self.assertIn("/api/sync/state", html)

        bridge = (root / "apple-app/Sources/TraceTodo/SharedWidgetBridge.swift").read_text(encoding="utf-8")
        self.assertIn("http://43.228.79.133:18090", bridge)
        self.assertIn("http://127.0.0.1:18090", bridge)
        self.assertIn("group.com.tracememory.tracetodo", bridge)

        widget_src = (root / "apple-app/Widgets/TraceTodoWidgets.swift").read_text(encoding="utf-8")
        self.assertIn(".accessoryInline", widget_src)
        self.assertIn("SharedWidgetBridge", widget_src)


if __name__ == "__main__":
    unittest.main()
