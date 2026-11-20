"""Comprehensive tests for Issue #7: Obsidian-style Force-Directed Knowledge Graph Canvas."""

from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from trace_memory.api.app import ApiDispatcher
from trace_memory.config import TraceMemoryConfig
from trace_memory.container import AppContainer
from trace_memory.domain.proposals import ChangeProposal, RelationSpec


REPO_ROOT = Path(__file__).resolve().parents[1]


class TestObsidianGraphCanvas(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        base = Path(self._tmp.name)
        self.app = AppContainer.build(
            TraceMemoryConfig(
                default_space="graph_space",
                data_dir=base / "data",
                knowledge_dir=base / "knowledge",
            )
        )
        self.dispatcher = ApiDispatcher(self.app)
        _, reg = self.dispatcher.dispatch(
            "POST",
            "/api/auth/register",
            {},
            {"username": "alice", "password": "Password123!", "display_name": "Alice"},
        )
        self.alice_headers = {"Authorization": f"Bearer {reg['token']}"}
        self.alice_space = reg["user"]["space_id"]

    def tearDown(self) -> None:
        self.app.close()
        self._tmp.cleanup()

    def test_canvas_layers_wikilink_ghost_dream_semantic_and_forward_resolution(self) -> None:
        # 1. Propose a memory via REST API with inline [[Ghost Blueprint]] and [[Payment Core]]
        code1, res1 = self.dispatcher.dispatch(
            "POST",
            "/api/memories/propose",
            {},
            {
                "action": "create",
                "memory_type": "semantic",
                "title": "Checkout Service",
                "summary": "Handles order checkout; depends on [[Payment Core]] and [[Ghost Blueprint]]",
                "payload": {"subject": "user", "predicate": "owns", "value": "Checkout Service"},
                "tags": ["arch"],
            },
            headers=self.alice_headers,
        )
        self.assertEqual(code1, 200)
        mem_checkout_id = res1["memory"]["memory_id"]

        # 2. Create [[Payment Core]] so one link resolves and [[Ghost Blueprint]] remains a ghost node
        code2, res2 = self.dispatcher.dispatch(
            "POST",
            "/api/memories/propose",
            {},
            {
                "action": "create",
                "memory_type": "semantic",
                "title": "Payment Core",
                "summary": "Processes WeChat & Alipay payment callbacks for order checkout",
                "payload": {"subject": "user", "predicate": "maintains", "value": "Payment Core"},
                "tags": ["arch"],
            },
            headers=self.alice_headers,
        )
        self.assertEqual(code2, 200)
        mem_payment_id = res2["memory"]["memory_id"]

        # 3. Run Dream consolidation to generate an `inferred` insight with `derived_from` edges
        code_dream, dream_res = self.dispatcher.dispatch(
            "POST", "/api/dream", {}, {}, headers=self.alice_headers
        )
        self.assertEqual(code_dream, 200)
        self.assertGreaterEqual(dream_res["insights_processed"], 1)

        # 4. Query GET /api/graph/canvas
        code_g, canvas = self.dispatcher.dispatch(
            "GET",
            "/api/graph/canvas",
            {"semantic_threshold": ["0.35"]},
            None,
            headers=self.alice_headers,
        )
        self.assertEqual(code_g, 200)
        self.assertEqual(canvas["mode"], "global")

        nodes_by_id = {n["id"]: n for n in canvas["nodes"]}
        self.assertIn(mem_checkout_id, nodes_by_id)
        self.assertIn(mem_payment_id, nodes_by_id)

        ghost_nodes = [n for n in canvas["nodes"] if n["is_ghost"]]
        self.assertEqual(len(ghost_nodes), 1)
        self.assertEqual(ghost_nodes[0]["title"], "Ghost Blueprint")

        edge_kinds = {e["edge_kind"] for e in canvas["edges"]}
        self.assertIn("wikilink", edge_kinds)
        self.assertIn("ghost_ref", edge_kinds)
        self.assertIn("derived_from", edge_kinds)

        # 5. Creating "Ghost Blueprint" resolves the ghost node into a real node + wikilink edge!
        code3, _ = self.dispatcher.dispatch(
            "POST",
            "/api/memories/propose",
            {},
            {
                "action": "create",
                "memory_type": "semantic",
                "title": "Ghost Blueprint",
                "summary": "System target architecture spec",
                "payload": {"subject": "user", "predicate": "spec", "value": "v2"},
            },
            headers=self.alice_headers,
        )
        self.assertEqual(code3, 200)

        _, canvas_after = self.dispatcher.dispatch(
            "GET", "/api/graph/canvas", {}, None, headers=self.alice_headers
        )
        self.assertEqual(canvas_after["stats"]["ghost_nodes"], 0)
        self.assertGreaterEqual(canvas_after["stats"]["wikilink_edges"], 2)

    def test_local_seed_hops_subject_filter_and_forget_c07_c09_isolation(self) -> None:
        # Self task + colleague task
        m_self = self.app.change_service.apply(
            ChangeProposal(
                space_id=self.alice_space,
                action="create",
                memory_type="task",
                title="修复支付网关超时",
                summary="排查线上微信支付回调超时",
                payload={"task_status": "todo", "importance": 5, "urgency": 5, "assignee": ""},
                relations=(RelationSpec(relation_type="depends_on", target="网关监控配置"),),
            )
        ).memory
        assert m_self is not None

        m_link = self.app.change_service.apply(
            ChangeProposal(
                space_id=self.alice_space,
                action="create",
                memory_type="semantic",
                title="网关监控配置",
                summary="线上支付网关 Prometheus 告警阈值说明",
                payload={"subject": "self", "predicate": "doc", "value": "prometheus"},
            )
        ).memory
        assert m_link is not None

        m_colleague = self.app.change_service.apply(
            ChangeProposal(
                space_id=self.alice_space,
                action="create",
                memory_type="task",
                title="小王负责整理季度财务报表",
                summary="小王周五前提交财务报表",
                payload={"task_status": "todo", "importance": 3, "urgency": 3, "assignee": "小王"},
            )
        ).memory
        assert m_colleague is not None

        # Subject filter: subject=小王 only returns colleague's node, while stats.subjects lists both
        code_sub, sub_canvas = self.dispatcher.dispatch(
            "GET",
            "/api/graph/canvas",
            {"subject": ["小王"]},
            None,
            headers=self.alice_headers,
        )
        self.assertEqual(code_sub, 200)
        self.assertEqual([n["id"] for n in sub_canvas["nodes"]], [m_colleague.memory_id])
        self.assertEqual(sub_canvas["stats"]["subjects"], ["self", "小王"])

        # Local seed subgraph: seed=m_self, max_hops=1
        code_loc, loc_canvas = self.dispatcher.dispatch(
            "GET",
            "/api/graph/canvas",
            {"seed": [m_self.memory_id], "max_hops": ["1"], "include_semantic": ["0"]},
            None,
            headers=self.alice_headers,
        )
        self.assertEqual(code_loc, 200)
        self.assertEqual(loc_canvas["mode"], "local")
        hop_map = {n["id"]: n["hop"] for n in loc_canvas["nodes"]}
        self.assertEqual(hop_map[m_self.memory_id], 0)
        self.assertEqual(hop_map[m_link.memory_id], 1)
        self.assertNotIn(m_colleague.memory_id, hop_map)

        # C07: Forgetting m_link must purge it from nodes AND never turn it into a ghost node!
        self.app.change_service.apply(
            ChangeProposal(
                space_id=self.alice_space,
                action="forget",
                memory_type="semantic",
                target_memory_id=m_link.memory_id,
                expected_version=m_link.version,
            )
        )
        _, after_forget = self.dispatcher.dispatch(
            "GET", "/api/graph/canvas", {}, None, headers=self.alice_headers
        )
        all_ids = {n["id"] for n in after_forget["nodes"]}
        all_titles = {n["title"] for n in after_forget["nodes"]}
        self.assertNotIn(m_link.memory_id, all_ids)
        self.assertNotIn("网关监控配置", all_titles)
        self.assertEqual(after_forget["stats"]["ghost_nodes"], 0)

        # C09: Another user probing Alice's memory ID as seed must receive 403 PermissionDenied
        _, reg_bob = self.dispatcher.dispatch(
            "POST",
            "/api/auth/register",
            {},
            {"username": "bob", "password": "Password123!", "display_name": "Bob"},
        )
        bob_headers = {"Authorization": f"Bearer {reg_bob['token']}"}
        code_probe, _ = self.dispatcher.dispatch(
            "GET",
            "/api/graph/canvas",
            {"seed": [m_self.memory_id]},
            None,
            headers=bob_headers,
        )
        self.assertEqual(code_probe, 403)

    def test_web_and_apple_ui_include_obsidian_force_graph_canvas(self) -> None:
        html = (REPO_ROOT / "src" / "trace_memory" / "api" / "dashboard.html").read_text(
            encoding="utf-8"
        )
        self.assertIn('id="graphCanvas"', html)
        self.assertIn('id="graphInspector"', html)
        self.assertIn("/api/graph/canvas?", html)
        self.assertIn("function stepGraphPhysics()", html)
        self.assertIn("function selectGraphNode(", html)

        swift_mem = (
            REPO_ROOT / "apple-app" / "Sources" / "TraceTodo" / "MemoriesView.swift"
        ).read_text(encoding="utf-8")
        self.assertIn("struct KnowledgeGraphCanvasView: View", swift_mem)
        self.assertIn("fetchGraphCanvas(", swift_mem)


if __name__ == "__main__":
    unittest.main()
