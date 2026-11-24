"""Shutdown must never let a later job commit ahead of an earlier, still-running job (same space)."""

from __future__ import annotations

from pathlib import Path
import tempfile
import threading
import time
import unittest

from trace_memory.config import TraceMemoryConfig
from trace_memory.container import AppContainer
from trace_memory.llm.mock_provider import DeterministicLLMProvider


class _GatedFirstLLM(DeterministicLLMProvider):
    """Note A blocks inside the LLM call until released; every other note returns at once."""

    def __init__(self) -> None:
        super().__init__()
        self.release_a = threading.Event()
        self.a_started = threading.Event()
        self.b_done = threading.Event()

    def extract_candidates(self, note_text: str, default_event_time: str, existing_memories=None):
        if "模块A" in note_text:
            self.a_started.set()
            self.release_a.wait(timeout=10)
        res = super().extract_candidates(note_text, default_event_time, existing_memories)
        if "模块B" in note_text:
            self.b_done.set()
        return res


class IngestShutdownOrderingTest(unittest.TestCase):
    def test_waiting_later_job_is_requeued_not_committed_on_shutdown(self) -> None:
        llm = _GatedFirstLLM()
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            app = AppContainer.build(
                TraceMemoryConfig(
                    default_space="stop_sp",
                    data_dir=base / "data",
                    knowledge_dir=base / "know",
                    ingest_worker_count=4,
                    ingest_space_concurrency=4,
                    rate_limit_per_minute=200,
                    ingest_pipeline="v1",
                ),
                llm_override=llm,
            )
            try:
                commit_order: list[str] = []
                orig_apply = app.change_service.apply

                def _tracking_apply(proposal, **kwargs):
                    commit_order.append(proposal.title)
                    return orig_apply(proposal, **kwargs)

                app.change_service.apply = _tracking_apply  # type: ignore[method-assign]
                app.ingest_worker.start()
                res_a = app.ingest_service.enqueue_note("stop_sp", "明天必须推进模块A联调方案")
                app.ingest_worker.notify()
                self.assertTrue(llm.a_started.wait(timeout=3))
                res_b = app.ingest_service.enqueue_note("stop_sp", "周五必须提交模块B压测报告")
                app.ingest_worker.notify()
                self.assertTrue(llm.b_done.wait(timeout=3))
                time.sleep(0.1)  # B is now parked in the commit barrier behind A
                self.assertEqual(commit_order, [])

                stopper = threading.Thread(target=app.ingest_worker.stop, kwargs={"timeout": 3.0})
                stopper.start()
                time.sleep(0.4)  # B sees the stop signal while A is still inside the LLM call
                self.assertEqual(commit_order, [], "B must not commit ahead of A during shutdown")
                job_b = app.job_repo.get_job(res_b.job_id)
                assert job_b is not None
                self.assertEqual(job_b["status"], "queued")
                self.assertEqual(job_b["attempts"], 0, "shutdown requeue must not consume a retry")

                llm.release_a.set()  # A is free to commit even during shutdown
                stopper.join(timeout=5)
                job_a = app.job_repo.get_job(res_a.job_id)
                assert job_a is not None
                self.assertEqual(job_a["status"], "completed")
                self.assertTrue(commit_order and "模块A" in commit_order[0])
                self.assertFalse(any("模块B" in t for t in commit_order))

                # After restart B is processed normally, still after A
                app.ingest_worker.start()
                self.assertTrue(app.ingest_worker.wait_for_space_idle("stop_sp", timeout=5.0))
                self.assertIn("模块B", commit_order[-1])
                job_b2 = app.job_repo.get_job(res_b.job_id)
                assert job_b2 is not None
                self.assertEqual(job_b2["status"], "completed")
            finally:
                app.close()


if __name__ == "__main__":
    unittest.main()
