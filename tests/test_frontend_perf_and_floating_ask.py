from __future__ import annotations

import asyncio
import gzip
from pathlib import Path
import tempfile
import unittest

from trace_memory.api.app import (
    DASHBOARD_HTML_PATH,
    ApiDispatcher,
    _etag_matches,
    _load_dashboard_html_cached,
    create_fastapi_app,
)
from trace_memory.config import TraceMemoryConfig
from trace_memory.container import AppContainer


class TestFrontendPerfAndFloatingAsk(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _build_container(self, *, auth_required: bool = False) -> AppContainer:
        cfg = TraceMemoryConfig(
            default_space="default",
            data_dir=self.root / ("data_auth" if auth_required else "data_open"),
            knowledge_dir=self.root / ("know_auth" if auth_required else "know_open"),
            auth_required=auth_required,
            rate_limit_per_minute=200,
        )
        return AppContainer.build(cfg)

    def test_bootstrap_endpoint_combines_cold_boot_state_open_mode(self) -> None:
        container = self._build_container(auth_required=False)
        try:
            api = ApiDispatcher(container)
            api.dispatch("POST", "/api/preferences", body={"space_id": "boot_sp", "theme_id": "nebula"})
            code, boot = api.dispatch("GET", "/api/bootstrap", {"space_id": ["boot_sp"]})
            self.assertEqual(code, 200)
            self.assertEqual(boot["health"]["status"], "ok")
            self.assertFalse(boot["auth"]["auth_required"])
            self.assertEqual(boot["preferences"]["theme_id"], "nebula")
            self.assertIn("sync_version", boot["sync_state"])
            self.assertIn("pending_ingest", boot["sync_state"])
        finally:
            container.close()

    def test_bootstrap_endpoint_respects_auth_required_without_401_on_guest_boot(self) -> None:
        container = self._build_container(auth_required=True)
        try:
            api = ApiDispatcher(container)
            # 1. Guest / expired token cold boot gets 200 with null space data (no leak, no 401 crash)
            code_guest, boot_guest = api.dispatch(
                "GET",
                "/api/bootstrap",
                headers={"Authorization": "Bearer expired_invalid_tok"},
            )
            self.assertEqual(code_guest, 200)
            self.assertTrue(boot_guest["auth"]["auth_required"])
            self.assertFalse(boot_guest["auth"]["authenticated"])
            self.assertIsNone(boot_guest["preferences"])
            self.assertIsNone(boot_guest["sync_state"])

            # 2. Registered & authenticated user gets full preferences + sync_state in 1 RTT
            c_reg, reg = api.dispatch(
                "POST",
                "/api/auth/register",
                body={"username": "alice_boot", "password": "Password123!", "display_name": "Alice"},
            )
            self.assertEqual(c_reg, 200)
            token = reg["token"]
            api.dispatch(
                "POST",
                "/api/preferences",
                body={"theme_id": "deep_forest"},
                headers={"Authorization": f"Bearer {token}"},
            )
            code_authed, boot_authed = api.dispatch(
                "GET",
                "/api/bootstrap",
                headers={"Authorization": f"Bearer {token}"},
            )
            self.assertEqual(code_authed, 200)
            self.assertTrue(boot_authed["auth"]["authenticated"])
            self.assertEqual(boot_authed["auth"]["user"]["username"], "alice_boot")
            self.assertEqual(boot_authed["preferences"]["theme_id"], "deep_forest")
            self.assertIsNotNone(boot_authed["sync_state"])
        finally:
            container.close()

    def test_etag_weak_and_proxy_gzip_matching(self) -> None:
        raw_bytes, etag = _load_dashboard_html_cached()
        self.assertGreater(len(raw_bytes), 100_000)
        self.assertTrue(etag.startswith('W/"') and etag.endswith('"'))
        bare = etag[3:-1]
        self.assertTrue(_etag_matches(etag, etag))
        self.assertTrue(_etag_matches(f'"{bare}"', etag))
        self.assertTrue(_etag_matches(f'W/"{bare}-gzip"', etag))
        self.assertTrue(_etag_matches(f'W/"other", W/"{bare}"', etag))
        self.assertFalse(_etag_matches('W/"0000000000000000"', etag))
        self.assertFalse(_etag_matches(None, etag))

    def test_fastapi_index_gzip_compression_and_304_not_modified(self) -> None:
        container = self._build_container(auth_required=False)
        try:
            app = create_fastapi_app(container)

            async def _call_asgi(path: str, req_headers: list[tuple[bytes, bytes]]) -> tuple[int, dict[str, str], bytes]:
                status_code = 0
                resp_headers: dict[str, str] = {}
                chunks: list[bytes] = []

                async def receive():
                    return {"type": "http.request", "body": b"", "more_body": False}

                async def send(msg):
                    nonlocal status_code, resp_headers
                    if msg["type"] == "http.response.start":
                        status_code = msg["status"]
                        resp_headers = {
                            k.decode("latin1").lower(): v.decode("latin1")
                            for k, v in msg.get("headers", [])
                        }
                    elif msg["type"] == "http.response.body":
                        chunks.append(msg.get("body", b""))

                scope = {
                    "type": "http",
                    "asgi": {"version": "3.0"},
                    "http_version": "1.1",
                    "method": "GET",
                    "scheme": "http",
                    "path": path,
                    "raw_path": path.encode("ascii"),
                    "query_string": b"",
                    "headers": req_headers,
                    "client": ("127.0.0.1", 51234),
                    "server": ("127.0.0.1", 18090),
                }
                await app(scope, receive, send)
                return status_code, resp_headers, b"".join(chunks)

            code, hdrs, gz_body = asyncio.run(
                _call_asgi("/", [(b"accept-encoding", b"gzip, deflate, br")])
            )
            self.assertEqual(code, 200)
            self.assertEqual(hdrs.get("content-encoding"), "gzip")
            self.assertEqual(hdrs.get("cache-control"), "no-cache")
            etag = hdrs.get("etag", "")
            self.assertTrue(etag.startswith('W/"'))
            raw_html = DASHBOARD_HTML_PATH.read_bytes()
            self.assertLess(len(gz_body), int(len(raw_html) * 0.35))
            self.assertEqual(gzip.decompress(gz_body), raw_html)

            code_304, hdrs_304, body_304 = asyncio.run(
                _call_asgi(
                    "/",
                    [
                        (b"accept-encoding", b"gzip"),
                        (b"if-none-match", etag.encode("latin1")),
                    ],
                )
            )
            self.assertEqual(code_304, 304)
            self.assertEqual(body_304, b"")
            self.assertEqual(hdrs_304.get("etag"), etag)
        finally:
            container.close()

    def test_dashboard_html_contains_floating_ask_optimistic_ux_and_zero_native_confirm(self) -> None:
        html = DASHBOARD_HTML_PATH.read_text(encoding="utf-8")
        required_markers = [
            "localStorage.getItem('tm_theme_id')",
            "/api/bootstrap",
            "renderInitialTasksSkeleton",
            'id="askFabBtn"',
            'id="askFloatPanel"',
            'id="askFloatHistory"',
            'id="askFloatInput"',
            "toggleAskFloatPanel()",
            "openFullAskFromFloat()",
            "syncAskFabVisibility()",
            'id="confirmDialogModal"',
            "askConfirmModal(",
            'id="todoInsightsFoldWrap"',
            'id="todoQuickScopeBar"',
            "setTaskQuickScope(",
            "_applyOptimisticTaskTransition(",
            "actionLabel: '↩ 撤销'",
            # Memory rail opens Star Graph by default + button hover specificity protection + week idle folding
            'data-tab="memories" onclick="switchTab(\'graph\', this)"',
            ":where(button):hover",
            "toggleCalIdleBand(",
            "cal-fold-band-pill",
            "is-idle-fold",
            "hasGutterOverlap",
            "cal-unsched-x",
            # Existing element contracts must remain intact
            'id="todoOverviewSection"',
            'id="taskKpis"',
            'id="topFocusList"',
            'id="taskInsightsBody"',
            'id="diagList"',
            "if (_imagePickerActive || _imageUploadInFlight) return true;",
        ]
        for marker in required_markers:
            self.assertIn(marker, html, f"Missing expected marker in dashboard.html: {marker}")

        self.assertNotIn("if (!confirm(", html)
        self.assertNotIn('<div class="rail-tip-box">', html)


if __name__ == "__main__":
    unittest.main()
