"""Tests for Markdown Sync/Conflicts, Budgeted Dream Consolidation, MCP Server, and Web API."""

from __future__ import annotations

import io
import json
from pathlib import Path
import shutil
import tempfile
import unittest

from trace_memory.api.app import ApiDispatcher
from trace_memory.config import TraceMemoryConfig
from trace_memory.container import AppContainer
from trace_memory.mcp_server.server import MCPToolDispatcher, serve_mcp_stdio


class TestMarkdownDreamMcpAndApi(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        base = Path(self._tmp.name)
        self.app = AppContainer.build(
            TraceMemoryConfig(
                default_space="dev",
                data_dir=base / "data",
                knowledge_dir=base / "knowledge",
            )
        )

    def tearDown(self) -> None:
        self.app.close()
        self._tmp.cleanup()

    def test_markdown_export_rebuild_reflow_and_conflict_draft_preservation(self) -> None:
        r = self.app.ingest_service.record_note("dev", "我现在搬到上海了")
        mem = r.changes[0].memory
        assert mem is not None

        # 1. Deterministic export & full rebuild after deleting export folder
        exp1 = self.app.markdown_sync.export_space("dev")
        self.assertEqual(exp1["active_count"], 1)
        space_dir = Path(exp1["export_dir"])
        md_file = space_dir / f"{mem.permalink}.md"
        self.assertTrue(md_file.is_file())
        original_content = md_file.read_text(encoding="utf-8")

        shutil.rmtree(space_dir)
        self.app.markdown_sync.export_space("dev")
        self.assertEqual(md_file.read_text(encoding="utf-8"), original_content)

        # 2. Controlled external edit reflow (v1 -> v2) with zero sync loop on re-scan
        edited_text = original_content.replace("居住城市：上海", "居住城市：上海浦东张江")
        md_file.write_text(edited_text, encoding="utf-8")
        sync1 = self.app.markdown_sync.sync_external_edits("dev")
        self.assertEqual(sync1["applied"], [mem.memory_id])
        self.assertEqual(len(sync1["conflicts"]), 0)

        # Immediate second scan must detect 0 changes (no infinite loop!)
        sync2 = self.app.markdown_sync.sync_external_edits("dev")
        self.assertEqual(sync2["applied"], [])
        self.assertEqual(sync2["unchanged"], 1)

        # 3. Concurrent conflict: file is at v2, server advances to v3, then user saves stale v2 edit
        stale_v2_draft = md_file.read_text(encoding="utf-8").replace(
            "上海浦东张江", "被过期草稿修改的文本"
        )
        # Advance server to v3 directly
        v2_mem = self.app.memory_repo.get("dev", mem.memory_id)
        assert v2_mem is not None
        self.assertEqual(v2_mem.version, 2)
        ApiDispatcher(self.app).dispatch(
            "POST",
            "/api/memories/propose",
            body={
                "space_id": "dev",
                "action": "update",
                "memory_type": "semantic",
                "target_memory_id": mem.memory_id,
                "expected_version": 2,
                "summary": "服务端最新权威版本 v3：上海徐汇",
            },
        )

        # Now write user's stale v2 draft to disk and sync
        md_file.write_text(stale_v2_draft, encoding="utf-8")
        sync_conflict = self.app.markdown_sync.sync_external_edits("dev")
        self.assertEqual(len(sync_conflict["conflicts"]), 1)

        # Server v3 is NOT overwritten, and user's draft is preserved in `*.conflict-v2.md`!
        v3_mem = self.app.memory_repo.get("dev", mem.memory_id)
        assert v3_mem is not None
        self.assertEqual(v3_mem.version, 3)
        self.assertIn("徐汇", v3_mem.summary)
        conflict_draft = space_dir / sync_conflict["conflicts"][0]["draft_saved_to"]
        self.assertTrue(conflict_draft.is_file())
        self.assertIn("被过期草稿修改的文本", conflict_draft.read_text(encoding="utf-8"))

    def test_dream_consolidation_budget_guard_and_inferred_tagging(self) -> None:
        # Create two clusters (2 episodes + 2 preferences)
        self.app.ingest_service.record_note("dev", "上周参加了架构评审会")
        self.app.ingest_service.record_note("dev", "今天参加了线上故障复盘会")
        self.app.ingest_service.record_note("dev", "我喜欢喝美式咖啡")
        self.app.ingest_service.record_note("dev", "我喜欢用极简键盘写代码")

        # Run Dream with strict `max_llm_calls=1` budget -> stops after 1st cluster!
        dream1 = self.app.dream_service.run_dream("dev", max_llm_calls=1)
        self.assertEqual(dream1["budget"]["used_llm_calls"], 1)
        self.assertTrue(dream1["budget"]["budget_halted"])
        self.assertEqual(dream1["insights_processed"], 1)

        created_insight = dream1["results"][0]["memory"]
        self.assertEqual(created_insight["assertion_kind"], "inferred")
        self.assertGreaterEqual(len(created_insight["evidences"]), 2)

        # Re-running with sufficient budget skips already-consolidated cluster and processes remaining
        dream2 = self.app.dream_service.run_dream("dev", max_llm_calls=5)
        self.assertGreaterEqual(dream2["skipped_unchanged"], 1)

    def test_mcp_stdio_protocol_and_web_api_endpoints(self) -> None:
        # 1. Test MCP JSON-RPC over stdio stream
        requests = [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
            {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {
                    "name": "tm_record",
                    "arguments": {"content": "今天必须修复支付对账延迟 bug"},
                },
            },
            {
                "jsonrpc": "2.0",
                "id": 4,
                "method": "tools/call",
                "params": {"name": "tm_tasks", "arguments": {"sort_by": "priority"}},
            },
        ]
        in_stream = io.StringIO("\n".join(json.dumps(r, ensure_ascii=False) for r in requests) + "\n")
        out_stream = io.StringIO()
        serve_mcp_stdio(self.app, space_id="dev", stdin=in_stream, stdout=out_stream)

        responses = [json.loads(line) for line in out_stream.getvalue().splitlines() if line.strip()]
        # 4 responses (notification produces no response)
        self.assertEqual(len(responses), 4)
        tools = responses[1]["result"]["tools"]
        read_only_map = {t["name"]: t["annotations"]["readOnlyHint"] for t in tools}
        self.assertTrue(read_only_map["tm_search"])
        self.assertTrue(read_only_map["tm_tasks"])
        self.assertFalse(read_only_map["tm_record"])

        task_res = responses[3]["result"]["structuredContent"]
        self.assertEqual(task_res["summary_counts"]["Q1_do_first"], 1)

        # Restricted action via MCP must be rejected
        disp = MCPToolDispatcher(self.app, trusted_space_id="dev")
        err_call = disp.call_tool("tm_propose_change", {"action": "approve", "target_memory_id": "x"})
        self.assertTrue(err_call["isError"])

        # 2. Test Web API Dispatcher (seed-demo, health, matrix, ask, traces)
        api = ApiDispatcher(self.app)
        code_h, health = api.dispatch("GET", "/api/health")
        self.assertEqual(code_h, 200)
        self.assertEqual(health["status"], "ok")

        code_s, seed = api.dispatch("POST", "/api/seed-demo", body={"space_id": "demo_space"})
        self.assertEqual(code_s, 200)
        self.assertGreater(seed["seeded_notes"], 5)

        code_m, mat = api.dispatch("GET", "/api/tasks/matrix", {"space_id": ["demo_space"]})
        self.assertEqual(code_m, 200)
        self.assertGreaterEqual(mat["summary_counts"]["total_todo"], 4)

        # 3. Regression check for FastAPI route signature when FastAPI is installed
        try:
            from fastapi.testclient import TestClient
            from trace_memory.api.app import create_fastapi_app

            client = TestClient(create_fastapi_app(self.app))
            self.assertEqual(client.get("/api/health?space_id=dev").status_code, 200)
            self.assertEqual(
                client.get("/api/tasks/matrix?sort_by=priority&include_done=false&space_id=dev").status_code,
                200,
            )
            fav_res = client.get("/favicon.ico")
            self.assertEqual(fav_res.status_code, 200)
            self.assertEqual(fav_res.headers.get("content-type"), "image/png")
            icon_res = client.get("/icon.png")
            self.assertEqual(icon_res.status_code, 200)
            self.assertEqual(icon_res.headers.get("content-type"), "image/png")
        except ImportError:
            pass

    def test_in_app_memory_create_edit_markdown_save_and_forget_api(self) -> None:
        api = ApiDispatcher(self.app)
        # 1. Create semantic memory directly from UI/App propose endpoint with tags
        code_c, created = api.dispatch(
            "POST",
            "/api/memories/propose",
            body={
                "space_id": "dev",
                "action": "create",
                "memory_type": "semantic",
                "title": "常驻办公城市",
                "summary": "目前主要在上海张江办公",
                "payload": {"subject": "user", "predicate": "office", "value": "上海张江"},
                "tags": ["work", "location"],
                "lock_for_user": True,
            },
        )
        self.assertEqual(code_c, 200)
        mem = created["memory"]
        mem_id = mem["memory_id"]
        self.assertEqual(mem["tags"], ["work", "location"])
        self.assertTrue(mem["user_locked"])

        # 2. Detail & Markdown read endpoint return rendered Markdown
        code_d, detail = api.dispatch("GET", "/api/memories/detail", {"space_id": ["dev"], "id": [mem_id]})
        self.assertEqual(code_d, 200)
        self.assertIn("# 常驻办公城市", detail["markdown"])
        self.assertIn("- [value] 上海张江", detail["markdown"])

        code_md, md_view = api.dispatch("GET", "/api/memories/markdown", {"space_id": ["dev"], "id": [mem_id]})
        self.assertEqual(code_md, 200)
        self.assertEqual(md_view["markdown"], detail["markdown"])

        # 3. Direct Markdown edit save advances v1 -> v2 cleanly
        edited_md = detail["markdown"].replace("上海张江", "北京海淀中关村")
        code_ms, ms_res = api.dispatch(
            "POST",
            "/api/memories/markdown/save",
            body={
                "space_id": "dev",
                "memory_id": mem_id,
                "expected_version": 1,
                "markdown": edited_md,
            },
        )
        self.assertEqual(code_ms, 200)
        self.assertEqual(ms_res["memory"]["version"], 2)
        self.assertIn("北京海淀中关村", ms_res["memory"]["summary"])
        self.assertEqual(ms_res["memory"]["payload"]["value"], "北京海淀中关村")

        # 4. Stale version Markdown save must be rejected with 409 VERSION_CONFLICT
        code_conflict, err_body = api.dispatch(
            "POST",
            "/api/memories/markdown/save",
            body={
                "space_id": "dev",
                "memory_id": mem_id,
                "expected_version": 1,
                "markdown": edited_md,
            },
        )
        self.assertEqual(code_conflict, 409)
        self.assertEqual(err_body["code"], "VERSION_CONFLICT")

        # 5. Forget memory via propose endpoint removes it from active search
        code_f, _ = api.dispatch(
            "POST",
            "/api/memories/propose",
            body={
                "space_id": "dev",
                "action": "forget",
                "target_memory_id": mem_id,
                "expected_version": 2,
            },
        )
        self.assertEqual(code_f, 200)
        _, search_after = api.dispatch("GET", "/api/memories/search", {"space_id": ["dev"], "q": [""]})
        self.assertEqual(len(search_after["memories"]), 0)


if __name__ == "__main__":
    unittest.main()
