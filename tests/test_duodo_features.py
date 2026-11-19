"""Tests for DuoDo-inspired Task Cards, AI Subtask Decomposition, Repeat, Auto-Rollover, Focus Lock & Supervision."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import tempfile
import unittest

from trace_memory.api.app import ApiDispatcher
from trace_memory.config import TraceMemoryConfig
from trace_memory.container import AppContainer
from trace_memory.domain.proposals import ValidationError, VersionConflictError


class TestDuoDoFeatures(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        base = Path(self._tmp.name)
        self.app = AppContainer.build(
            TraceMemoryConfig(
                default_space="duodo_user",
                data_dir=base / "data",
                knowledge_dir=base / "knowledge",
            )
        )
        self.api = ApiDispatcher(self.app)

    def tearDown(self) -> None:
        self.app.close()
        self._tmp.cleanup()

    def test_save_task_card_and_memory_driven_ai_decompose(self) -> None:
        # 1. Seed personal semantic/preference memory in this space
        self.app.ingest_service.record_note("duodo_user", "我非常喜欢极简风格布置和手冲咖啡")
        self.app.ingest_service.record_note("duodo_user", "2026年12月24日要举办婚庆仪式")

        # 2. Call AI decompose (`✨ AI 拆解`) -> retrieves personal memories & yields 3-6 checklist items
        code_d, dec = self.api.dispatch(
            "POST",
            "/api/tasks/decompose",
            body={"space_id": "duodo_user", "title": "准备12月24日婚庆用品", "summary": "提前采购物料"},
        )
        self.assertEqual(code_d, 200)
        self.assertGreaterEqual(len(dec["subtasks"]), 3)
        self.assertLessEqual(len(dec["subtasks"]), 6)
        self.assertTrue(len(dec["memory_context_used"]) >= 1)

        # 3. Create task card via DuoDo modal payload (`POST /api/tasks/save`)
        due_soon = (datetime.now().astimezone() + timedelta(hours=3)).replace(microsecond=0).isoformat()
        code_s, saved = self.api.dispatch(
            "POST",
            "/api/tasks/save",
            body={
                "space_id": "duodo_user",
                "title": "准备12月24日婚庆用品",
                "color_theme": "pink",
                "task_mode": "personal",
                "supervision_enabled": False,
                "due_at": due_soon,
                "is_all_day": False,
                "reminder_offset_minutes": 15,
                "auto_rollover": True,
                "focus_lock_enabled": True,
                "focus_minutes": 45,
                "importance": 4,
                "urgency": 2,
                "subtasks": dec["subtasks"],
            },
        )
        self.assertEqual(code_s, 200)
        mem = saved["memory"]
        self.assertEqual(mem["payload"]["color_theme"], "pink")
        self.assertEqual(mem["payload"]["focus_minutes"], 45)
        self.assertTrue(mem["payload"]["auto_rollover"])
        # Today + specific time within 3h dynamically promotes urgency -> Q1!
        self.assertEqual(mem["payload"]["effective_urgency"], 5)
        self.assertEqual(mem["payload"]["quadrant"], "Q1_do_first")

        # 4. Toggle all subtasks -> `all_subtasks_done` becomes True
        cur_ver = mem["version"]
        last_toggle = None
        for sub in mem["payload"]["subtasks"]:
            code_t, last_toggle = self.api.dispatch(
                "POST",
                "/api/tasks/subtasks/toggle",
                body={
                    "space_id": "duodo_user",
                    "memory_id": mem["memory_id"],
                    "subtask_id": sub["id"],
                    "done": True,
                    "expected_version": cur_ver,
                },
            )
            self.assertEqual(code_t, 200)
            cur_ver = last_toggle["memory"]["version"]
        assert last_toggle is not None
        self.assertTrue(last_toggle["all_subtasks_done"])

        # CAS stale version check
        code_err, err_body = self.api.dispatch(
            "POST",
            "/api/tasks/subtasks/toggle",
            body={
                "space_id": "duodo_user",
                "memory_id": mem["memory_id"],
                "subtask_id": mem["payload"]["subtasks"][0]["id"],
                "done": False,
                "expected_version": 1,
            },
        )
        self.assertEqual(code_err, 409)
        self.assertEqual(err_body["code"], "VERSION_CONFLICT")

    def test_repeating_task_spawns_next_cycle_with_reset_subtasks(self) -> None:
        base_due = "2026-11-18T18:00:00+08:00"
        res = self.app.task_matrix_service.save_task_card(
            "duodo_user",
            {
                "title": "提交团队技术周报",
                "due_at": base_due,
                "is_all_day": False,
                "repeat_rule": "weekly",
                "importance": 4,
                "urgency": 4,
                "subtasks": [
                    {"id": "s1", "title": "汇总本周线上变更", "done": True},
                    {"id": "s2", "title": "整理下周重点计划", "done": True},
                ],
            },
        )
        mem = res.memory
        assert mem is not None

        code, out = self.api.dispatch(
            "POST",
            "/api/tasks/transition",
            body={
                "space_id": "duodo_user",
                "memory_id": mem.memory_id,
                "action": "complete_task",
                "expected_version": mem.version,
            },
        )
        self.assertEqual(code, 200)
        self.assertEqual(out["memory"]["payload"]["task_status"], "done")
        next_task = out["next_task"]
        self.assertIsNotNone(next_task)
        self.assertEqual(next_task["payload"]["task_status"], "todo")
        self.assertTrue(next_task["payload"]["due_at"].startswith("2026-11-25T18:00:00"))
        self.assertTrue(all(not s["done"] for s in next_task["payload"]["subtasks"]))

    def test_auto_rollover_moves_yesterday_task_to_today_and_warns_after_3_days(self) -> None:
        tz = timezone(timedelta(hours=8))
        day1 = datetime(2026, 11, 15, 10, 0, tzinfo=tz)
        res = self.app.task_matrix_service.save_task_card(
            "duodo_user",
            {
                "title": "整理历史架构债清单",
                "due_at": day1.replace(hour=23, minute=59, second=59).isoformat(),
                "is_all_day": True,
                "auto_rollover": True,
                "importance": 4,
                "urgency": 2,
            },
        )
        mem = res.memory
        assert mem is not None

        # Simulate 3 consecutive days passing without completion
        for offset in (1, 2, 3):
            check_day = day1 + timedelta(days=offset)
            matrix = self.app.task_matrix_service.build_matrix_analysis("duodo_user", now=check_day)
            # Idempotent re-check on the same day must NOT increment rollover_count again!
            matrix_same = self.app.task_matrix_service.build_matrix_analysis("duodo_user", now=check_day)
            self.assertEqual(
                matrix["sorted_tasks"][0]["payload"]["rollover_count"],
                matrix_same["sorted_tasks"][0]["payload"]["rollover_count"],
            )

        final_matrix = self.app.task_matrix_service.build_matrix_analysis(
            "duodo_user", now=day1 + timedelta(days=3)
        )
        task_after = final_matrix["sorted_tasks"][0]
        self.assertEqual(task_after["payload"]["rollover_count"], 3)
        self.assertTrue(task_after["payload"]["due_at"].startswith("2026-11-18"))
        self.assertTrue(any("已连续顺延 3 天" in d for d in final_matrix["diagnostics"]))

    def test_focus_session_and_privacy_guarded_supervision_board(self) -> None:
        # 1. Create one private personal task and one supervised multiplayer task
        priv = self.app.task_matrix_service.save_task_card(
            "duodo_user",
            {
                "title": "私人日记隐私事项",
                "task_mode": "personal",
                "supervision_enabled": False,
                "focus_lock_enabled": True,
                "focus_minutes": 25,
            },
        ).memory
        pub = self.app.task_matrix_service.save_task_card(
            "duodo_user",
            {
                "title": "多人联调 API 接口上线",
                "task_mode": "multiplayer",
                "supervision_enabled": True,
                "assignee": "Xander",
                "focus_lock_enabled": True,
                "focus_minutes": 30,
            },
        ).memory
        assert priv is not None and pub is not None

        # 2. Focus events: abort then complete
        code_f1, f1 = self.api.dispatch(
            "POST",
            "/api/tasks/focus",
            body={
                "space_id": "duodo_user",
                "memory_id": pub.memory_id,
                "event": "abort_session",
                "reason": "临时开会",
                "elapsed_seconds": 320,
                "expected_version": pub.version,
            },
        )
        self.assertEqual(code_f1, 200)
        self.assertEqual(len(f1["memory"]["payload"]["focus_interruptions"]), 1)

        code_f2, f2 = self.api.dispatch(
            "POST",
            "/api/tasks/focus",
            body={
                "space_id": "duodo_user",
                "memory_id": pub.memory_id,
                "event": "complete_session",
                "expected_version": f1["memory"]["version"],
            },
        )
        self.assertEqual(code_f2, 200)
        self.assertEqual(f2["memory"]["payload"]["focus_sessions_completed"], 1)

        # 3. Supervision board MUST hide private personal task & show supervised task
        code_b, board = self.api.dispatch("GET", "/api/supervision/board", {"space_id": ["duodo_user"]})
        self.assertEqual(code_b, 200)
        titles = [t["title"] for t in board["tasks"]]
        self.assertIn("多人联调 API 接口上线", titles)
        self.assertNotIn("私人日记隐私事项", titles)

        # 4. Nudging a private personal task is rejected; nudging supervised task succeeds
        code_n_priv, _ = self.api.dispatch(
            "POST",
            "/api/supervision/nudge",
            body={"space_id": "duodo_user", "memory_id": priv.memory_id, "kind": "nudge"},
        )
        self.assertEqual(code_n_priv, 400)

        code_n_pub, nudged = self.api.dispatch(
            "POST",
            "/api/supervision/nudge",
            body={
                "space_id": "duodo_user",
                "memory_id": pub.memory_id,
                "kind": "cheer",
                "from_user": "小明",
                "message": "加油冲！",
            },
        )
        self.assertEqual(code_n_pub, 200)
        self.assertEqual(nudged["memory"]["payload"]["nudges"][0]["from_user"], "小明")

    def test_dashboard_menu_tab_switching_and_button_integrity(self) -> None:
        from trace_memory.api.app import DASHBOARD_HTML_PATH, PWA_SW_JS

        html = DASHBOARD_HTML_PATH.read_text(encoding="utf-8")
        # Menu tabs must toggle panel visibility cleanly via CSS
        self.assertIn(".panel { display: none; }", html)
        self.assertIn(".panel.active { display: block;", html)
        self.assertIn("--header-h", html)
        for tab in ("tasks", "supervision", "memories", "graph", "ask"):
            self.assertIn(f'data-tab="{tab}"', html)
            self.assertIn(f'id="panel-{tab}" class="panel', html)
        self.assertIn("function switchTab(name, btn, opts)", html)
        self.assertIn("fallbackCopyText", html)
        self.assertIn("withBtnBusy", html)
        self.assertIn("trace-todo-shell-v3", PWA_SW_JS)


if __name__ == "__main__":
    unittest.main()
