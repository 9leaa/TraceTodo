from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
import tempfile
import unittest

from trace_memory.api.app import ApiDispatcher
from trace_memory.config import TraceMemoryConfig
from trace_memory.container import AppContainer
from trace_memory.domain.notes import BEIJING_TZ, beijing_now, beijing_today_date, extract_beijing_time_range
from trace_memory.domain.proposals import ChangeProposal
from trace_memory.services.memo_journal_service import MOOD_CATALOG


REPO_ROOT = Path(__file__).resolve().parents[1]


class TestUxCalendarMoodsFixes(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        self.cfg = TraceMemoryConfig(
            default_space="default",
            data_dir=root / "data",
            knowledge_dir=root / "knowledge",
            auth_required=False,
        )
        self.container = AppContainer.build(self.cfg)
        self.api = ApiDispatcher(self.container)

    def tearDown(self) -> None:
        self.container.close()
        self._tmp.cleanup()

    def test_extract_beijing_time_range_formats(self) -> None:
        ref = datetime(2026, 11, 22, 10, 0, 0, tzinfo=BEIJING_TZ)

        tr1 = extract_beijing_time_range("今天21:00-23:00，我要写作业", ref)
        self.assertEqual(tr1, ("2026-11-22T21:00:00+08:00", "2026-11-22T23:00:00+08:00", 120))

        tr2 = extract_beijing_time_range("晚上9点到11点写论文", ref)
        self.assertEqual(tr2, ("2026-11-22T21:00:00+08:00", "2026-11-22T23:00:00+08:00", 120))

        tr3 = extract_beijing_time_range("明天下午2点半到4点开会", ref)
        self.assertEqual(tr3, ("2026-11-23T14:30:00+08:00", "2026-11-23T16:00:00+08:00", 90))

        tr4 = extract_beijing_time_range("今天23:00-01:00部署发版", ref)
        self.assertEqual(tr4, ("2026-11-22T23:00:00+08:00", "2026-11-23T01:00:00+08:00", 120))

    def test_ingest_time_range_task_projects_exact_calendar_span(self) -> None:
        today = beijing_today_date()
        c_in, r_in = self.api.dispatch(
            "POST",
            "/api/notes/record",
            body={
                "space_id": "tr_user",
                "content": "今天21:00-23:00，我要写作业",
                "async_mode": False,
            },
        )
        self.assertEqual(c_in, 200)
        _, mat = self.api.dispatch("GET", "/api/tasks/matrix", {"space_id": ["tr_user"]})
        self.assertEqual(len(mat["sorted_tasks"]), 1)
        task = mat["sorted_tasks"][0]
        self.assertEqual(task["title"], "写作业")
        p = task["payload"]
        self.assertEqual(p["start_at"], f"{today}T21:00:00+08:00")
        self.assertEqual(p["due_at"], f"{today}T23:00:00+08:00")
        self.assertEqual(p["duration_minutes"], 120)
        self.assertFalse(p["is_all_day"])

        c_cal, ov = self.api.dispatch(
            "GET",
            "/api/calendar/overview",
            {"space_id": ["tr_user"], "view": ["week"], "anchor_date": [today]},
        )
        self.assertEqual(c_cal, 200)
        day_bucket = next(d for d in ov["days"] if d["date"] == today)
        self.assertEqual(len(day_bucket["timed_blocks"]), 1)
        blk = day_bucket["timed_blocks"][0]
        self.assertEqual(blk["start_minute"], 1260)
        self.assertEqual(blk["end_minute"], 1380)
        self.assertEqual(blk["duration_minutes"], 120)
        self.assertEqual(blk["time_range_label"], "21:00 - 23:00")

    def test_overnight_task_splits_cleanly_across_two_days(self) -> None:
        today = beijing_today_date()
        tomorrow = (datetime.strptime(today, "%Y-%m-%d").date() + timedelta(days=1)).isoformat()
        c_save, _ = self.api.dispatch(
            "POST",
            "/api/tasks/save",
            body={
                "space_id": "ov_user",
                "title": "凌晨发版窗口",
                "start_at": f"{today}T23:00:00+08:00",
                "due_at": f"{tomorrow}T01:00:00+08:00",
                "is_all_day": False,
            },
        )
        self.assertEqual(c_save, 200)

        c_cal, ov = self.api.dispatch(
            "GET",
            "/api/calendar/overview",
            {"space_id": ["ov_user"], "view": ["week"], "anchor_date": [today]},
        )
        self.assertEqual(c_cal, 200)
        b1 = next(d for d in ov["days"] if d["date"] == today)
        self.assertEqual(b1["timed_blocks"][0]["start_minute"], 1380)
        self.assertEqual(b1["timed_blocks"][0]["end_minute"], 1440)
        self.assertEqual(b1["timed_blocks"][0]["time_range_label"], "23:00 - 24:00")

        b2 = next((d for d in ov["days"] if d["date"] == tomorrow), None)
        if b2 is not None:
            self.assertEqual(b2["timed_blocks"][0]["start_minute"], 0)
            self.assertEqual(b2["timed_blocks"][0]["end_minute"], 60)
            self.assertEqual(b2["timed_blocks"][0]["time_range_label"], "00:00 - 01:00")

    def test_twelve_mood_catalog_and_negative_mood_journal_summary(self) -> None:
        self.assertEqual(len(MOOD_CATALOG), 12)
        groups = {m.get("group") for m in MOOD_CATALOG}
        self.assertEqual(groups, {"positive", "neutral", "negative"})
        neg_ids = {m["id"] for m in MOOD_CATALOG if m.get("group") == "negative"}
        self.assertEqual(neg_ids, {"sad", "anxious", "angry", "low"})

        today = beijing_today_date()
        c_save, r_save = self.api.dispatch(
            "POST",
            "/api/journal/save",
            body={
                "space_id": "neg_mood_user",
                "journal_date": today,
                "mood": "anxious",
                "content": "今天线上排查耗时很久，有点焦虑 #复盘",
            },
        )
        self.assertEqual(c_save, 200)
        self.assertEqual(r_save["journal"]["mood"], "anxious")
        self.assertEqual(r_save["journal"]["mood_meta"]["label"], "焦虑")
        self.assertEqual(r_save["journal"]["mood_meta"]["group"], "negative")

        c_sum, r_sum = self.api.dispatch(
            "POST",
            "/api/journal/ai-summary",
            body={"space_id": "neg_mood_user", "date": today, "mood": "anxious"},
        )
        self.assertEqual(c_sum, 200)
        self.assertIn("情绪关照", r_sum["summary_markdown"])

    def test_priority_reason_escalation_strips_trailing_punctuation(self) -> None:
        base_now = beijing_now().replace(microsecond=0)
        due_dt = base_now + timedelta(days=10)
        res = self.container.change_service.apply(
            ChangeProposal(
                space_id="prio_clean",
                action="create",
                memory_type="task",
                actor_id="user",
                origin="user_direct",
                title="完成家庭作业",
                summary="完成家庭作业",
                canonical_key="task:self:hw",
                payload={
                    "task_status": "todo",
                    "importance": 3,
                    "urgency": 2,
                    "due_at": due_dt.isoformat(),
                    "priority_reason": "有明确截止日期，属于常规但有时效性的任务。",
                },
            )
        )
        self.assertIsNotNone(res.memory)
        self.container.task_matrix_service.refresh_due_task_statuses(
            "prio_clean", now=due_dt - timedelta(hours=6)
        )
        updated = self.container.memory_repo.get("prio_clean", res.memory.memory_id)
        assert updated is not None
        pr = str(updated.payload.get("priority_reason", ""))
        self.assertNotIn("。；", pr)
        self.assertIn("常规但有时效性的任务；临近截止自动提权", pr)

    def test_dashboard_html_ux_calendar_and_copy_markers(self) -> None:
        html = (REPO_ROOT / "src" / "trace_memory" / "api" / "dashboard.html").read_text(encoding="utf-8")
        for expected in (
            "cache: 'no-store'",
            "data-chk-count",
            "data-chk-fill",
            "cd-form-grid",
            "minmax(0, 1fr)",
            "memo-empty-card",
            "column-span: all",
            "minuteToY",
            "calWeekDensityToggle",
            "tsStartTime",
            "wrapGraphText",
            "id: 'anxious'",
            "id: 'sad'",
            "id: 'angry'",
            "id: 'low'",
        ):
            self.assertIn(expected, html, f"Missing expected marker: {expected}")

        for forbidden in (
            "三合一收敛视图",
            "万物复用 RawNote 轻结构化内核",
        ):
            self.assertNotIn(forbidden, html, f"Deprecated jargon should be removed: {forbidden}")


if __name__ == "__main__":
    unittest.main()
