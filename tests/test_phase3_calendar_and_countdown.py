from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from trace_memory.api.app import ApiDispatcher
from trace_memory.config import TraceMemoryConfig
from trace_memory.container import AppContainer


REPO_ROOT = Path(__file__).resolve().parents[1]


class TestPhase3CalendarTimeline(unittest.TestCase):
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

    def test_week_timeline_projects_timed_tasks_all_day_focus_and_overlaps(self) -> None:
        # 1. Create an unscheduled task (should land in unscheduled_tasks pool)
        c_un, r_un = self.api.dispatch(
            "POST",
            "/api/tasks/save",
            body={"space_id": "cal_user", "title": "阅读分布式系统论文", "color_theme": "mint"},
        )
        self.assertEqual(c_un, 200)
        unscheduled_id = r_un["memory"]["memory_id"]

        # 2. Schedule two overlapping timed tasks + one all-day task on 2026-11-23 (Monday)
        c_s1, r_s1 = self.api.dispatch(
            "POST",
            "/api/calendar/schedule",
            body={
                "space_id": "cal_user",
                "title": "架构评审会议",
                "date": "2026-11-23",
                "time": "14:00",
                "is_all_day": False,
                "duration_minutes": 90,
                "color_theme": "lavender",
            },
        )
        self.assertEqual(c_s1, 200)

        c_s2, _ = self.api.dispatch(
            "POST",
            "/api/calendar/schedule",
            body={
                "space_id": "cal_user",
                "title": "前端双链联调",
                "date": "2026-11-23",
                "time": "14:30",
                "is_all_day": False,
                "duration_minutes": 60,
                "color_theme": "peach",
            },
        )
        self.assertEqual(c_s2, 200)

        c_ad, _ = self.api.dispatch(
            "POST",
            "/api/calendar/schedule",
            body={
                "space_id": "cal_user",
                "title": "提交月度报销单",
                "date": "2026-11-23",
                "is_all_day": True,
                "color_theme": "butter",
            },
        )
        self.assertEqual(c_ad, 200)

        # 3. Record a completed Pomodoro focus session on 2026-11-23 10:00 - 10:25
        c_fc, _ = self.api.dispatch(
            "POST",
            "/api/focus/record",
            body={
                "space_id": "cal_user",
                "mode": "pomodoro",
                "status": "completed",
                "planned_minutes": 25,
                "duration_seconds": 1500,
                "task_title": "阅读论文专注",
                "started_at": "2026-11-23T10:00:00+08:00",
                "ended_at": "2026-11-23T10:25:00+08:00",
            },
        )
        self.assertEqual(c_fc, 200)

        # 4. Query week overview anchored on 2026-11-25 (same week Mon 11-23 .. Sun 11-29)
        c_ov, ov = self.api.dispatch(
            "GET",
            "/api/calendar/overview",
            {"space_id": ["cal_user"], "view": ["week"], "anchor_date": ["2026-11-25"]},
        )
        self.assertEqual(c_ov, 200)
        self.assertEqual(len(ov["days"]), 7)
        self.assertEqual(ov["range_start"], "2026-11-23")
        self.assertEqual(ov["range_end"], "2026-11-29")
        self.assertEqual(ov["summary"]["unscheduled_count"], 1)
        self.assertEqual(ov["unscheduled_tasks"][0]["memory_id"], unscheduled_id)

        mon = ov["days"][0]
        self.assertEqual(mon["date"], "2026-11-23")
        self.assertEqual(len(mon["all_day_tasks"]), 1)
        self.assertEqual(len(mon["timed_blocks"]), 2)
        self.assertEqual(len(mon["focus_blocks"]), 1)
        self.assertEqual(mon["focus_minutes"], 25)
        # Merged interval of [14:00-15:30] and [14:30-15:30] is 90 minutes (no double counting!)
        self.assertEqual(mon["busy_minutes"], 90)
        # Overlap columns assigned cleanly as col 0 and col 1 of 2
        cols = sorted((b["col_idx"], b["col_total"]) for b in mon["timed_blocks"])
        self.assertEqual(cols, [(0, 2), (1, 2)])

    def test_assign_unscheduled_task_to_slot_and_unschedule_shares_matrix_truth(self) -> None:
        _, r_task = self.api.dispatch(
            "POST",
            "/api/tasks/save",
            body={"space_id": "cal_user2", "title": "准备产品路演视频"},
        )
        mem_id = r_task["memory"]["memory_id"]

        # Assign existing task onto 2026-11-24 09:30 (45m)
        c_as, r_as = self.api.dispatch(
            "POST",
            "/api/calendar/schedule",
            body={
                "space_id": "cal_user2",
                "memory_id": mem_id,
                "date": "2026-11-24",
                "time": "09:30",
                "is_all_day": False,
                "duration_minutes": 45,
            },
        )
        self.assertEqual(c_as, 200)
        self.assertEqual(r_as["memory"]["memory_id"], mem_id)
        self.assertTrue(r_as["memory"]["payload"]["due_at"].startswith("2026-11-24T09:30:00"))
        self.assertEqual(r_as["memory"]["payload"]["duration_minutes"], 45)

        # Verify Month view also projects 42 cells & shows the scheduled task without creating duplicates
        c_mo, mo = self.api.dispatch(
            "GET",
            "/api/calendar/overview",
            {"space_id": ["cal_user2"], "view": ["month"], "anchor_date": ["2026-11-24"]},
        )
        self.assertEqual(c_mo, 200)
        self.assertEqual(len(mo["days"]), 42)
        self.assertEqual(mo["summary"]["unscheduled_count"], 0)
        self.assertEqual(mo["summary"]["total_timed_blocks"], 1)

        # Unschedule back to pool
        c_un, _ = self.api.dispatch(
            "POST",
            "/api/calendar/schedule",
            body={"space_id": "cal_user2", "memory_id": mem_id, "unschedule": True},
        )
        self.assertEqual(c_un, 200)
        _, mat = self.api.dispatch("GET", "/api/tasks/matrix", {"space_id": ["cal_user2"]})
        self.assertEqual(len(mat["sorted_tasks"]), 1)
        self.assertEqual(mat["sorted_tasks"][0]["payload"]["due_at"], "")

    def test_dashboard_contains_week_timeline_and_slot_modal_markers(self) -> None:
        html = (REPO_ROOT / "src" / "trace_memory" / "api" / "dashboard.html").read_text(encoding="utf-8")
        for marker in ("calWeekContainer", "calSlotModal", "paintWeekTimeline", "cal-macaron-block"):
            self.assertIn(marker, html)


if __name__ == "__main__":
    unittest.main()
