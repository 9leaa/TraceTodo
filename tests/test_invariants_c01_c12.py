"""End-to-end verification of Core Business Invariants C01 through C12."""

from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from trace_memory.config import TraceMemoryConfig
from trace_memory.container import AppContainer
from trace_memory.domain.proposals import (
    ChangeProposal,
    PermissionDeniedError,
    VersionConflictError,
)
from trace_memory.llm.mock_provider import DeterministicLLMProvider


def _make_container(tmp_dir: str, **overrides) -> AppContainer:
    base = Path(tmp_dir)
    cfg = TraceMemoryConfig(
        default_space="alice",
        data_dir=base / "data",
        knowledge_dir=base / "knowledge",
    )
    return AppContainer.build(cfg, **overrides)


class TestInvariantsC01ToC12(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.app = _make_container(self._tmp.name)

    def tearDown(self) -> None:
        self.app.close()
        self._tmp.cleanup()

    def test_c01_causal_ordering_late_old_history_never_overwrites_current_state(self) -> None:
        # 1. User lived in Beijing in 2025, moved to Shanghai in Oct 2026
        self.app.ingest_service.record_note(
            "alice", "我住在北 京".replace(" ", ""), event_time="2025-05-01T10:00:00+00:00"
        )
        self.app.ingest_service.record_note(
            "alice", "我现在搬到上海了", event_time="2026-10-01T10:00:00+00:00"
        )
        # 2. Later, user backfills an even older 2023 memory of living in Shenzhen
        res_backfill = self.app.ingest_service.record_note(
            "alice", "2023年的时候我住在深圳", event_time="2023-06-01T10:00:00+00:00"
        )
        self.assertEqual(res_backfill.changes[0].status, "skipped_older_event")

        mem = self.app.memory_repo.find_by_canonical_key("alice", "fact:user:residence")
        self.assertIsNotNone(mem)
        assert mem is not None
        self.assertEqual(mem.payload["value"], "上海")
        self.assertIn("北京", mem.payload["previous_values"])
        self.assertIn("深圳", mem.payload["previous_values"])

    def test_c02_raw_note_revision_invalidates_dependent_facts_and_dreams(self) -> None:
        r1 = self.app.ingest_service.record_note("alice", "我现在住在杭州")
        note_id = r1.note.note_id
        mem_before = self.app.memory_repo.find_by_canonical_key("alice", "fact:user:residence")
        self.assertIsNotNone(mem_before)

        # Replace raw note with content carrying no extractable fact
        self.app.ingest_service.edit_raw_note("alice", note_id, "哈哈")
        active_after = self.app.memory_repo.find_by_canonical_key(
            "alice", "fact:user:residence", statuses=("active",)
        )
        self.assertIsNone(active_after)

        # Vector index must also be purged so stale Hangzhou fact cannot be recalled
        hits = self.app.retrieval_service.search("alice", "杭州")
        self.assertEqual(len(hits["memories"]), 0)

    def test_c03_preference_preserves_positive_polarity_and_exceptions(self) -> None:
        self.app.ingest_service.record_note("alice", "我很喜欢喝咖啡，但是晚上八点后不喝")
        prefs = self.app.memory_repo.list_memories("alice", memory_type="preference")
        self.assertEqual(len(prefs), 1)
        pref = prefs[0]
        self.assertEqual(pref.payload["polarity"], "positive")
        self.assertTrue(any("晚上" in ex for ex in pref.payload["exceptions"]))

    def test_c04_two_state_task_protocol_and_no_silent_reopen(self) -> None:
        r = self.app.ingest_service.record_note("alice", "明天必须提交季度周报")
        task = r.changes[0].memory
        assert task is not None
        self.assertEqual(task.payload["task_status"], "todo")

        # Complete task
        done_res = self.app.task_matrix_service.transition_task_state(
            "alice", task.memory_id, "complete_task", expected_version=task.version
        )
        assert done_res.memory is not None
        self.assertEqual(done_res.memory.payload["task_status"], "done")
        self.assertEqual(done_res.memory.payload["close_reason"], "completed")

        # Ordinary update must NOT silently reopen a `done` task
        upd_res = self.app.change_service.apply(
            ChangeProposal(
                space_id="alice",
                action="update",
                memory_type="task",
                target_memory_id=task.memory_id,
                expected_version=done_res.memory.version,
                payload={"task_status": "todo", "priority_reason": "attempt silent reopen"},
            )
        )
        assert upd_res.memory is not None
        self.assertEqual(upd_res.memory.payload["task_status"], "done")

        # Explicit reopen_task DOES reopen
        reopen_res = self.app.task_matrix_service.transition_task_state(
            "alice", task.memory_id, "reopen_task", expected_version=upd_res.memory.version
        )
        assert reopen_res.memory is not None
        self.assertEqual(reopen_res.memory.payload["task_status"], "todo")
        self.assertEqual(reopen_res.memory.payload["close_reason"], "")

    def test_c05_distinct_assignees_and_rounds_do_not_false_merge(self) -> None:
        self.app.ingest_service.record_note("alice", "我负责整理接口压测报告")
        self.app.ingest_service.record_note("alice", "小王负责整理接口压测报告")
        self.app.ingest_service.record_note("alice", "完成第一轮系统回归测试")
        self.app.ingest_service.record_note("alice", "完成第二轮系统回归测试")

        tasks = self.app.memory_repo.list_memories("alice", memory_type="task")
        self.assertEqual(len(tasks), 4)

    def test_c06_user_locked_card_and_rejected_inference_are_protected(self) -> None:
        r = self.app.ingest_service.record_note("alice", "有空整理技术博客文章")
        task = r.changes[0].memory
        assert task is not None

        # User manually locks task into Q1
        locked = self.app.task_matrix_service.update_task_quadrant_or_priority(
            "alice", task.memory_id, expected_version=task.version, quadrant="Q1_do_first"
        ).memory
        assert locked is not None
        self.assertTrue(locked.user_locked)
        self.assertEqual(locked.payload["quadrant"], "Q1_do_first")

        # Subsequent automatic extraction on same task must NOT overwrite user-locked Q1
        r2 = self.app.ingest_service.record_note("alice", "有空整理技术博客文章")
        self.assertEqual(r2.changes[0].status, "skipped_user_locked")
        reloaded = self.app.memory_repo.get("alice", task.memory_id)
        assert reloaded is not None
        self.assertEqual(reloaded.payload["quadrant"], "Q1_do_first")

    def test_c07_partial_span_forget_purges_target_while_preserving_sibling_memory(self) -> None:
        r = self.app.ingest_service.record_note(
            "alice", "我住在上海。平时喜欢喝拿铁咖啡。"
        )
        self.assertEqual(len(r.changes), 2)

        # Partially forget ONLY "上海", keeping coffee preference alive
        self.app.ingest_service.forget_note("alice", r.note.note_id, span_to_forget="上海")

        # Residence memory must be forgotten & unsearchable
        res_search = self.app.retrieval_service.search("alice", "上海")
        self.assertEqual(len(res_search["memories"]), 0)
        for nh in res_search["notes"]:
            self.assertNotIn("上海", nh["note"]["content"])

        # Export must not contain forgotten content either
        self.app.markdown_sync.export_space("alice")
        for md_file in (self.app.config.knowledge_dir / "alice").rglob("*.md"):
            self.assertNotIn("上海", md_file.read_text(encoding="utf-8"))

        # Coffee preference from the same note remains active!
        coffee_hits = self.app.retrieval_service.search("alice", "拿铁咖啡")
        self.assertGreaterEqual(len(coffee_hits["memories"]), 1)

    def test_c08_idempotency_replay_and_cas_version_conflict(self) -> None:
        r1 = self.app.ingest_service.record_note(
            "alice", "明天必须修复登录超时 bug", idempotency_key="msg-001"
        )
        r2 = self.app.ingest_service.record_note(
            "alice", "明天必须修复登录超时 bug", idempotency_key="msg-001"
        )
        self.assertEqual(r2.extraction_status, "idempotent_replay")
        tasks = self.app.memory_repo.list_memories("alice", memory_type="task")
        self.assertEqual(len(tasks), 1)

        mem = tasks[0]
        # Advance to v2
        self.app.task_matrix_service.update_task_quadrant_or_priority(
            "alice", mem.memory_id, expected_version=1, quadrant="Q2_schedule"
        )
        # Stale edit presenting expected_version=1 must raise VersionConflictError
        with self.assertRaises(VersionConflictError) as ctx:
            self.app.task_matrix_service.update_task_quadrant_or_priority(
                "alice", mem.memory_id, expected_version=1, quadrant="Q4_eliminate"
            )
        self.assertEqual(ctx.exception.actual, 2)

    def test_c09_strict_cross_space_isolation_without_content_leakage(self) -> None:
        secret = self.app.ingest_service.record_note(
            "alice", "我的私人银行卡尾号秘密记录是 9981"
        )
        alice_mem = secret.changes[0].memory
        assert alice_mem is not None

        # Bob searching cannot see Alice's memories or notes
        bob_search = self.app.retrieval_service.search("bob", "银行卡 9981")
        self.assertEqual(len(bob_search["memories"]), 0)
        self.assertEqual(len(bob_search["notes"]), 0)

        # Bob probing Alice's memory_id gets PermissionDeniedError with zero secret text leaked
        with self.assertRaises(PermissionDeniedError) as err:
            self.app.retrieval_service.read_memory_detail("bob", alice_mem.memory_id)
        self.assertNotIn("9981", str(err.exception))

    def test_c10_llm_failure_preserves_raw_note_and_reports_degraded_honestly(self) -> None:
        class _BrokenLLM(DeterministicLLMProvider):
            def extract_candidates(self, note_text: str, default_event_time: str):
                raise RuntimeError("Upstream LLM 503 Unavailable")

        degraded_app = _make_container(self._tmp.name, llm_override=_BrokenLLM())
        try:
            res = degraded_app.ingest_service.record_note("bob", "明天提醒我续费服务器")
            self.assertEqual(res.extraction_status, "degraded_error")
            self.assertIn("503", res.error_message)
            # RawNote is still safely persisted!
            saved_note = degraded_app.note_repo.get("bob", res.note.note_id)
            self.assertIsNotNone(saved_note)
        finally:
            degraded_app.close()

    def test_c12_ask_current_vs_history_vs_no_answer_and_read_only_safety(self) -> None:
        self.app.ingest_service.record_note(
            "alice", "我住在成都", event_time="2024-02-01T00:00:00+00:00"
        )
        self.app.ingest_service.record_note(
            "alice", "我现在搬到上海了", event_time="2026-09-01T00:00:00+00:00"
        )
        r_task = self.app.ingest_service.record_note("alice", "明天必须提交架构评审文档")
        task_id = r_task.changes[0].memory.memory_id  # type: ignore[union-attr]

        # 1. Current question -> answers Shanghai
        ans_cur = self.app.ask_service.ask("alice", "我现在住在哪个城市？")
        self.assertEqual(ans_cur["query_mode"], "current")
        self.assertTrue(ans_cur["has_sufficient_evidence"])
        self.assertIn("上海", ans_cur["answer"])

        # 2. History question -> includes version evolution
        ans_hist = self.app.ask_service.ask("alice", "我以前的居住城市历史变迁是什么？")
        self.assertEqual(ans_hist["query_mode"], "history")
        self.assertIn("v1", ans_hist["answer"])

        # 3. Unanswerable question -> refuses honestly without hallucinating
        ans_none = self.app.ask_service.ask("alice", "我养的宠物猫叫什么名字？")
        self.assertFalse(ans_none["has_sufficient_evidence"])

        # 4. Disguised imperative command inside `ask` NEVER mutates task state
        self.app.ask_service.ask("alice", "架构评审文档已经完成了")
        still_todo = self.app.memory_repo.get("alice", task_id)
        assert still_todo is not None
        self.assertEqual(still_todo.payload["task_status"], "todo")


if __name__ == "__main__":
    unittest.main()
