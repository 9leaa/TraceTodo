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
        self.assertTrue(r_as["memory"]["payload"]["start_at"].startswith("2026-11-24T09:30:00"))
        self.assertTrue(r_as["memory"]["payload"]["due_at"].startswith("2026-11-24T10:15:00"))
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
        for marker in (
            "calWeekContainer",
            "calSlotModal",
            "paintWeekTimeline",
            "cal-macaron-block",
            "countdownWallGrid",
            "countdownEditorModal",
        ):
            self.assertIn(marker, html)


class TestPhase3CountdownsAndLunarEngine(unittest.TestCase):
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

    def test_lunar_solar_bidirectional_accuracy_and_spring_festival(self) -> None:
        from datetime import date
        from trace_memory.services.lunar_calendar import (
            lunar_to_solar,
            next_lunar_occurrence,
            solar_to_lunar,
        )

        expected_cny = {
            2024: date(2024, 2, 10),
            2025: date(2025, 1, 29),
            2026: date(2026, 2, 17),
            2027: date(2027, 2, 6),
        }
        for yr, expected_solar in expected_cny.items():
            self.assertEqual(lunar_to_solar(yr, 1, 1), expected_solar)
            rev = solar_to_lunar(expected_solar)
            self.assertEqual((rev.year, rev.month, rev.day, rev.is_leap_month), (yr, 1, 1, False))
            self.assertEqual(rev.short_label, "农历正月初一")

        # 2026 Mid-Autumn Festival: 2026-09-25 <-> Lunar 2026-08-15
        mid_autumn = date(2026, 9, 25)
        l_mid = solar_to_lunar(mid_autumn)
        self.assertEqual((l_mid.year, l_mid.month, l_mid.day), (2026, 8, 15))
        self.assertEqual(l_mid.short_label, "农历八月十五")
        self.assertEqual(lunar_to_solar(2026, 8, 15), mid_autumn)

        # Next Lunar New Year relative to 2026-11-21 should be 2027-02-06
        nxt = next_lunar_occurrence(1, 1, date(2026, 11, 21))
        self.assertEqual(nxt, date(2027, 2, 6))

    def test_countdown_anniversary_lifecycle_and_calendar_projection(self) -> None:
        from datetime import date, timedelta
        from trace_memory.domain.notes import beijing_today_date

        today = date.fromisoformat(beijing_today_date())

        # 1. Lunar yearly Spring Festival
        c1, r_cny = self.api.dispatch(
            "POST",
            "/api/countdowns/save",
            body={
                "space_id": "cd_space",
                "title": "春节团圆",
                "kind": "countdown",
                "calendar_type": "lunar",
                "lunar_year": today.year,
                "lunar_month": 1,
                "lunar_day": 1,
                "repeat_rule": "yearly",
                "icon": "🧧",
                "color_theme": "crimson",
                "pinned": True,
            },
        )
        self.assertEqual(c1, 200)
        self.assertEqual(r_cny["lunar_label"], "农历正月初一")

        # 2. Tomorrow countdown -> display_number == "1", badge_label == "还有"
        c2, r_tmr = self.api.dispatch(
            "POST",
            "/api/countdowns/save",
            body={
                "space_id": "cd_space",
                "title": "项目上线评审",
                "kind": "countdown",
                "calendar_type": "solar",
                "target_date": (today + timedelta(days=1)).isoformat(),
                "repeat_rule": "none",
                "pinned": True,
            },
        )
        self.assertEqual(c2, 200)
        self.assertEqual(r_tmr["display_number"], "1")
        self.assertEqual(r_tmr["badge_label"], "还有")

        # 3. Today countdown -> display_number == "今天"
        c3, r_today = self.api.dispatch(
            "POST",
            "/api/countdowns/save",
            body={
                "space_id": "cd_space",
                "title": "今日纪念时刻",
                "kind": "countdown",
                "calendar_type": "solar",
                "target_date": today.isoformat(),
                "repeat_rule": "none",
                "pinned": True,
            },
        )
        self.assertEqual(c3, 200)
        self.assertEqual(r_today["display_number"], "今天")
        self.assertEqual(r_today["badge_label"], "今天")

        # 4. Positive Anniversary (today - 78 days) -> display_number == "78", badge_label == "已经"
        c4, r_anniv = self.api.dispatch(
            "POST",
            "/api/countdowns/save",
            body={
                "space_id": "cd_space",
                "title": "恋爱纪念日",
                "kind": "anniversary",
                "calendar_type": "solar",
                "target_date": (today - timedelta(days=78)).isoformat(),
                "repeat_rule": "yearly",
                "icon": "💍",
                "color_theme": "pink",
            },
        )
        self.assertEqual(c4, 200)
        self.assertEqual(r_anniv["display_number"], "78")
        self.assertEqual(r_anniv["badge_label"], "已经")

        # 5. List & verify top_capsules <= 3 and space isolation
        c_list, listing = self.api.dispatch("GET", "/api/countdowns/list", {"space_id": ["cd_space"]})
        self.assertEqual(c_list, 200)
        self.assertEqual(listing["total"], 4)
        self.assertEqual(len(listing["top_capsules"]), 3)
        # Today pinned item should rank #1
        self.assertEqual(listing["items"][0]["display_number"], "今天")

        _, other_list = self.api.dispatch("GET", "/api/countdowns/list", {"space_id": ["other_space"]})
        self.assertEqual(other_list["total"], 0)

        # 6. Verify Calendar overview projects Lunar Spring Festival onto 2027-02-06!
        c_cal, cal_ov = self.api.dispatch(
            "GET",
            "/api/calendar/overview",
            {"space_id": ["cd_space"], "view": ["week"], "anchor_date": ["2027-02-06"]},
        )
        self.assertEqual(c_cal, 200)
        feb6 = next(d for d in cal_ov["days"] if d["date"] == "2027-02-06")
        cd_titles = [c["title"] for c in feb6["countdowns"]]
        self.assertIn("春节团圆", cd_titles)

        # 7. Delete one countdown
        c_del, r_del = self.api.dispatch(
            "POST",
            "/api/countdowns/delete",
            body={"space_id": "cd_space", "countdown_id": r_tmr["countdown_id"]},
        )
        self.assertEqual(c_del, 200)
        self.assertTrue(r_del["deleted"])

    def test_ios_widget_bundle_registers_countdown_widget(self) -> None:
        widget_src = (REPO_ROOT / "apple-app" / "Widgets" / "TraceTodoWidgets.swift").read_text(encoding="utf-8")
        self.assertIn("struct TraceCountdownWidget: Widget", widget_src)
        self.assertIn("TraceCountdownWidget()", widget_src)


if __name__ == "__main__":
    unittest.main()
