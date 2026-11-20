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


if __name__ == "__main__":
    unittest.main()
