"""Tests for Dense Vector Similarity, Vector Lifecycle Cleanup, Forward Links, and Graph Budgets."""

from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from trace_memory.config import TraceMemoryConfig
from trace_memory.container import AppContainer
from trace_memory.domain.proposals import ChangeProposal, RelationSpec
from trace_memory.storage.vector_store import MEMORIES_COLLECTION


class TestVectorHybridAndGraph(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        base = Path(self._tmp.name)
        self.app = AppContainer.build(
            TraceMemoryConfig(
                default_space="vec_space",
                data_dir=base / "data",
                knowledge_dir=base / "knowledge",
            )
        )

    def tearDown(self) -> None:
        self.app.close()
        self._tmp.cleanup()

    def test_dense_vector_recalls_non_overlapping_semantic_synonyms(self) -> None:
        # Surface words: "微信支付回调超时 bug"
        self.app.ingest_service.record_note("vec_space", "明天必须修复微信支付回调超时 bug")

        # Query uses completely non-overlapping words: "线上结算隐患"
        # BM25 has 0 token overlap, so recall MUST come from Dense Vector similarity!
        res = self.app.retrieval_service.search("vec_space", "线上结算隐患")
        self.assertGreaterEqual(len(res["memories"]), 1)
        top_hit = res["memories"][0]
        self.assertIsNone(top_hit["bm25_rank"])
        self.assertEqual(top_hit["vector_rank"], 1)
        self.assertGreater(top_hit["vector_score"], 0.25)

    def test_vector_lifecycle_purges_forgotten_and_superseded_vectors(self) -> None:
        r = self.app.ingest_service.record_note("vec_space", "明天必须修复微信支付回调超时 bug")
        mem = r.changes[0].memory
        assert mem is not None
        self.assertEqual(self.app.vector_store.count(MEMORIES_COLLECTION, "vec_space"), 1)

        # Forget the memory -> vector record must be physically purged
        self.app.change_service.apply(
            ChangeProposal(
                space_id="vec_space",
                action="forget",
                memory_type="task",
                target_memory_id=mem.memory_id,
                expected_version=mem.version,
            )
        )
        self.assertEqual(self.app.vector_store.count(MEMORIES_COLLECTION, "vec_space"), 0)

    def test_forward_reference_resolution_and_cycle_safe_budgeted_expansion(self) -> None:
        # 1. Create Memory A linking to non-existent `[[Architecture Spec]]` (forward reference!)
        a = self.app.change_service.apply(
            ChangeProposal(
                space_id="vec_space",
                action="create",
                memory_type="semantic",
                title="Search Engine Design",
                summary="Uses RRF over BM25 and Cosine vectors",
                payload={"subject": "search", "predicate": "uses", "value": "RRF"},
                relations=(RelationSpec(relation_type="implements", target="Architecture Spec"),),
            )
        ).memory
        assert a is not None

        rels_before = self.app.relation_repo.get_neighbors("vec_space", a.memory_id)
        self.assertEqual(len(rels_before), 1)
        self.assertFalse(rels_before[0].to_dict()["resolved"])

        # 2. Create `Architecture Spec` linking back to `Search Engine Design` (forming a cycle!)
        b = self.app.change_service.apply(
            ChangeProposal(
                space_id="vec_space",
                action="create",
                memory_type="semantic",
                title="Architecture Spec",
                summary="Core system blueprint",
                payload={"subject": "arch", "predicate": "defines", "value": "blueprint"},
                relations=(RelationSpec(relation_type="relates_to", target="Search Engine Design"),),
            )
        ).memory
        assert b is not None

        # Forward reference on A -> B must now be automatically resolved!
        rels_after = [
            r for r in self.app.relation_repo.get_neighbors("vec_space", a.memory_id)
            if r.from_memory_id == a.memory_id
        ]
        self.assertEqual(rels_after[0].to_memory_id, b.memory_id)

        # 3. Cyclic graph expansion finishes cleanly and respects `max_nodes=1` budget
        ctx_full = self.app.retrieval_service.build_graph_context(
            "vec_space", a.memory_id, max_hops=3, max_nodes=5
        )
        self.assertEqual(len(ctx_full["nodes"]), 2)

        ctx_capped = self.app.retrieval_service.build_graph_context(
            "vec_space", a.memory_id, max_hops=3, max_nodes=1
        )
        self.assertEqual(len(ctx_capped["nodes"]), 1)
        self.assertTrue(ctx_capped["budget"]["budget_truncated"])

        # 4. Forgetting B clears `to_memory_id` (keeps broken link metadata) and blocks B from expansion
        self.app.change_service.apply(
            ChangeProposal(
                space_id="vec_space",
                action="forget",
                memory_type="semantic",
                target_memory_id=b.memory_id,
                expected_version=b.version,
            )
        )
        ctx_after_forget = self.app.retrieval_service.build_graph_context(
            "vec_space", a.memory_id, max_hops=3, max_nodes=5
        )
        expanded_ids = [n["memory"]["memory_id"] for n in ctx_after_forget["nodes"]]
        self.assertEqual(expanded_ids, [a.memory_id])


if __name__ == "__main__":
    unittest.main()
