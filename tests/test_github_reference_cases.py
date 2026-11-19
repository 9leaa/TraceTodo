"""GitHub-Referenced Test Suite drawing real-world scenarios from `suixinji`, `basic-memory`, and `ReMe`.

Reference mapping:
- TC-SXJ-01..03  <- `suixinji/tests/test_task_state_reconciliation.py`,
                    `suixinji/tests/test_layer3_v2_no_answer_gate.py`,
                    `suixinji/tests/test_fact_memory_source_revision.py`
- TC-BM-01..04   <- `basic-memory/test-int/test_locked_notes.py`,
                    `basic-memory/test-int/test_mandatory_permalinks_integration.py`,
                    `basic-memory/docs/DOMAIN_MODEL.md`
- TC-REME-01..03 <- `ReMe/tests/unit/test_auto_dream.py`,
                    `ReMe/tests/unit/test_auto_memory_idempotency.py`
- TC-FUS-01..02  <- Trace-Memory Fusion (4-Quadrant Overdue Loop + Mixed Code/Semantic RRF)
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import tempfile
import unittest

from trace_memory.config import TraceMemoryConfig
from trace_memory.container import AppContainer
from trace_memory.domain.memories import MemoryEvidence
from trace_memory.domain.proposals import ChangeProposal, ValidationError


class TestGitHubReferenceCases(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        base = Path(self._tmp.name)
        self.app = AppContainer.build(
            TraceMemoryConfig(
                default_space="qa_space",
                data_dir=base / "data",
                knowledge_dir=base / "knowledge",
            )
        )

    def tearDown(self) -> None:
        self.app.close()
        self._tmp.cleanup()

    # -------------------------------------------------------------------------
    # Suixinji-inspired Cases (TC-SXJ-01 .. TC-SXJ-03)
    # -------------------------------------------------------------------------

    def test_tc_sxj_01_progressive_task_lifecycle_create_inprogress_complete(self) -> None:
        """Ref: suixinji/tests/test_task_state_reconciliation.py::test_task_lifecycle_merges_action_wording."""
        # Step 1: Create todo
        self.app.ingest_service.record_note("qa_space", "记得制作系统首页消息路径图")
        # Step 2: In-progress update stays `todo` (two-state protocol) and merges into same card
        self.app.ingest_service.record_note("qa_space", "需要继续制作系统首页消息路径图")
        tasks_mid = self.app.memory_repo.list_memories("qa_space", memory_type="task")
        self.assertEqual(len(tasks_mid), 1)
        self.assertEqual(tasks_mid[0].payload["task_status"], "todo")
        self.assertEqual(tasks_mid[0].version, 2)

        # Step 3: Completion transitions the single card to `done` (v3) with all 3 evidences attached
        self.app.ingest_service.record_note("qa_space", "系统首页消息路径图做完了")
        tasks_end = self.app.memory_repo.list_memories("qa_space", memory_type="task")
        self.assertEqual(len(tasks_end), 1)
        self.assertEqual(tasks_end[0].payload["task_status"], "done")
        self.assertEqual(tasks_end[0].version, 3)
        self.assertEqual(len(tasks_end[0].evidences), 3)

    def test_tc_sxj_02_topic_compatibility_no_answer_gate_across_same_memory_type(self) -> None:
        """Ref: suixinji/tests/test_layer3_v2_no_answer_gate.py::test_topic_gate_rejects_same_type_different_preference_attribute."""
        self.app.ingest_service.record_note("qa_space", "我平时非常喜欢喝冰美式咖啡")
        ans = self.app.ask_service.ask("qa_space", "我最喜欢的科幻电影是什么？")
        self.assertFalse(ans["has_sufficient_evidence"])
        self.assertNotIn("冰美式", ans["answer"])
        self.assertEqual(len(ans["citations"]["memories"]), 0)

    def test_tc_sxj_03_multi_source_fact_survives_when_only_one_source_note_is_revised(self) -> None:
        """Ref: suixinji/tests/test_fact_memory_source_revision.py (multi-evidence survival)."""
        r1 = self.app.ingest_service.record_note("qa_space", "我现在住在上海")
        r2 = self.app.ingest_service.record_note("qa_space", "目前常住在上海")
        mem = self.app.memory_repo.find_by_canonical_key("qa_space", "fact:user:residence")
        assert mem is not None
        self.assertEqual(len(mem.evidences), 2)

        # Revise first note to empty greeting -> fact must SURVIVE backed by r2.note!
        self.app.ingest_service.edit_raw_note("qa_space", r1.note.note_id, "你好")
        surviving = self.app.memory_repo.find_by_canonical_key("qa_space", "fact:user:residence")
        self.assertIsNotNone(surviving)
        assert surviving is not None
        self.assertEqual(surviving.status, "active")
        self.assertEqual(surviving.payload["value"], "上海")
        self.assertEqual([e.note_id for e in surviving.evidences], [r2.note.note_id])

    # -------------------------------------------------------------------------
    # Basic-Memory-inspired Cases (TC-BM-01 .. TC-BM-04)
    # -------------------------------------------------------------------------

    def test_tc_bm_01_malformed_frontmatter_and_path_traversal_rejected(self) -> None:
        """Ref: basic-memory/test-int/test_locked_notes.py & markdown boundary guards."""
        self.app.ingest_service.record_note("qa_space", "我现在搬到杭州了")
        exp = self.app.markdown_sync.export_space("qa_space")
        space_root = Path(exp["export_dir"])

        # 1. Path traversal check on internal helper
        with self.assertRaises(ValidationError):
            self.app.markdown_sync._ensure_safe_child(space_root, "../../etc/passwd")

        # 2. Unclosed YAML frontmatter in edited Markdown file raises explicit ValidationError
        md_files = [p for p in space_root.rglob("*.md") if p.name != "index.md"]
        self.assertEqual(len(md_files), 1)
        md_files[0].write_text("---\ntitle: [unclosed_yaml\nBody without closing fence\n", encoding="utf-8")
        with self.assertRaises(ValidationError):
            self.app.markdown_sync.sync_external_edits("qa_space")

    def test_tc_bm_02_locked_card_rejects_background_auto_overwrite_and_keeps_version(self) -> None:
        """Ref: basic-memory/test-int/test_locked_notes.py::test_locked_note_rejects_append."""
        n = self.app.ingest_service.record_note("qa_space", "我喜欢用机械键盘写代码").note
        locked_res = self.app.change_service.apply(
            ChangeProposal(
                space_id="qa_space",
                action="create",
                memory_type="semantic",
                title="核心发布手册规范",
                summary="发版前必须跑通全量回归套件",
                canonical_key="doc:release_runbook",
                payload={"subject": "runbook", "predicate": "rule", "value": "必须全量回归"},
                lock_for_user=True,
                evidences=(MemoryEvidence(note_id=n.note_id, note_revision=1, quote="规范"),),
            )
        )
        card = locked_res.memory
        assert card is not None
        self.assertTrue(card.user_locked)
        self.assertEqual(card.version, 1)

        # Background extractor / dream consolidator attempting to overwrite locked card is blocked
        attempt = self.app.change_service.apply(
            ChangeProposal(
                space_id="qa_space",
                action="update",
                memory_type="semantic",
                target_memory_id=card.memory_id,
                origin="dream_consolidator",
                summary="试图覆盖锁定规范",
                payload={"subject": "runbook", "predicate": "rule", "value": "无需测试直接发版"},
            )
        )
        self.assertEqual(attempt.status, "skipped_user_locked")
        after = self.app.memory_repo.get("qa_space", card.memory_id)
        assert after is not None
        self.assertEqual(after.version, 1)
        self.assertEqual(after.payload["value"], "必须全量回归")

    def test_tc_bm_03_duplicate_titles_auto_disambiguate_unique_permalinks(self) -> None:
        """Ref: basic-memory/test-int/test_mandatory_permalinks_integration.py."""
        c1 = self.app.change_service.apply(
            ChangeProposal(
                space_id="qa_space",
                action="create",
                memory_type="semantic",
                title="架构设计纪要",
                summary="第一期检索层纪要",
                canonical_key="doc:arch_1",
                payload={"subject": "arch", "predicate": "phase", "value": "phase1"},
            )
        ).memory
        c2 = self.app.change_service.apply(
            ChangeProposal(
                space_id="qa_space",
                action="create",
                memory_type="semantic",
                title="架构设计纪要",
                summary="第二期存储层纪要",
                canonical_key="doc:arch_2",
                payload={"subject": "arch", "predicate": "phase", "value": "phase2"},
            )
        ).memory
        assert c1 is not None and c2 is not None
        self.assertNotEqual(c1.permalink, c2.permalink)
        self.assertTrue(c2.permalink.endswith("-2"))
        # Both are addressable via `memory://` URI
        r1 = self.app.retrieval_service.read_memory_detail("qa_space", c1.uri)
        r2 = self.app.retrieval_service.read_memory_detail("qa_space", c2.uri)
        self.assertEqual(r1["memory"]["summary"], "第一期检索层纪要")
        self.assertEqual(r2["memory"]["summary"], "第二期存储层纪要")

    def test_tc_bm_04_deleting_exported_markdown_file_never_forgets_canonical_memory(self) -> None:
        """Ref: FUSION_IMPLEMENTATION_PLAN §7.2 (deleting view file != forgetting memory)."""
        r = self.app.ingest_service.record_note("qa_space", "我平时喜欢喝乌龙茶")
        mem = r.changes[0].memory
        assert mem is not None

        exp = self.app.markdown_sync.export_space("qa_space")
        md_path = Path(exp["export_dir"]) / f"{mem.permalink}.md"
        self.assertTrue(md_path.is_file())

        # User deletes the exported `.md` file from disk and runs `sync`
        md_path.unlink()
        self.app.markdown_sync.sync_external_edits("qa_space")

        # Canonical memory in DB remains active AND `sync`'s re-export self-heals the missing view file!
        still_active = self.app.memory_repo.get("qa_space", mem.memory_id)
        self.assertIsNotNone(still_active)
        assert still_active is not None
        self.assertEqual(still_active.status, "active")
        self.assertTrue(md_path.is_file())

    # -------------------------------------------------------------------------
    # ReMe-inspired Cases (TC-REME-01 .. TC-REME-03)
    # -------------------------------------------------------------------------

    def test_tc_reme_01_dream_snapshot_digest_idempotency_zero_llm_calls_when_unchanged(self) -> None:
        """Ref: ReMe/tests/unit/test_auto_dream.py (_snapshot_digest idempotency)."""
        self.app.ingest_service.record_note("qa_space", "我喜欢喝美式咖啡")
        self.app.ingest_service.record_note("qa_space", "我喜欢读分布式系统论文")

        run1 = self.app.dream_service.run_dream("qa_space")
        self.assertEqual(run1["budget"]["used_llm_calls"], 1)
        self.assertEqual(run1["insights_processed"], 1)

        # Immediate second Dream run with zero underlying changes must consume 0 LLM calls!
        run2 = self.app.dream_service.run_dream("qa_space")
        self.assertEqual(run2["budget"]["used_llm_calls"], 0)
        self.assertEqual(run2["insights_processed"], 0)
        self.assertEqual(run2["skipped_unchanged"], 1)

    def test_tc_reme_02_dream_never_recursively_consolidates_inferred_cards(self) -> None:
        """Ref: Prevent model hallucination amplification loop across Dream cycles."""
        self.app.ingest_service.record_note("qa_space", "上周参加了技术架构分享会")
        self.app.ingest_service.record_note("qa_space", "昨天参加了季度产品复盘会")
        self.app.dream_service.run_dream("qa_space")

        # Add one unrelated stated fact -> Dream should NOT pair the earlier `inferred` card with it
        inferred_cards = [
            m
            for m in self.app.memory_repo.list_memories("qa_space")
            if m.assertion_kind == "inferred"
        ]
        self.assertEqual(len(inferred_cards), 1)
        run_again = self.app.dream_service.run_dream("qa_space")
        self.assertEqual(run_again["insights_processed"], 0)

    def test_tc_reme_03_dream_token_budget_circuit_breaker_halts_cleanly(self) -> None:
        """Ref: Token budget circuit breaker stops multi-cluster Dream before overspending."""
        self.app.ingest_service.record_note("qa_space", "上周参加了架构评审会")
        self.app.ingest_service.record_note("qa_space", "今天参加了故障复盘会")
        self.app.ingest_service.record_note("qa_space", "我喜欢喝手冲咖啡")
        self.app.ingest_service.record_note("qa_space", "我喜欢用双拼输入法")

        # Set a tight token ceiling (100 tokens) -> 1st cluster finishes (~150 tok) and halts before 2nd!
        res = self.app.dream_service.run_dream("qa_space", max_llm_calls=10, max_tokens=100)
        self.assertEqual(res["budget"]["used_llm_calls"], 1)
        self.assertTrue(res["budget"]["budget_halted"])
        self.assertIn("max_tokens", res["budget"]["halted_reason"])

    # -------------------------------------------------------------------------
    # Fusion Scenarios (TC-FUS-01 .. TC-FUS-02)
    # -------------------------------------------------------------------------

    def test_tc_fus_01_overdue_task_alert_and_natural_language_completion_loop(self) -> None:
        """End-to-end 4-Quadrant overdue detection -> natural language completion -> alert cleared."""
        from trace_memory.domain.notes import RawNote, new_id

        past_due = (datetime.now(timezone.utc) - timedelta(hours=5)).isoformat()
        src_note = RawNote(
            note_id=new_id("note"),
            space_id="qa_space",
            content="需要提交季度差旅报销单",
            source_type="cli",
        )
        self.app.note_repo.save(src_note)
        self.app.change_service.apply(
            ChangeProposal(
                space_id="qa_space",
                action="create",
                memory_type="task",
                title="提交季度差旅报销单",
                summary="整理发票并提交季度差旅报销单",
                canonical_key="task:self:r0:reimburse",
                payload={"task_status": "todo", "importance": 4, "urgency": 2, "due_at": past_due},
                evidences=(MemoryEvidence(note_id=src_note.note_id, note_revision=1, quote="提交季度差旅报销单"),),
            )
        )

        mat_before = self.app.task_matrix_service.build_matrix_analysis("qa_space")
        self.assertEqual(mat_before["summary_counts"]["overdue"], 1)
        self.assertEqual(mat_before["summary_counts"]["Q1_do_first"], 1)
        self.assertTrue(any("超过截止时间" in d for d in mat_before["diagnostics"]))

        # Complete via natural language sentence
        self.app.ingest_service.record_note("qa_space", "我已经把提交季度差旅报销单搞定了")
        mat_after = self.app.task_matrix_service.build_matrix_analysis("qa_space")
        self.assertEqual(mat_after["summary_counts"]["overdue"], 0)
        self.assertEqual(mat_after["summary_counts"]["total_todo"], 0)
        self.assertEqual(mat_after["summary_counts"]["total_done"], 1)

    def test_tc_fus_02_hybrid_rrf_recalls_both_exact_error_code_and_semantic_paraphrase(self) -> None:
        """Verify BM25 exact identifier hit + Dense Vector semantic paraphrase hit on the same item."""
        self.app.ingest_service.record_note(
            "qa_space", "今天必须排查支付网关 ERR_504_GATEWAY 超时故障"
        )

        # 1. Exact error code lookup -> BM25 rank #1
        exact_res = self.app.retrieval_service.search("qa_space", "ERR_504_GATEWAY")
        self.assertEqual(len(exact_res["memories"]), 1)
        self.assertEqual(exact_res["memories"][0]["bm25_rank"], 1)

        # 2. Pure semantic paraphrase with zero shared words -> Dense Vector rank #1
        sem_res = self.app.retrieval_service.search("qa_space", "线上结算异常隐患")
        self.assertEqual(len(sem_res["memories"]), 1)
        self.assertEqual(sem_res["memories"][0]["vector_rank"], 1)


if __name__ == "__main__":
    unittest.main()
