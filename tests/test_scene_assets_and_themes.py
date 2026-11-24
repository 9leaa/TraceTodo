from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request

from trace_memory.api.app import ApiDispatcher
from trace_memory.config import TraceMemoryConfig
from trace_memory.container import AppContainer


REPO_ROOT = Path(__file__).resolve().parents[1]

_SERVER_CODE = """
import sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from trace_memory.api.app import run_web_server
from trace_memory.config import TraceMemoryConfig
from trace_memory.container import AppContainer
root = Path(sys.argv[2])
cfg = TraceMemoryConfig(default_space="default", data_dir=root / "data", knowledge_dir=root / "knowledge", auth_required=False, rate_limit_per_minute=10000)
container = AppContainer.build(cfg)
run_web_server(container, host="127.0.0.1", port=int(sys.argv[3]))
"""


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class TestSceneAssetsAndThemes(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.cfg = TraceMemoryConfig(
            default_space="default",
            data_dir=self.root / "data",
            knowledge_dir=self.root / "knowledge",
            auth_required=False,
            rate_limit_per_minute=200,
        )
        self.container = AppContainer.build(self.cfg)
        self.api = ApiDispatcher(self.container)

    def tearDown(self) -> None:
        self.container.close()
        self._tmp.cleanup()

    def test_new_themes_and_scene_extra_sync_via_preferences(self) -> None:
        code, res = self.api.dispatch("GET", "/api/preferences", {"space_id": ["scene_sp"]})
        self.assertEqual(code, 200)
        ids = {t["id"] for t in res["themes_catalog"]}
        for new_id in (
            "nebula",
            "cyber",
            "polar",
            "sakura",
            "warm_ivory",
            "celadon_mist",
            "velvet_noir",
            "obsidian_gold",
            "tide_blue",
        ):
            self.assertIn(new_id, ids)

        for paired_theme, paired_scene in (
            ("nebula", "starfield"),
            ("warm_ivory", "portrait_m_sunny"),
            ("celadon_mist", "portrait_f_hanfu"),
            ("velvet_noir", "portrait_f_cool"),
            ("obsidian_gold", "portrait_m"),
            ("tide_blue", "portrait_m_sport"),
            ("autumn", "portrait_f_qipao"),
            ("snow_peak", "portrait_f_snow"),
            ("nebula", "portrait_f_ballet"),
            ("cyber", "portrait_f_cyber"),
        ):
            code, res = self.api.dispatch(
                "POST",
                "/api/preferences",
                body={
                    "space_id": "scene_sp",
                    "theme_id": paired_theme,
                    "extra": {"scene_id": paired_scene, "scene_intensity": 3, "scene_motion": 1},
                },
            )
            self.assertEqual(code, 200)
            self.assertEqual(res["theme_id"], paired_theme)
            self.assertEqual(res["extra"]["scene_id"], paired_scene)
            self.assertEqual(res["extra"]["scene_intensity"], 3)

        code, res = self.api.dispatch(
            "POST", "/api/preferences", body={"space_id": "scene_sp", "theme_id": "not_a_theme"}
        )
        self.assertEqual(code, 400)

    def test_concurrent_preference_patches_do_not_lose_fields(self) -> None:
        space = "race_sp"
        for round_idx in range(12):
            barrier = threading.Barrier(2)

            def post_theme() -> tuple:
                barrier.wait()
                return self.api.dispatch(
                    "POST", "/api/preferences", body={"space_id": space, "theme_id": "nebula"}
                )

            def post_scene() -> tuple:
                barrier.wait()
                return self.api.dispatch(
                    "POST",
                    "/api/preferences",
                    body={"space_id": space, "extra": {"scene_id": "starfield", "scene_intensity": 2}},
                )

            with ThreadPoolExecutor(max_workers=2) as pool:
                fut_theme = pool.submit(post_theme)
                fut_scene = pool.submit(post_scene)
                code_theme, _ = fut_theme.result()
                code_scene, _ = fut_scene.result()
            self.assertEqual(code_theme, 200, f"round {round_idx}")
            self.assertEqual(code_scene, 200, f"round {round_idx}")

            code, res = self.api.dispatch("GET", "/api/preferences", {"space_id": [space]})
            self.assertEqual(code, 200)
            self.assertEqual(res["theme_id"], "nebula", f"theme lost in round {round_idx}")
            self.assertEqual(res["extra"]["scene_id"], "starfield", f"scene lost in round {round_idx}")
            self.assertEqual(res["extra"]["scene_intensity"], 2)

    def test_zero_dep_server_serves_scene_images_and_blocks_traversal(self) -> None:
        port = _free_port()
        env = dict(os.environ)
        proc = subprocess.Popen(
            [sys.executable, "-c", _SERVER_CODE, str(REPO_ROOT / "src"), str(self.root), str(port)],
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        self.addCleanup(proc.terminate)
        deadline = time.time() + 15
        ready = False
        while time.time() < deadline:
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                    ready = True
                    break
            except OSError:
                if proc.poll() is not None:
                    self.fail("test server exited early")
                time.sleep(0.15)
        self.assertTrue(ready, "test server did not start")

        for scene_file in (
            "starfield.jpg",
            "portrait_m.jpg",
            "portrait_m_sunny.jpg",
            "portrait_m_ancient.jpg",
            "portrait_m_sport.jpg",
            "portrait_f.jpg",
            "portrait_f_cool.jpg",
            "portrait_f_hanfu.jpg",
            "portrait_f_vitality.jpg",
            "portrait_f_qipao.jpg",
            "portrait_f_snow.jpg",
            "portrait_f_ballet.jpg",
            "portrait_f_cyber.jpg",
        ):
            with self.subTest(scene=scene_file):
                with urllib.request.urlopen(f"http://127.0.0.1:{port}/scenes/{scene_file}", timeout=5) as r:
                    self.assertEqual(r.status, 200)
                    self.assertEqual(r.headers["Content-Type"], "image/jpeg")
                    body = r.read()
                    self.assertTrue(body.startswith(b"\xff\xd8\xff"))
                    self.assertGreater(len(body), 10000)

        for bad in ("/scenes/nope.jpg", "/scenes/..%2fapp.py", "/scenes/starfield.jpg/../../app.py"):
            with self.subTest(path=bad):
                try:
                    urllib.request.urlopen(f"http://127.0.0.1:{port}{bad}", timeout=5)
                    self.fail("expected 404")
                except urllib.error.HTTPError as e:
                    self.assertEqual(e.code, 404)

    def test_dashboard_html_exposes_scene_and_immersive_ui(self) -> None:
        html = (REPO_ROOT / "src" / "trace_memory" / "api" / "dashboard.html").read_text(encoding="utf-8")
        for marker in (
            'id="ambientSceneLayer"',
            'id="ambientSceneCanvas"',
            'id="focusSceneImg"',
            'id="focusSceneDock"',
            'id="focusSceneStrip"',
            'id="fsDimRange"',
            'id="fsBlurRange"',
            'id="fsDensityRange"',
            'id="fsSpeedRange"',
            'id="focusSoundBtn"',
            'id="sceneGalleryGrid"',
            'id="sceneIntensityBar"',
            "SCENE_CATALOG",
            "applyFocusSceneDOM",
            "startAmbience",
            "stopAmbience",
            "toggleSoundMuted",
            '[data-theme="nebula"]',
            '[data-theme="cyber"]',
            '[data-theme="polar"]',
            '[data-theme="sakura"]',
            '[data-theme="warm_ivory"]',
            '[data-theme="celadon_mist"]',
            '[data-theme="velvet_noir"]',
            '[data-theme="obsidian_gold"]',
            'id="sceneCategoryBar"',
            'id="sceneSyncThemeCheck"',
            "portrait_m_sunny",
            "portrait_m_ancient",
            "portrait_m_sport",
            "portrait_f_cool",
            "portrait_f_hanfu",
            "portrait_f_vitality",
            "portrait_f_qipao",
            "portrait_f_snow",
            "portrait_f_ballet",
            "portrait_f_cyber",
            '[data-theme="tide_blue"]',
            "theme-gallery-wrap",
            "theme-group-head",
        ):
            self.assertIn(marker, html, f"Missing UI marker: {marker}")

    def test_theme_catalog_light_dark_partition_is_complete(self) -> None:
        code, res = self.api.dispatch("GET", "/api/preferences", {"space_id": ["group_sp"]})
        self.assertEqual(code, 200)
        cat = res["themes_catalog"]
        light = [t for t in cat if t["is_dark"] is False]
        dark = [t for t in cat if t["is_dark"] is True]
        self.assertEqual(len(light) + len(dark), len(cat))
        self.assertGreaterEqual(len(light), 5)
        self.assertGreaterEqual(len(dark), 5)
        self.assertIn("tide_blue", {t["id"] for t in light})

    def test_focus_duration_pills_do_not_override_active_text_color_inline(self) -> None:
        html = (REPO_ROOT / "src" / "trace_memory" / "api" / "dashboard.html").read_text(encoding="utf-8")
        self.assertNotIn(
            'onclick="selectPomodoroDuration(25)" style="border-color:var(--primary);color:var(--primary)',
            html,
        )
        self.assertNotIn("b.style.color = active ? 'var(--primary)' : '';", html)
        self.assertIn(".btn-group button.active:hover", html)


if __name__ == "__main__":
    unittest.main()