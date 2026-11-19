"""Tests for To-Do Sorting & Eisenhower 4-Quadrant Analysis (`TaskMatrixService`)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import tempfile
import unittest

from trace_memory.config import TraceMemoryConfig
from trace_memory.container import AppContainer


class TestTaskQuadrantsAndSorting(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        base = Path(self._tmp.name)
        self.app = AppContainer.build(
            TraceMemoryConfig(
                default_space="matrix_user",
                data_dir=base / "data",
                knowledge_dir=base / "knowledge",
            )
        )

    def tearDown(self) -> None:
        self.app.close()
        self._tmp.cleanup()

    def test_four_quadrant_classification_and_sorting_modes(self) -> None:
        # Q1: Important & Urgent
        self.app.ingest_service.record_note("matrix_user", "今天必须修复线上支付安全漏洞")
        # Q2: Important & Not Urgent
        self.app.ingest_service.record_note("matrix_user", "下个月规划核心架构演进方案")
        # Q3: Not Important & Urgent
        self.app.ingest_service.record_note("matrix_user", "今天下午帮同事转发会议链接")
        # Q4: Not Important & Not Urgent
        self.app.ingest_service.record_note("matrix_user", "有空的时候顺便整理旧书架")

        matrix = self.app.task_matrix_service.build_matrix_analysis("matrix_user")
        counts = matrix["summary_counts"]
        self.assertEqual(counts["total_todo"], 4)
        self.assertEqual(counts["Q1_do_first"], 1)
        self.assertEqual(counts["Q2_schedule"], 1)
        self.assertEqual(counts["Q3_delegate"], 1)
        self.assertEqual(counts["Q4_eliminate"], 1)

        # Priority sort puts Q1 first and Q4 last
        by_prio = self.app.task_matrix_service.list_sorted_tasks("matrix_user", sort_by="priority")
        self.assertEqual(by_prio[0]["payload"]["quadrant"], "Q1_do_first")
        self.assertEqual(by_prio[-1]["payload"]["quadrant"], "Q4_eliminate")

        # Importance sort puts high-importance items (Q1 & Q2) ahead of Q3
        by_imp = self.app.task_matrix_service.list_sorted_tasks("matrix_user", sort_by="importance")
        top2_quads = {by_imp[0]["payload"]["quadrant"], by_imp[1]["payload"]["quadrant"]}
        self.assertEqual(top2_quads, {"Q1_do_first", "Q2_schedule"})

        # Urgency sort puts urgent items (Q1 & Q3) ahead of Q2
        by_urg = self.app.task_matrix_service.list_sorted_tasks("matrix_user", sort_by="urgency")
        top2_urg_quads = {by_urg[0]["payload"]["quadrant"], by_urg[1]["payload"]["quadrant"]}
        self.assertEqual(top2_urg_quads, {"Q1_do_first", "Q3_delegate"})

    def test_dynamic_deadline_escalation_promotes_q2_to_q1_when_due_approaches(self) -> None:
        now = datetime(2026, 11, 16, 12, 0, tzinfo=timezone.utc)
        due_in_20_days = (now + timedelta(days=20)).isoformat()

        r = self.app.ingest_service.record_note(
            "matrix_user", "下个月完成核心模块设计评审", event_time=now.isoformat()
        )
        task = r.changes[0].memory
        assert task is not None

        # Set explicit due date 20 days out (unlocked auto-quadrant check)
        matrix_early = self.app.task_matrix_service.build_matrix_analysis("matrix_user", now=now)
        self.assertEqual(matrix_early["summary_counts"]["Q2_schedule"], 1)

        # Fast-forward clock to 12 hours before its due_at -> dynamically escalates to Q1!
        due_dt = datetime.fromisoformat(task.payload["due_at"])
        near_deadline = due_dt - timedelta(hours=12)
        matrix_near = self.app.task_matrix_service.build_matrix_analysis(
            "matrix_user", now=near_deadline
        )
        self.assertEqual(matrix_near["summary_counts"]["Q1_do_first"], 1)
        self.assertEqual(matrix_near["summary_counts"]["Q2_schedule"], 0)
        _ = due_in_20_days

    def test_preference_memory_calibrates_task_importance(self) -> None:
        # User states a strong preference for "向量检索"
        self.app.ingest_service.record_note("matrix_user", "我非常喜欢钻研向量检索算法")
        # Later records a neutrally phrased task mentioning "向量检索"
        r = self.app.ingest_service.record_note("matrix_user", "记得整理向量检索实验对比记录")
        task = r.changes[0].memory
        assert task is not None
        self.assertGreaterEqual(task.payload["importance"], 4)
        self.assertIn("偏好", task.payload["priority_reason"])

    def test_typo_and_llm_canonical_key_drift_still_completes_existing_quadrant_task(self) -> None:
        """Reproduces exact real-world bug: '告警' vs '告急' + LLM returning `upsert` with `task_status='done'`."""
        from trace_memory.llm.mock_provider import DeterministicLLMProvider
        from trace_memory.llm.protocol import ExtractedCandidate, ExtractionBatch

        # 1. Create initial task ("告警")
        self.app.ingest_service.record_note("matrix_user", "明天下午五点前必须把线上订单支付超时告警修好")
        self.assertEqual(
            self.app.task_matrix_service.build_matrix_analysis("matrix_user")["summary_counts"]["Q1_do_first"],
            1,
        )

        # 2. Simulate LLM returning a drifted canonical_key + typo "告急" + `mutation_hint="upsert"` with `task_status="done"`
        class _DriftedCompletionLLM(DeterministicLLMProvider):
            def extract_candidates(self, note_text, default_event_time, existing_memories=None):
                return ExtractionBatch(
                    candidates=(
                        ExtractedCandidate(
                            memory_type="task",
                            title="修复线上订单支付超时告急",
                            summary="用户已完成对线上订单支付超时问题的修复工作。",
                            canonical_key="task:fix_online_order_payment_timeout",
                            mutation_hint="upsert",
                            payload={"task_status": "done", "importance": 5, "urgency": 5},
                            event_time=default_event_time,
                            quote=note_text,
                        ),
                    ),
                    llm_calls=1,
                    tokens_used=50,
                )

        self.app.ingest_service.llm = _DriftedCompletionLLM()
        self.app.ingest_service.record_note("matrix_user", "我已修复线上订单支付超时告急")

        # Original task MUST be marked done and removed from active Todo quadrants (no duplicate card!)
        matrix_after = self.app.task_matrix_service.build_matrix_analysis("matrix_user")
        self.assertEqual(matrix_after["summary_counts"]["total_todo"], 0)
        self.assertEqual(matrix_after["summary_counts"]["Q1_do_first"], 0)
        self.assertEqual(matrix_after["summary_counts"]["total_done"], 1)


if __name__ == "__main__":
    unittest.main()
