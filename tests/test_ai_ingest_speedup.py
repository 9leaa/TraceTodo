"""Tests for AI Ingest end-to-end speedup across Backend, Web, and Apple clients."""

from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from trace_memory.api.app import DASHBOARD_HTML_PATH, ApiDispatcher
from trace_memory.config import TraceMemoryConfig
from trace_memory.container import AppContainer
from trace_memory.llm.mock_provider import DeterministicLLMProvider, SemanticHashEmbedder
from trace_memory.llm.openai_compat import OpenAICompatEmbedder, _post_json_with_retry


REPO_ROOT = Path(__file__).resolve().parents[1]


class _SlowEmbedder(SemanticHashEmbedder):
    """Simulates a 350ms network embedding call to verify task commit is decoupled from embedding."""

    def __init__(self, delay_seconds: float = 0.35, dimension: int = 128) -> None:
        super().__init__(dimension=dimension)
        self.delay_seconds = delay_seconds
        self.calls = 0

    def embed(self, text: str) -> list[float]:
        self.calls += 1
        if self.delay_seconds > 0:
            time.sleep(self.delay_seconds)
        return super().embed(text)


class TestAIIngestSpeedup(unittest.TestCase):
    def test_keep_alive_pool_reuses_single_tcp_connection_and_recovers_on_close(self) -> None:
        connection_ids: set[int] = set()
        request_count = 0

        class _KeepAliveHandler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def do_POST(self) -> None:  # noqa: N802
                nonlocal request_count
                request_count += 1
                connection_ids.add(self.connection.fileno())
                length = int(self.headers.get("Content-Length", "0") or "0")
                _ = self.rfile.read(length) if length > 0 else b""
                resp = json.dumps({"ok": True, "seq": request_count}).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(resp)))
                # Force close on 3rd request to verify stale/closed socket auto-recovery on 4th
                if request_count == 3:
                    self.send_header("Connection", "close")
                    self.close_connection = True
                else:
                    self.send_header("Connection", "keep-alive")
                self.end_headers()
                self.wfile.write(resp)

            def log_message(self, fmt: str, *args) -> None:
                return

        class _DaemonServer(ThreadingHTTPServer):
            daemon_threads = True

        server = _DaemonServer(("127.0.0.1", 0), _KeepAliveHandler)
        port = server.server_address[1]
        t = threading.Thread(target=server.serve_forever, daemon=True)
        t.start()
        try:
            with patch("urllib.request.getproxies", return_value={}):
                url = f"http://127.0.0.1:{port}/v1/chat/completions"
                r1 = _post_json_with_retry(url, "k", {"ping": 1}, timeout_seconds=5.0)
                r2 = _post_json_with_retry(url, "k", {"ping": 2}, timeout_seconds=5.0)
                self.assertEqual(r1["seq"], 1)
                self.assertEqual(r2["seq"], 2)
                # Requests 1 & 2 reused the exact same keep-alive socket
                self.assertEqual(len(connection_ids), 1)

                r3 = _post_json_with_retry(url, "k", {"ping": 3}, timeout_seconds=5.0)
                r4 = _post_json_with_retry(url, "k", {"ping": 4}, timeout_seconds=5.0)
                self.assertEqual(r3["seq"], 3)
                self.assertEqual(r4["seq"], 4)
                self.assertEqual(request_count, 4)
        finally:
            server.shutdown()
            server.server_close()

    def test_batch_embedder_collapses_multiple_items_into_minimal_http_calls(self) -> None:
        http_payloads: list[dict] = []

        def _fake_post(url: str, api_key: str, payload: dict, timeout_seconds: float = 20.0) -> dict:
            http_payloads.append(payload)
            inp = payload["input"]
            items = inp if isinstance(inp, list) else [inp]
            return {
                "data": [
                    {"index": idx, "embedding": [float(idx + 1), 0.5, 0.25, 0.125]}
                    for idx in range(len(items))
                ]
            }

        embedder = OpenAICompatEmbedder(
            base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
            api_key="sk-test",
            model="text-embedding-v3",
            dimension=4,
        )
        with patch("trace_memory.llm.openai_compat._post_json_with_retry", side_effect=_fake_post):
            # 1) Sync ingest of a 2-memory note + 1 RawNote = 3 vectors in ONE single HTTP batch call!
            with tempfile.TemporaryDirectory() as tmp:
                base = Path(tmp)
                app = AppContainer.build(
                    TraceMemoryConfig(
                        default_space="batch_sp",
                        data_dir=base / "data",
                        knowledge_dir=base / "know",
                    ),
                    llm_override=DeterministicLLMProvider(),
                    embedder_override=embedder,
                )
                try:
                    res = app.ingest_service.record_note("batch_sp", "我住在上海。平时喜欢喝拿铁咖啡。")
                    self.assertEqual(res.extraction_status, "completed")
                    self.assertEqual(len(res.changes), 2)
                    self.assertEqual(len(http_payloads), 1)
                    self.assertEqual(len(http_payloads[0]["input"]), 3)
                finally:
                    app.close()

            # 2) 12 texts chunk cleanly into 10 + 2 (DashScope <= 10 limit)
            http_payloads.clear()
            vecs = embedder.embed_many([f"文本{i}" for i in range(12)])
            self.assertEqual(len(vecs), 12)
            self.assertEqual(len(http_payloads), 2)
            self.assertEqual(len(http_payloads[0]["input"]), 10)
            self.assertEqual(len(http_payloads[1]["input"]), 2)

    def test_async_ingest_decouples_task_visibility_and_long_poll_from_slow_embedding(self) -> None:
        slow_embedder = _SlowEmbedder(delay_seconds=0.35)
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            app = AppContainer.build(
                TraceMemoryConfig(
                    default_space="fast_sp",
                    data_dir=base / "data",
                    knowledge_dir=base / "know",
                    ingest_worker_count=2,
                ),
                llm_override=DeterministicLLMProvider(),
                embedder_override=slow_embedder,
            )
            try:
                app.ingest_worker.start()
                api = ApiDispatcher(app)

                t0 = time.perf_counter()
                code_rec, rec = api.dispatch(
                    "POST",
                    "/api/notes/record",
                    body={
                        "space_id": "fast_sp",
                        "content": "明天下午必须提交线上支付性能优化方案",
                        "async_mode": True,
                    },
                )
                self.assertEqual(code_rec, 202, rec)
                job_id = rec["job_id"]

                # Long-poll wakes immediately when LLM + SQLite commit finish (WITHOUT waiting 2 * 350ms = 700ms embedding!)
                code_st, st = api.dispatch(
                    "GET",
                    "/api/notes/ingest-status",
                    {
                        "space_id": ["fast_sp"],
                        "job_id": [job_id],
                        "wait_ms": ["4000"],
                    },
                )
                visible_ms = (time.perf_counter() - t0) * 1000.0
                self.assertEqual(code_st, 200, st)
                self.assertEqual(st["pending"], 0)
                self.assertEqual(st["job"]["status"], "completed")
                self.assertLess(
                    visible_ms,
                    300.0,
                    f"Task visibility took {visible_ms:.1f}ms; should not block on 700ms embeddings!",
                )

                # Four-Quadrant matrix ALREADY contains the extracted task immediately!
                code_mat, mat = api.dispatch("GET", "/api/tasks/matrix", {"space_id": ["fast_sp"]})
                self.assertEqual(code_mat, 200)
                self.assertEqual(len(mat["sorted_tasks"]), 1)

                # Wait for deferred vector worker to finish and confirm vectors are indexed
                self.assertTrue(app.ingest_worker.wait_for_space_idle("fast_sp", timeout=5.0))
                self.assertGreaterEqual(slow_embedder.calls, 2)
                hits = app.retrieval_service.search("fast_sp", "支付性能优化")
                self.assertGreaterEqual(len(hits["memories"]), 1)
            finally:
                app.close()

    def test_web_and_apple_three_client_speedup_contracts(self) -> None:
        html = DASHBOARD_HTML_PATH.read_text(encoding="utf-8")
        for web_marker in (
            'id="optimisticIngestStack"',
            "addOptimisticIngestCard(",
            "refreshAfterIngestChange()",
            "ensureSyncEventStream()",
            "/api/sync/stream",
        ):
            self.assertIn(web_marker, html)

        todo_store = (REPO_ROOT / "apple-app/Sources/TraceTodo/TodoStore.swift").read_text(encoding="utf-8")
        api_client = (REPO_ROOT / "apple-app/Sources/TraceTodo/APIClient.swift").read_text(encoding="utf-8")
        theme_prefs = (REPO_ROOT / "apple-app/Sources/TraceTodo/ThemeAndPreferences.swift").read_text(encoding="utf-8")
        mac_objc = (REPO_ROOT / "apple-app/mac-native/TraceTodoMac.m").read_text(encoding="utf-8")

        self.assertIn("optimisticIngestItems", todo_store)
        self.assertIn("func refreshTasksFast()", todo_store)
        self.assertIn("streamSyncEvents(", api_client)
        self.assertIn("func fetchBootstrap()", api_client)
        self.assertIn("struct OptimisticIngestBannerStackView", theme_prefs)
        self.assertIn('@"async_mode": @YES', mac_objc)


if __name__ == "__main__":
    unittest.main()
