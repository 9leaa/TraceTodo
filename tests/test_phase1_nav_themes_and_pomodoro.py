from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from trace_memory.api.app import ApiDispatcher
from trace_memory.config import TraceMemoryConfig
from trace_memory.container import AppContainer


REPO_ROOT = Path(__file__).resolve().parents[1]


class TestPhase1NavThemesAndPreferences(unittest.TestCase):
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

    def test_default_vision_model_is_qwen_vl_plus(self) -> None:
        self.assertEqual(self.cfg.vision_model, "qwen-vl-plus")
        code, health = self.api.dispatch("GET", "/api/health")
        self.assertEqual(code, 200)
        self.assertEqual(health["vision_model"], "qwen-vl-plus")

    def test_preferences_theme_and_feature_toggles_lifecycle(self) -> None:
        code, init_prefs = self.api.dispatch("GET", "/api/preferences", {"space_id": ["alice"]})
        self.assertEqual(code, 200)
        self.assertEqual(init_prefs["theme_id"], "classic")
        self.assertTrue(init_prefs["enabled_modules"]["calendar"])
        self.assertTrue(init_prefs["enabled_modules"]["focus"])
        self.assertTrue(init_prefs["enabled_modules"]["notes"])
        self.assertTrue(init_prefs["enabled_modules"]["journal"])
        self.assertTrue(init_prefs["enabled_modules"]["countdown"])
        self.assertTrue(init_prefs["enabled_modules"]["supervision"])
        self.assertGreaterEqual(len(init_prefs["themes_catalog"]), 6)

        _, sync_before = self.api.dispatch("GET", "/api/sync/state", {"space_id": ["alice"]})

        # Switch to West Lake theme and hide journal & supervision modules
        code_up, updated = self.api.dispatch(
            "POST",
            "/api/preferences",
            body={
                "space_id": "alice",
                "theme_id": "westlake",
                "enabled_modules": {"journal": False, "supervision": False},
                "pomodoro_minutes": 45,
            },
        )
        self.assertEqual(code_up, 200)
        self.assertEqual(updated["theme_id"], "westlake")
        self.assertFalse(updated["enabled_modules"]["journal"])
        self.assertFalse(updated["enabled_modules"]["supervision"])
        self.assertTrue(updated["enabled_modules"]["focus"])
        self.assertEqual(updated["pomodoro_minutes"], 45)

        # Shorthand single-module toggle
        code_sh, updated2 = self.api.dispatch(
            "POST",
            "/api/preferences",
            body={"space_id": "alice", "module_key": "countdown", "enabled": False},
        )
        self.assertEqual(code_sh, 200)
        self.assertFalse(updated2["enabled_modules"]["countdown"])
        self.assertEqual(updated2["theme_id"], "westlake")

        # Sync version bumped for cross-device refresh
        _, sync_after = self.api.dispatch("GET", "/api/sync/state", {"space_id": ["alice"]})
        self.assertGreater(sync_after["sync_version"], sync_before["sync_version"])

        # Space isolation: bob space still uses classic defaults
        _, bob_prefs = self.api.dispatch("GET", "/api/preferences", {"space_id": ["bob"]})
        self.assertEqual(bob_prefs["theme_id"], "classic")
        self.assertTrue(bob_prefs["enabled_modules"]["supervision"])

        # Invalid theme rejected with 400 ValidationError
        code_bad, _ = self.api.dispatch(
            "POST",
            "/api/preferences",
            body={"space_id": "alice", "theme_id": "non_existent_theme"},
        )
        self.assertEqual(code_bad, 400)

    def test_notes_list_and_bm25_search_for_suiji_tab(self) -> None:
        self.container.ingest_service.record_note(
            space_id="default",
            content="西湖边散步想到了向量索引冷热分层方案",
            source_type="web",
        )
        code, listed = self.api.dispatch("GET", "/api/notes/list")
        self.assertEqual(code, 200)
        self.assertEqual(listed["count"], 1)

        code_s, searched = self.api.dispatch("GET", "/api/notes/list", {"q": ["西湖冷热分层"]})
        self.assertEqual(code_s, 200)
        self.assertGreaterEqual(searched["count"], 1)

    def test_five_tab_converged_navigation_and_theme_markers(self) -> None:
        html = (REPO_ROOT / "src" / "trace_memory" / "api" / "dashboard.html").read_text(encoding="utf-8")
        for primary_tab in ("tasks", "calendar", "focus", "notes", "memories"):
            self.assertIn(f'data-tab="{primary_tab}"', html)
            self.assertIn(f'id="panel-{primary_tab}"', html)
        for theme_id in ("westlake", "autumn", "golden_gate", "snow_peak", "deep_forest"):
            self.assertIn(f'[data-theme="{theme_id}"]', html)
        self.assertIn('id="todoViewSegBar"', html)
        self.assertIn('id="prefsModal"', html)

        swift_app = (REPO_ROOT / "apple-app" / "Sources" / "TraceTodo" / "TraceTodoApp.swift").read_text(
            encoding="utf-8"
        )
        for label in ('case tasks = "待办"', 'case calendar = "日历"', 'case focus = "专注"', 'case notes = "随记"', 'case memories = "记忆"'):
            self.assertIn(label, swift_app)

    def test_pomodoro_and_stopwatch_focus_center_4grid_and_timeline(self) -> None:
        # Create a task in alice's space
        _, saved = self.api.dispatch(
            "POST",
            "/api/tasks/save",
            body={
                "space_id": "alice",
                "title": "重构混合向量索引模块",
                "importance": 5,
                "urgency": 4,
                "focus_minutes": 25,
            },
        )
        task_id = saved["memory"]["memory_id"]
        task_ver = saved["memory"]["version"]

        # 1) Record a completed 25m Pomodoro bound to the task today
        code_p, res_p = self.api.dispatch(
            "POST",
            "/api/focus/record",
            body={
                "space_id": "alice",
                "mode": "pomodoro",
                "status": "completed",
                "planned_minutes": 25,
                "duration_seconds": 1500,
                "task_memory_id": task_id,
                "expected_version": task_ver,
            },
        )
        self.assertEqual(code_p, 200)
        self.assertEqual(res_p["task"]["payload"]["focus_sessions_completed"], 1)
        self.assertEqual(res_p["stats"]["today_pomodoros"], 1)
        self.assertEqual(res_p["stats"]["today_seconds"], 1500)

        # 2) Record a 20m free-focus Stopwatch session today (no task_memory_id)
        code_sw, res_sw = self.api.dispatch(
            "POST",
            "/api/focus/record",
            body={
                "space_id": "alice",
                "mode": "stopwatch",
                "status": "completed",
                "duration_seconds": 1200,
                "task_title": "阅读论文自由专注",
            },
        )
        self.assertEqual(code_sw, 200)
        self.assertEqual(res_sw["session"]["task_title"], "阅读论文自由专注")
        # Stopwatch adds to duration, while today_pomodoros counts completed pomodoros
        self.assertEqual(res_sw["stats"]["today_pomodoros"], 1)
        self.assertEqual(res_sw["stats"]["today_seconds"], 2700)

        # 3) Record a historical Pomodoro from an earlier Beijing date
        code_old, _ = self.api.dispatch(
            "POST",
            "/api/focus/record",
            body={
                "space_id": "alice",
                "mode": "pomodoro",
                "status": "completed",
                "planned_minutes": 25,
                "duration_seconds": 1500,
                "ended_at": "2026-10-01T15:00:00+08:00",
            },
        )
        self.assertEqual(code_old, 200)

        # 4) Record an aborted Pomodoro (should not inflate completed stats, but records interruption)
        code_ab, res_ab = self.api.dispatch(
            "POST",
            "/api/focus/record",
            body={
                "space_id": "alice",
                "mode": "pomodoro",
                "status": "aborted",
                "planned_minutes": 25,
                "duration_seconds": 300,
                "task_memory_id": task_id,
                "abort_reason": "临时线上告警",
            },
        )
        self.assertEqual(code_ab, 200)
        self.assertEqual(res_ab["stats"]["aborted_sessions"], 1)

        # Verify 4-grid overview & timeline ordering
        code_ov, ov = self.api.dispatch("GET", "/api/focus/overview", {"space_id": ["alice"]})
        self.assertEqual(code_ov, 200)
        st = ov["stats"]
        self.assertEqual(st["today_pomodoros"], 1)
        self.assertEqual(st["total_pomodoros"], 2)
        self.assertEqual(st["today_seconds"], 2700)
        self.assertEqual(st["total_seconds"], 4200)
        self.assertEqual(len(ov["sessions"]), 4)

        # Space isolation: bob cannot attach alice's task & sees empty stats
        code_forbid, _ = self.api.dispatch(
            "POST",
            "/api/focus/record",
            body={"space_id": "bob", "task_memory_id": task_id, "mode": "pomodoro"},
        )
        self.assertEqual(code_forbid, 403)
        _, bob_ov = self.api.dispatch("GET", "/api/focus/overview", {"space_id": ["bob"]})
        self.assertEqual(bob_ov["stats"]["total_seconds"], 0)

        # Delete aborted session cleanly
        aborted_sid = res_ab["session"]["session_id"]
        code_del, del_res = self.api.dispatch(
            "POST",
            "/api/focus/delete",
            body={"space_id": "alice", "session_id": aborted_sid},
        )
        self.assertEqual(code_del, 200)
        self.assertTrue(del_res["deleted"])

    def test_focus_center_ui_markers_present_in_web_and_swift(self) -> None:
        html = (REPO_ROOT / "src" / "trace_memory" / "api" / "dashboard.html").read_text(encoding="utf-8")
        self.assertIn('id="focusModeSegBar"', html)
        self.assertIn('id="focusStatsGrid"', html)
        self.assertIn('id="focusTimelineList"', html)
        self.assertIn("function startUnifiedFocus(", html)

        swift_focus = (REPO_ROOT / "apple-app" / "Sources" / "TraceTodo" / "FocusLockView.swift").read_text(
            encoding="utf-8"
        )
        self.assertIn("enum FocusTimerMode", swift_focus)
        self.assertIn("struct FocusCenterView: View", swift_focus)
        self.assertIn("今日番茄", swift_focus)
        self.assertIn("专注历史时间轴", swift_focus)


if __name__ == "__main__":
    unittest.main()
