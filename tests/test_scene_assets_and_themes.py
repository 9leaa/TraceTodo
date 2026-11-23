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
        for new_id in ("nebula", "cyber", "polar", "sakura"):
            self.assertIn(new_id, ids)

        code, res = self.api.dispatch(
            "POST",
            "/api/preferences",
            body={
                "space_id": "scene_sp",
                "theme_id": "nebula",
                "extra": {"scene_id": "starfield", "scene_intensity": 3, "scene_motion": 1},
            },
        )
        self.assertEqual(code, 200)
        self.assertEqual(res["theme_id"], "nebula")
        self.assertEqual(res["extra"]["scene_id"], "starfield")
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

        with urllib.request.urlopen(f"http://127.0.0.1:{port}/scenes/starfield.jpg", timeout=5) as r:
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
        ):
            self.assertIn(marker, html, f"Missing UI marker: {marker}")


if __name__ == "__main__":
    unittest.main()