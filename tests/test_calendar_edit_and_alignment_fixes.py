from __future__ import annotations

from datetime import datetime, timedelta
import json
from pathlib import Path
import re
import subprocess
import tempfile
import unittest

from trace_memory.api.app import ApiDispatcher
from trace_memory.config import TraceMemoryConfig
from trace_memory.container import AppContainer
from trace_memory.domain.notes import BEIJING_TZ


REPO_ROOT = Path(__file__).resolve().parents[1]


class TestCalendarDurationEditAndAlignmentFixes(unittest.TestCase):
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

    def test_schedule_slot_supports_durations_beyond_4h_and_sets_exact_due_at(self) -> None:
        # 6 hours (360m): 09:00 -> 15:00
        c6, r6 = self.api.dispatch(
            "POST",
            "/api/calendar/schedule",
            body={
                "space_id": "dur_user",
                "title": "全天封闭开发上半场",
                "date": "2026-11-23",
                "time": "09:00",
                "is_all_day": False,
                "duration_minutes": 360,
                "color_theme": "lavender",
            },
        )
        self.assertEqual(c6, 200)
        p6 = r6["memory"]["payload"]
        self.assertEqual(p6["start_at"], "2026-11-23T09:00:00+08:00")
        self.assertEqual(p6["due_at"], "2026-11-23T15:00:00+08:00")
        self.assertEqual(p6["duration_minutes"], 360)

        # Custom 5 hours (300m) + 10 hours (600m overnight): 18:00 -> next day 04:00
        c10, r10 = self.api.dispatch(
            "POST",
            "/api/calendar/schedule",
            body={
                "space_id": "dur_user",
                "title": "通宵压测值守",
                "date": "2026-11-24",
                "time": "18:00",
                "is_all_day": False,
                "duration_minutes": 600,
            },
        )
        self.assertEqual(c10, 200)
        p10 = r10["memory"]["payload"]
        self.assertEqual(p10["start_at"], "2026-11-24T18:00:00+08:00")
        self.assertEqual(p10["due_at"], "2026-11-25T04:00:00+08:00")
        self.assertEqual(p10["duration_minutes"], 600)

        c5, r5 = self.api.dispatch(
            "POST",
            "/api/calendar/schedule",
            body={
                "space_id": "dur_user",
                "title": "自定义5小时深度冲刺",
                "date": "2026-11-26",
                "time": "13:00",
                "is_all_day": False,
                "duration_minutes": 300,
            },
        )
        self.assertEqual(c5, 200)
        self.assertEqual(r5["memory"]["payload"]["due_at"], "2026-11-26T18:00:00+08:00")

        c_ov, ov = self.api.dispatch(
            "GET",
            "/api/calendar/overview",
            {"space_id": ["dur_user"], "view": ["week"], "anchor_date": ["2026-11-23"]},
        )
        self.assertEqual(c_ov, 200)
        by_date = {d["date"]: d for d in ov["days"]}
        self.assertEqual(by_date["2026-11-23"]["timed_blocks"][0]["time_range_label"], "09:00 - 15:00")
        self.assertEqual(by_date["2026-11-24"]["timed_blocks"][0]["time_range_label"], "18:00 - 24:00")
        self.assertEqual(by_date["2026-11-25"]["timed_blocks"][0]["time_range_label"], "00:00 - 04:00")
        self.assertEqual(by_date["2026-11-26"]["timed_blocks"][0]["time_range_label"], "13:00 - 18:00")

    def test_edit_calendar_task_roundtrip_has_no_drift_or_explosion(self) -> None:
        _, created = self.api.dispatch(
            "POST",
            "/api/calendar/schedule",
            body={
                "space_id": "edit_user",
                "title": "周报撰写与核对",
                "date": "2026-11-23",
                "time": "10:00",
                "is_all_day": False,
                "duration_minutes": 120,
            },
        )
        mem_id = created["memory"]["memory_id"]
        ver1 = created["memory"]["version"]
        self.assertEqual(created["memory"]["payload"]["start_at"], "2026-11-23T10:00:00+08:00")
        self.assertEqual(created["memory"]["payload"]["due_at"], "2026-11-23T12:00:00+08:00")

        # 1. Edit title only keeping exact start/due timestamps
        c_e1, r_e1 = self.api.dispatch(
            "POST",
            "/api/tasks/save",
            body={
                "space_id": "edit_user",
                "target_memory_id": mem_id,
                "expected_version": ver1,
                "title": "周报撰写与核对（已修改标题）",
                "start_at": "2026-11-23T10:00:00+08:00",
                "due_at": "2026-11-23T12:00:00+08:00",
                "is_all_day": False,
                "duration_minutes": 120,
            },
        )
        self.assertEqual(c_e1, 200)
        p1 = r_e1["memory"]["payload"]
        self.assertEqual(p1["start_at"], "2026-11-23T10:00:00+08:00")
        self.assertEqual(p1["due_at"], "2026-11-23T12:00:00+08:00")
        self.assertEqual(p1["duration_minutes"], 120)
        self.assertFalse(p1["is_all_day"])

        # 2. Shift slot to 14:00 - 16:00 on same date
        c_e2, r_e2 = self.api.dispatch(
            "POST",
            "/api/tasks/save",
            body={
                "space_id": "edit_user",
                "target_memory_id": mem_id,
                "expected_version": r_e1["memory"]["version"],
                "title": "周报撰写与核对（已修改标题）",
                "start_at": "2026-11-23T14:00:00+08:00",
                "due_at": "2026-11-23T16:00:00+08:00",
                "is_all_day": False,
                "duration_minutes": 120,
            },
        )
        self.assertEqual(c_e2, 200)
        p2 = r_e2["memory"]["payload"]
        self.assertEqual(p2["start_at"], "2026-11-23T14:00:00+08:00")
        self.assertEqual(p2["due_at"], "2026-11-23T16:00:00+08:00")
        self.assertEqual(p2["duration_minutes"], 120)

        _, ov = self.api.dispatch(
            "GET",
            "/api/calendar/overview",
            {"space_id": ["edit_user"], "view": ["week"], "anchor_date": ["2026-11-23"]},
        )
        day_blocks = next(d for d in ov["days"] if d["date"] == "2026-11-23")["timed_blocks"]
        self.assertEqual(len(day_blocks), 1)
        self.assertEqual(day_blocks[0]["start_minute"], 840)
        self.assertEqual(day_blocks[0]["end_minute"], 960)
        self.assertEqual(day_blocks[0]["time_range_label"], "14:00 - 16:00")

    def test_save_task_card_normalizes_utc_and_repairs_due_not_after_start(self) -> None:
        # 1. UTC timestamps are normalized to Beijing +08:00 consistently
        c_tz, r_tz = self.api.dispatch(
            "POST",
            "/api/tasks/save",
            body={
                "space_id": "tz_user",
                "title": "跨时区写入校准",
                "start_at": "2026-11-23T02:00:00Z",
                "due_at": "2026-11-23T04:00:00+00:00",
                "is_all_day": False,
            },
        )
        self.assertEqual(c_tz, 200)
        p_tz = r_tz["memory"]["payload"]
        self.assertEqual(p_tz["start_at"], "2026-11-23T10:00:00+08:00")
        self.assertEqual(p_tz["due_at"], "2026-11-23T12:00:00+08:00")
        self.assertEqual(p_tz["duration_minutes"], 120)

        # 2. Legacy/broken submission where due_at == start_at repairs due_at using duration_minutes
        c_rep, r_rep = self.api.dispatch(
            "POST",
            "/api/tasks/save",
            body={
                "space_id": "tz_user",
                "title": "起止相同自动修复",
                "start_at": "2026-11-23T15:00:00+08:00",
                "due_at": "2026-11-23T15:00:00+08:00",
                "is_all_day": False,
                "duration_minutes": 90,
            },
        )
        self.assertEqual(c_rep, 200)
        p_rep = r_rep["memory"]["payload"]
        self.assertEqual(p_rep["start_at"], "2026-11-23T15:00:00+08:00")
        self.assertEqual(p_rep["due_at"], "2026-11-23T16:30:00+08:00")
        self.assertEqual(p_rep["duration_minutes"], 90)

    def test_auto_rollover_preserves_start_at_and_due_at_span(self) -> None:
        _, created = self.api.dispatch(
            "POST",
            "/api/tasks/save",
            body={
                "space_id": "roll_user",
                "title": "昨日未完成研发任务",
                "start_at": "2026-11-20T14:00:00+08:00",
                "due_at": "2026-11-20T16:30:00+08:00",
                "is_all_day": False,
                "auto_rollover": True,
            },
        )
        mem_id = created["memory"]["memory_id"]

        now_bj = datetime(2026, 11, 23, 9, 0, 0, tzinfo=BEIJING_TZ)
        rolled = self.container.task_matrix_service.apply_auto_rollover("roll_user", now=now_bj)
        self.assertEqual(len(rolled), 1)
        p = rolled[0]["payload"]
        self.assertEqual(p["start_at"], "2026-11-23T14:00:00+08:00")
        self.assertEqual(p["due_at"], "2026-11-23T16:30:00+08:00")
        self.assertEqual(rolled[0]["memory_id"], mem_id)

        # Overnight timed rollover: 2026-11-20 23:00 .. 2026-11-21 01:00 -> rolled on 2026-11-23
        _, ov_task = self.api.dispatch(
            "POST",
            "/api/tasks/save",
            body={
                "space_id": "roll_user2",
                "title": "跨夜任务顺延",
                "start_at": "2026-11-20T23:00:00+08:00",
                "due_at": "2026-11-21T01:00:00+08:00",
                "is_all_day": False,
                "auto_rollover": True,
            },
        )
        rolled2 = self.container.task_matrix_service.apply_auto_rollover("roll_user2", now=now_bj)
        self.assertEqual(len(rolled2), 1)
        p2 = rolled2[0]["payload"]
        self.assertEqual(p2["start_at"], "2026-11-22T23:00:00+08:00")
        self.assertEqual(p2["due_at"], "2026-11-23T01:00:00+08:00")
        self.assertEqual(rolled2[0]["memory_id"], ov_task["memory"]["memory_id"])

    def test_dashboard_html_calendar_fixes_markers_and_js_helpers(self) -> None:
        html = (REPO_ROOT / "src" / "trace_memory" / "api" / "dashboard.html").read_text(encoding="utf-8")

        # 1. CSS & Duration Controls
        for expected in (
            ".pomo-preset-btn.active",
            ".cal-dur-custom-wrap.active",
            'data-dur="360"',
            'data-dur="480"',
            "calSlotCustomDurInput",
            "calSlotCustomDurUnit",
            "calSlotRangePreview",
            "onCalSlotCustomDurationInput",
            "resolveCalSlotDurationMinutes",
            # 2. Task edit time hint & consistency
            "tsTimeDurationHint",
            "onTaskSheetStartTimeChange",
            "onTaskSheetEndTimeChange",
            "parseIsoYmdHhmm",
            "addMinutesToYmdHhmm",
            # 3. Unified week scroll container & sticky aligned columns
            "position: sticky;",
            "grid-template-columns: 56px repeat(7, minmax(0, 1fr));",
            ".cal-week-hdr-row > *",
            ".cal-week-allday-row > *",
            ".cal-week-body-grid > *",
        ):
            self.assertIn(expected, html, f"Missing expected marker: {expected}")

        # Verify calWeekHeaderRow, calWeekAllDayRow, and calWeekBodyGrid all sit inside calWeekScrollBox
        m_box = re.search(
            r'<div id="calWeekContainer"[^>]*>.*?<div class="cal-week-vscroll" id="calWeekScrollBox">(.*?)</div>\s*</div>\s*</div>\s*<!-- Subview 2',
            html,
            re.DOTALL,
        )
        self.assertIsNotNone(m_box, "Expected unified calWeekScrollBox markup")
        inner_box = m_box.group(1)
        self.assertIn('id="calWeekHeaderRow"', inner_box)
        self.assertIn('id="calWeekAllDayRow"', inner_box)
        self.assertIn('id="calWeekBodyGrid"', inner_box)

        # Execute the extracted JS time helpers in node to verify exact Beijing calculations
        scripts = "\n".join(re.findall(r"<script[^>]*>(.*?)</script>", html, re.S))
        helper_snippets = []
        for fn_name in (
            "hhmmToMinutes",
            "parseIsoYmdHhmm",
            "addMinutesToYmdHhmm",
            "formatMinsHuman",
            "buildIsoDueAt",
        ):
            m_fn = re.search(rf"function {fn_name}\([^)]*\)\s*\{{", scripts)
            self.assertIsNotNone(m_fn, f"Missing JS function {fn_name}")
            idx = m_fn.start()
            brace_idx = scripts.index("{", idx)
            depth = 0
            end_idx = brace_idx
            for i in range(brace_idx, len(scripts)):
                if scripts[i] == "{":
                    depth += 1
                elif scripts[i] == "}":
                    depth -= 1
                    if depth == 0:
                        end_idx = i + 1
                        break
            helper_snippets.append(scripts[idx:end_idx])

        js_probe = "\n".join(helper_snippets) + """
        const res = {
          utcParsed: parseIsoYmdHhmm('2026-11-23T02:00:00Z'),
          bjParsed: parseIsoYmdHhmm('2026-11-23T21:30:00+08:00'),
          added6h: addMinutesToYmdHhmm('2026-11-23', '09:00', 360),
          addedOvernight: addMinutesToYmdHhmm('2026-11-23', '22:00', 180),
          built: buildIsoDueAt('2026-11-23', false, '14:30'),
          builtAllDay: buildIsoDueAt('2026-11-23', true, ''),
        };
        console.log(JSON.stringify(res));
        """
        proc = subprocess.run(["node", "-e", js_probe], capture_output=True, text=True, check=True)
        out = json.loads(proc.stdout.strip())
        self.assertEqual(out["utcParsed"]["ymd"], "2026-11-23")
        self.assertEqual(out["utcParsed"]["hhmm"], "10:00")
        self.assertEqual(out["bjParsed"]["hhmm"], "21:30")
        self.assertEqual(out["added6h"], {"ymd": "2026-11-23", "hhmm": "15:00", "dayOffset": 0})
        self.assertEqual(out["addedOvernight"], {"ymd": "2026-11-24", "hhmm": "01:00", "dayOffset": 1})
        self.assertEqual(out["built"], "2026-11-23T14:30:00+08:00")
        self.assertEqual(out["builtAllDay"], "2026-11-23T23:59:59+08:00")


if __name__ == "__main__":
    unittest.main()
