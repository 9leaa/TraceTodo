"""Tests for Calendar Lunar dates, 24 Solar Terms, Festivals, and Monthly Holiday Sync."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
import json
from pathlib import Path
import tempfile
import unittest

from trace_memory.api.app import ApiDispatcher
from trace_memory.config import TraceMemoryConfig
from trace_memory.container import AppContainer
from trace_memory.services.lunar_calendar import (
    HolidayScheduleManager,
    describe_calendar_day,
    get_festivals,
    get_solar_term,
    solar_to_lunar,
)


BJ_TZ = timezone(timedelta(hours=8))


class LunarSolarTermsFestivalsTest(unittest.TestCase):
    def test_lunar_first_day_shows_month_and_chuxi_dynamic(self) -> None:
        # 2025 has a 29-day 12th lunar month: 2025-01-28 is 腊月廿九 除夕, 2025-01-29 is 正月初一 春节
        eve_2025 = describe_calendar_day(date(2025, 1, 28))
        self.assertEqual(eve_2025["lunar_short"], "腊月廿九")
        self.assertIn("除夕", eve_2025["festivals"])
        self.assertEqual(eve_2025["calendar_sub_label"], "除夕")
        self.assertEqual(eve_2025["calendar_sub_kind"], "festival")

        cny_2025 = describe_calendar_day(date(2025, 1, 29))
        self.assertEqual(cny_2025["lunar_month_cn"], "正月")
        self.assertEqual(cny_2025["lunar_day_cn"], "初一")
        self.assertIn("春节", cny_2025["festivals"])
        self.assertEqual(cny_2025["calendar_sub_label"], "春节")

        # 2024 had a 30-day 12th lunar month: 2024-02-09 was 腊月三十 除夕
        eve_2024 = describe_calendar_day(date(2024, 2, 9))
        self.assertEqual(eve_2024["lunar_short"], "腊月三十")
        self.assertIn("除夕", eve_2024["festivals"])

        # Ordinary 1st lunar day shows lunar month name (e.g. 2025-06-25 = 六月初一, 2025-07-25 = 闰六月初一)
        reg_first = describe_calendar_day(date(2025, 6, 25))
        self.assertEqual(reg_first["calendar_sub_label"], "六月")
        self.assertEqual(reg_first["calendar_sub_kind"], "lunar_month")

        leap_first = describe_calendar_day(date(2025, 7, 25))
        self.assertEqual(leap_first["calendar_sub_label"], "闰六月")
        self.assertEqual(leap_first["calendar_sub_kind"], "lunar_month")

    def test_24_solar_terms_astronomical_accuracy(self) -> None:
        self.assertEqual(get_solar_term(date(2025, 4, 4)), "清明")
        self.assertEqual(get_solar_term(date(2026, 4, 5)), "清明")
        self.assertEqual(get_solar_term(date(2026, 1, 20)), "大寒")
        self.assertEqual(get_solar_term(date(2026, 2, 4)), "立春")
        self.assertEqual(get_solar_term(date(2026, 2, 18)), "雨水")
        self.assertEqual(get_solar_term(date(2026, 11, 22)), "小雪")
        self.assertEqual(get_solar_term(date(2026, 12, 22)), "冬至")

        xiaoxue_info = describe_calendar_day(date(2026, 11, 22))
        self.assertEqual(xiaoxue_info["solar_term"], "小雪")
        self.assertEqual(xiaoxue_info["calendar_sub_label"], "小雪")
        self.assertEqual(xiaoxue_info["calendar_sub_kind"], "solar_term")

    def test_festivals_include_international_weekday_and_collision(self) -> None:
        # 2020-10-01: National Day coincides with Mid-Autumn Festival -> compact label "国庆·中秋"
        col = describe_calendar_day(date(2020, 10, 1))
        self.assertEqual(col["festivals"][:2], ["国庆节", "中秋节"])
        self.assertEqual(col["calendar_sub_label"], "国庆·中秋")
        self.assertEqual(col["calendar_sub_kind"], "festival")

        # User opted in common/international festivals
        val = describe_calendar_day(date(2026, 2, 14))
        self.assertIn("情人节", val["festivals"])
        self.assertEqual(val["calendar_sub_label"], "情人节")
        self.assertEqual(val["calendar_sub_kind"], "festival_minor")
        self.assertEqual(val["holiday_status"], "work")  # 2026-02-14 is Spring Festival make-up workday

        xmas = describe_calendar_day(date(2026, 12, 25))
        self.assertIn("圣诞节", xmas["festivals"])

        # Dynamic weekday festivals in 2026: Mother's Day (May 10), Father's Day (June 21), Thanksgiving (Nov 26)
        _, m_minor = get_festivals(date(2026, 5, 10))
        self.assertIn("母亲节", m_minor)
        _, f_minor = get_festivals(date(2026, 6, 21))
        self.assertIn("父亲节", f_minor)
        _, t_minor = get_festivals(date(2026, 11, 26))
        self.assertIn("感恩节", t_minor)


class HolidaySyncAndCalendarAPITest(unittest.TestCase):
    def test_builtin_2026_rest_and_workdays(self) -> None:
        mgr = HolidayScheduleManager()
        self.assertEqual(mgr.get_holiday_info(date(2026, 2, 14)), ("work", "春节"))
        self.assertEqual(mgr.get_holiday_info(date(2026, 2, 16)), ("rest", "春节"))
        self.assertEqual(mgr.get_holiday_info(date(2026, 2, 28)), ("work", "春节"))
        self.assertEqual(mgr.get_holiday_info(date(2026, 9, 25)), ("rest", "中秋节"))
        self.assertEqual(mgr.get_holiday_info(date(2026, 10, 10)), ("work", "国庆节"))
        self.assertEqual(mgr.get_holiday_info(date(2026, 11, 25)), ("", ""))

    def test_monthly_sync_mirror_failover_timor_and_override(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            calls: list[str] = []

            def mock_fetch(url: str, timeout: float = 8.0) -> tuple[int, str]:
                calls.append(url)
                if "/2026.json" in url and "cdn.jsdelivr.net" in url:
                    raise TimeoutError("simulated timeout on primary cdn")
                if "/2026.json" in url and "fastly.jsdelivr.net" in url:
                    return 200, json.dumps({
                        "days": [
                            {"date": "2026-01-01", "name": "元旦", "isOffDay": True},
                            {"date": "2026-10-10", "name": "国庆节", "isOffDay": False},
                        ]
                    })
                if "/2027.json" in url:
                    return 404, ""
                if "timor.tech/api/holiday/year/2027" in url:
                    return 200, json.dumps({
                        "code": 0,
                        "holiday": {
                            "01-01": {"holiday": True, "name": "元旦", "date": "2027-01-01"},
                            "02-06": {"holiday": False, "name": "春节前补班", "date": "2027-02-06"},
                        },
                    })
                return 404, ""

            mgr = HolidayScheduleManager(data_dir=data_dir, http_fetcher=mock_fetch)
            nov_now = datetime(2026, 11, 25, 10, 0, tzinfo=BJ_TZ)
            rep1 = mgr.maybe_monthly_sync(now=nov_now)
            self.assertFalse(rep1["skipped"])
            self.assertTrue(rep1["updated"])
            self.assertEqual(rep1["synced_years"], [2026, 2027])
            self.assertTrue((data_dir / "holidays_cache.json").is_file())

            # Same month second call must skip network IO completely
            prev_call_count = len(calls)
            rep2 = mgr.maybe_monthly_sync(now=datetime(2026, 11, 28, 9, 0, tzinfo=BJ_TZ))
            self.assertTrue(rep2["skipped"])
            self.assertEqual(len(calls), prev_call_count)

            # Next month (Dec 1) automatically syncs again
            rep3 = mgr.maybe_monthly_sync(now=datetime(2026, 12, 1, 0, 2, tzinfo=BJ_TZ))
            self.assertFalse(rep3["skipped"])
            self.assertGreater(len(calls), prev_call_count)

            # Reload from disk cache and verify timor.tech parsed 2027 entries
            mgr_reloaded = HolidayScheduleManager(data_dir=data_dir)
            self.assertEqual(mgr_reloaded.get_holiday_info(date(2027, 1, 1)), ("rest", "元旦"))
            self.assertEqual(mgr_reloaded.get_holiday_info(date(2027, 2, 6)), ("work", "春节前补班"))

            # Manual holidays_override.json has highest precedence
            (data_dir / "holidays_override.json").write_text(
                json.dumps({"2027-02-06": {"status": "rest", "name": "临时放假"}}),
                encoding="utf-8",
            )
            self.assertEqual(mgr_reloaded.get_holiday_info(date(2027, 2, 6)), ("rest", "临时放假"))

    def test_calendar_api_endpoints_and_scheduler_integration(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cfg = TraceMemoryConfig(
                default_space="default",
                data_dir=root / "data",
                knowledge_dir=root / "knowledge",
                auth_required=False,
            )
            container = AppContainer.build(cfg)
            try:
                container.holiday_manager._http_fetcher = lambda url, timeout=8.0: (
                    (
                        200,
                        json.dumps({
                            "days": [
                                {"date": "2026-02-17", "name": "春节", "isOffDay": True},
                                {"date": "2026-10-01", "name": "国庆节", "isOffDay": True},
                            ]
                        }),
                    )
                    if "/2026.json" in url
                    else (404, "")
                )

                api = ApiDispatcher(container)
                code_m, month_data = api.dispatch(
                    "GET",
                    "/api/calendar/overview",
                    {"space_id": ["default"], "view": ["month"], "anchor_date": ["2026-10-01"]},
                )
                self.assertEqual(code_m, 200)
                self.assertIn("today_lunar_full", month_data)
                self.assertIn("holiday_sync", month_data)

                by_date = {d["date"]: d for d in month_data["days"]}
                oct1 = by_date["2026-10-01"]
                self.assertEqual(oct1["calendar_sub_label"], "国庆节")
                self.assertEqual(oct1["calendar_sub_kind"], "festival")
                self.assertEqual(oct1["holiday_status"], "rest")

                oct10 = by_date["2026-10-10"]
                self.assertEqual(oct10["holiday_status"], "work")
                self.assertEqual(oct10["holiday_name"], "国庆节")

                code_st, status_data = api.dispatch("GET", "/api/calendar/holidays/status", {"space_id": ["default"]})
                self.assertEqual(code_st, 200)
                self.assertIn("builtin_years", status_data)

                code_sy, sync_data = api.dispatch("POST", "/api/calendar/holidays/sync", {"space_id": ["default"]}, {})
                self.assertEqual(code_sy, 200)
                self.assertIn("synced_years", sync_data)

                code_w, week_data = api.dispatch(
                    "GET",
                    "/api/calendar/overview",
                    {"space_id": ["default"], "view": ["week"], "anchor_date": ["2026-02-16"]},
                )
                self.assertEqual(code_w, 200)
                week_by_date = {d["date"]: d for d in week_data["days"]}
                self.assertEqual(week_by_date["2026-02-16"]["calendar_sub_label"], "除夕")
                self.assertEqual(week_by_date["2026-02-17"]["calendar_sub_label"], "春节")
                self.assertEqual(week_by_date["2026-02-17"]["holiday_status"], "rest")

                # Verify scheduler integration runs monthly holiday sync cleanly when enabled
                sched_report = container.daily_scheduler.run_once(
                    now=datetime(2027, 1, 1, 0, 1, tzinfo=BJ_TZ),
                    include_holiday_sync=True,
                )
                self.assertIn("holiday_sync", sched_report)
                self.assertFalse(sched_report["holiday_sync"]["skipped"])
            finally:
                container.close()
