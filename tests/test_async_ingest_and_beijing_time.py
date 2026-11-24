"""Regression & concurrency tests for Beijing Time unification and Async Ingest Queue."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import tempfile
import threading
import time
import unittest

from trace_memory.api.app import ApiDispatcher
from trace_memory.config import TraceMemoryConfig
from trace_memory.container import AppContainer
from trace_memory.domain.memories import parse_iso_datetime
from trace_memory.domain.notes import (
    BEIJING_TZ,
    beijing_now_iso,
    beijing_today_date,
    format_beijing_prompt_clock,
    utc_now_iso,
)
from trace_memory.llm.mock_provider import DeterministicLLMProvider
from trace_memory.llm.openai_compat import OpenAICompatLLMProvider


class ControlledLLMProvider(DeterministicLLMProvider):
    """Test double that tracks per-space concurrency, injects delays/gates, and simulates 429s."""

    provider_name = "controlled_mock"

    def __init__(self, delay_seconds: float = 0.05) -> None:
        super().__init__()
        self.delay_seconds = delay_seconds
        self.lock = threading.Lock()
        self.active_by_tag: dict[str, int] = {}
        self.max_concurrent_global = 0
        self.current_global = 0
        self.execution_order: list[str] = []
        self.gate_note_substring: str = ""
        self.entered_gate = threading.Event()
        self.release_gate = threading.Event()
        self.fail_once_substring: str = ""
        self._failed_once = False
        self.seen_ask_notes: list[str] = []

    def extract_candidates(self, note_text: str, default_event_time: str, existing_memories=None):
        with self.lock:
            self.current_global += 1
            self.max_concurrent_global = max(self.max_concurrent_global, self.current_global)
            self.execution_order.append(note_text)
        try:
            if self.gate_note_substring and self.gate_note_substring in note_text:
                self.entered_gate.set()
                self.release_gate.wait(timeout=5.0)
            if self.fail_once_substring and self.fail_once_substring in note_text:
                with self.lock:
                    if not self._failed_once:
                        self._failed_once = True
                        raise RuntimeError("LLM API HTTP 429: rate limit exceeded")
            if self.delay_seconds > 0:
                time.sleep(self.delay_seconds)
            return super().extract_candidates(note_text, default_event_time, existing_memories)
        finally:
            with self.lock:
                self.current_global -= 1

    def synthesize_answer(self, question, query_mode, memories, notes, history_versions):
        self.seen_ask_notes = [n.visible_content() for n in notes]
        return super().synthesize_answer(question, query_mode, memories, notes, history_versions)


class TestBeijingTimeAndAsyncIngest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        self.cfg = TraceMemoryConfig(
            data_dir=root / "data",
            knowledge_dir=root / "knowledge",
            auth_required=False,
            ingest_worker_count=4,
            llm_max_concurrency=4,
            ingest_max_retries=3,
            rate_limit_per_minute=200,
            default_daily_note_limit=100,
            default_daily_llm_limit=100,
        )
        self.llm = ControlledLLMProvider(delay_seconds=0.04)
        self.app = AppContainer.build(self.cfg, llm_override=self.llm)
        self.app.ingest_worker.retry_base_delay_seconds = 0.02
        self.api = ApiDispatcher(self.app)

    def tearDown(self) -> None:
        self.app.close()
        self._tmp.cleanup()

    def test_beijing_timezone_consistency_and_prompt_anchors(self) -> None:
        # 1. All default backend timestamps are anchored to +08:00
        self.assertTrue(beijing_now_iso().endswith("+08:00"))
        self.assertTrue(utc_now_iso().endswith("+08:00"))

        # 2. UTC 17:30 on Nov 18 is ALREADY Nov 19 01:30 in Beijing Time!
        utc_night = datetime(2026, 11, 18, 17, 30, 0, tzinfo=timezone.utc)
        self.assertEqual(beijing_today_date(utc_night), "2026-11-19")
        anchor = format_beijing_prompt_clock(utc_night)
        self.assertIn("2026-11-19 01:30:00", anchor)
        self.assertIn("昨天=2026-11-18", anchor)
        self.assertIn("今天=2026-11-19", anchor)
        self.assertIn("明天=2026-11-20", anchor)
        self.assertIn("北京时间", anchor)

        # 3. Naive ISO and plain YYYY-MM-DD parse into Beijing Time (+08:00)
        p_date = parse_iso_datetime("2026-11-19")
        assert p_date is not None
        self.assertEqual(p_date.tzinfo, BEIJING_TZ)
        self.assertEqual(p_date.isoformat(), "2026-11-19T23:59:59+08:00")

        p_naive = parse_iso_datetime("2026-11-19T09:00:00")
        assert p_naive is not None
        self.assertEqual(p_naive.isoformat(), "2026-11-19T09:00:00+08:00")

        # 4. Auto-rollover triggers at 00:30 Beijing Time (16:30 UTC previous day)
        self.app.task_matrix_service.save_task_card(
            space_id="tz_space",
            data={
                "title": "验证北京时间零点自动顺延",
                "due_at": "2026-11-18T23:59:59+08:00",
                "auto_rollover": True,
                "is_all_day": True,
            },
        )
        bj_after_midnight_in_utc = datetime(2026, 11, 18, 16, 30, 0, tzinfo=timezone.utc)
        rolled = self.app.task_matrix_service.apply_auto_rollover(
            "tz_space", now=bj_after_midnight_in_utc
        )
        self.assertEqual(len(rolled), 1)
        self.assertTrue(rolled[0]["payload"]["due_at"].startswith("2026-11-19T23:59:59+08:00"))
        self.assertEqual(rolled[0]["payload"]["last_rollover_date"], "2026-11-19")

        # 5. OpenAICompatLLMProvider prompts embed Beijing Time anchor & normalize naive outputs to +08:00
        captured: list[tuple[str, str]] = []
        provider = OpenAICompatLLMProvider(
            base_url="https://example.invalid/v1",
            api_key="test-key",
            model="test-model",
        )

        def _fake_chat_json(sys_p: str, usr_p: str):
            captured.append((sys_p, usr_p))
            return (
                {
                    "candidates": [
                        {
                            "memory_type": "task",
                            "title": "明天交周报",
                            "summary": "明天提交周报",
                            "canonical_key": "task:weekly-report",
                            "mutation_hint": "upsert",
                            "payload": {
                                "task_status": "todo",
                                "importance": 4,
                                "urgency": 4,
                                "priority_reason": "截止临近",
                                "due_at": "2026-11-20",
                                "assignee": "self",
                            },
                            "event_time": "2026-11-19T01:30:00",
                            "quote": "明天交周报",
                        }
                    ]
                },
                42,
            )

        provider._chat_json = _fake_chat_json  # type: ignore[method-assign]
        batch = provider.extract_candidates("明天交周报", utc_night.isoformat())
        self.assertEqual(len(captured), 1)
        sys_p, usr_p = captured[0]
        self.assertIn("北京时间", sys_p)
        self.assertIn("+08:00", sys_p)
        self.assertIn("今天=2026-11-19", usr_p)
        self.assertIn("明天=2026-11-20", usr_p)
        self.assertEqual(batch.candidates[0].event_time, "2026-11-19T01:30:00+08:00")
        self.assertEqual(batch.candidates[0].payload["due_at"], "2026-11-20T23:59:59+08:00")

    def test_async_fast_ack_per_space_serial_order_and_burst_quota_protection(self) -> None:
        # Track strict per-space serialization
        in_flight_same_space = 0
        max_same_space = 0
        orig_extract = self.llm.extract_candidates

        def _tracking_extract(note_text, default_event_time, existing_memories=None):
            nonlocal in_flight_same_space, max_same_space
            in_flight_same_space += 1
            max_same_space = max(max_same_space, in_flight_same_space)
            try:
                return orig_extract(note_text, default_event_time, existing_memories)
            finally:
                in_flight_same_space -= 1

        self.llm.extract_candidates = _tracking_extract  # type: ignore[method-assign]
        self.llm.delay_seconds = 0.08
        self.app.ingest_worker.start()

        # Burst-send 2 dependent notes in the same space back-to-back
        t0 = time.perf_counter()
        c1, r1 = self.api.dispatch(
            "POST",
            "/api/notes/record",
            body={
                "space_id": "burst_user",
                "content": "明天必须提交季度财务复盘报告",
                "async_mode": True,
            },
        )
        c2, r2 = self.api.dispatch(
            "POST",
            "/api/notes/record",
            body={
                "space_id": "burst_user",
                "content": "季度财务复盘报告已经提交完成了",
                "async_mode": True,
            },
        )
        enqueue_ms = (time.perf_counter() - t0) * 1000.0
        self.assertEqual(c1, 202, r1)
        self.assertEqual(c2, 202, r2)
        self.assertEqual(r1["extraction_status"], "queued")
        self.assertEqual(r2["extraction_status"], "queued")
        self.assertTrue(r1["job_id"])
        self.assertTrue(r2["job_id"])
        # Fast-Ack must return much faster than 2 * 80ms LLM extraction
        self.assertLess(enqueue_ms, 120.0)

        self.assertTrue(self.app.ingest_worker.wait_for_space_idle("burst_user", timeout=5.0))
        # v2 pipeline: same-space extraction may overlap (that is the speed-up), but commits stay
        # strictly FIFO per space, which the single "done" task below proves.
        self.assertLessEqual(max_same_space, 2)

        # Verify FIFO causal order: task created by note 1 was completed by note 2
        tasks = self.app.task_matrix_service.list_sorted_tasks("burst_user", task_status="all")
        self.assertEqual(len(tasks), 1)
        self.assertEqual(tasks[0]["payload"]["task_status"], "done")

        # Verify async quota reservation prevents burst bypass
        self.app.settings_store.update({"default_daily_note_limit": 5})
        for i in range(3):
            code_ok, body_ok = self.api.dispatch(
                "POST",
                "/api/notes/record",
                body={
                    "space_id": "burst_user",
                    "content": f"明天必须推进第{i + 10}号异步配额校验任务",
                    "async_mode": True,
                },
            )
            self.assertEqual(code_ok, 202, body_ok)
        # 2 + 3 = 5 notes already reserved; 6th burst request must be rejected immediately with 429
        code_over, body_over = self.api.dispatch(
            "POST",
            "/api/notes/record",
            body={
                "space_id": "burst_user",
                "content": "第6条超出限额的异步连发任务",
                "async_mode": True,
            },
        )
        self.assertEqual(code_over, 429, body_over)
        self.assertEqual(body_over["code"], "QUOTA_EXCEEDED")
        self.app.ingest_worker.wait_for_space_idle("burst_user", timeout=5.0)

    def test_cross_space_parallelism_fair_round_robin_and_transient_retry(self) -> None:
        self.llm.delay_seconds = 0.06
        self.llm.fail_once_substring = "触发一次429重试"

        # Enqueue 5 jobs for heavy_user BEFORE 1 job for light_user_a and light_user_b
        for i in range(5):
            self.app.ingest_service.enqueue_note(
                "heavy_user", f"明天必须完成重度用户的第{i + 1}项后台任务"
            )
        r_a = self.app.ingest_service.enqueue_note("light_user_a", "明天必须推进轻度用户A的紧急联调")
        r_b = self.app.ingest_service.enqueue_note(
            "light_user_b", "明天必须推进轻度用户B触发一次429重试的发布"
        )

        # Now start worker pool and observe fair scheduling + cross-space parallelism + retry
        self.app.ingest_worker.start()
        self.assertTrue(self.app.ingest_worker.wait_for_space_idle("light_user_a", timeout=5.0))
        self.assertTrue(self.app.ingest_worker.wait_for_space_idle("light_user_b", timeout=5.0))
        self.assertTrue(self.app.ingest_worker.wait_for_space_idle("heavy_user", timeout=5.0))

        # Multiple spaces ran concurrently (>1 global concurrency)
        self.assertGreaterEqual(self.llm.max_concurrent_global, 2)

        # Light user A was NOT starved behind all 5 heavy_user jobs
        idx_light_a = next(
            i for i, txt in enumerate(self.llm.execution_order) if "轻度用户A" in txt
        )
        idx_heavy_last = next(
            i for i, txt in enumerate(self.llm.execution_order) if "第5项后台任务" in txt
        )
        self.assertLess(idx_light_a, idx_heavy_last)

        # Transient 429 job succeeded on attempt 2
        job_b = self.app.job_repo.get_job(r_b.job_id)
        assert job_b is not None
        self.assertEqual(job_b["status"], "completed")
        self.assertEqual(job_b["attempts"], 2)
        job_a = self.app.job_repo.get_job(r_a.job_id)
        assert job_a is not None
        self.assertEqual(job_a["status"], "completed")

    def test_ask_read_your_own_writes_during_processing_and_midflight_forget_race(self) -> None:
        self.llm.delay_seconds = 0.0
        self.llm.gate_note_substring = "星巴克"
        self.app.ingest_worker.start()

        # 1. Submit note; worker pauses inside LLM extraction
        c_rec, rec = self.api.dispatch(
            "POST",
            "/api/notes/record",
            body={
                "space_id": "ryow_space",
                "content": "刚刚约好明天下午三点在星巴克和王总开产品评审会",
                "async_mode": True,
            },
        )
        self.assertEqual(c_rec, 202, rec)
        self.assertTrue(self.llm.entered_gate.wait(timeout=3.0))

        # Check ingest-status reports pending=1 while processing
        c_st, st = self.api.dispatch("GET", "/api/notes/ingest-status", {"space_id": ["ryow_space"]})
        self.assertEqual(c_st, 200)
        self.assertEqual(st["pending"], 1)

        # Ask immediately BEFORE extraction completes -> must see the pending raw note!
        c_ask, ask_res = self.api.dispatch(
            "POST",
            "/api/ask",
            body={"space_id": "ryow_space", "question": "明天和王总在哪里开会？"},
        )
        self.assertEqual(c_ask, 200, ask_res)
        self.assertGreaterEqual(ask_res["pending_ingest_count"], 1)
        self.assertTrue(
            any("[刚录入·整理中]" in s and "星巴克" in s for s in self.llm.seen_ask_notes),
            f"Expected pending raw note in ask context, got {self.llm.seen_ask_notes}",
        )
        self.assertIn("星巴克", ask_res["answer"])

        # Release first job and wait idle
        self.llm.release_gate.set()
        self.assertTrue(self.app.ingest_worker.wait_for_space_idle("ryow_space", timeout=5.0))

        # 2. Mid-flight forget race: user forgets note WHILE LLM is extracting -> zero ghost writes!
        self.llm.entered_gate.clear()
        self.llm.release_gate.clear()
        self.llm.gate_note_substring = "蓝鲸999"

        c_frec, frec = self.api.dispatch(
            "POST",
            "/api/notes/record",
            body={
                "space_id": "race_space",
                "content": "我现在搬到蓝鲸999秘密城市定居了",
                "async_mode": True,
            },
        )
        self.assertEqual(c_frec, 202, frec)
        note_id = frec["note"]["note_id"]
        job_id = frec["job_id"]
        self.assertTrue(self.llm.entered_gate.wait(timeout=3.0))

        # Forget while LLM is still mid-flight
        c_fg, _ = self.api.dispatch(
            "POST",
            "/api/notes/forget",
            body={"space_id": "race_space", "note_id": note_id},
        )
        self.assertEqual(c_fg, 200)

        # Now let LLM finish returning candidates
        self.llm.release_gate.set()
        done_job = self.app.ingest_worker.wait_for_job(job_id, timeout=5.0)
        assert done_job is not None
        self.assertEqual(done_job["status"], "cancelled")

        # Zero ghost memories and zero search hits
        mems = self.app.memory_repo.list_memories("race_space", status="active")
        self.assertEqual(len(mems), 0)
        _, search_res = self.api.dispatch(
            "GET", "/api/memories/search", {"space_id": ["race_space"], "q": ["蓝鲸999"]}
        )
        self.assertEqual(len(search_res["memories"]), 0)
        self.assertEqual(len(search_res["notes"]), 0)

    def test_startup_crash_recovery_and_fastapi_nonblocking_event_loop(self) -> None:
        # 1. Crash recovery: simulate an interrupted job stuck in `processing`
        enq = self.app.ingest_service.enqueue_note(
            "recover_space", "明天必须检查崩溃恢复后的持久化队列任务"
        )
        with self.app.db.transaction() as conn:
            conn.execute(
                "UPDATE ingest_jobs SET status = 'processing' WHERE job_id = ?",
                (enq.job_id,),
            )
        self.app.ingest_worker.start()
        recovered_job = self.app.ingest_worker.wait_for_job(enq.job_id, timeout=5.0)
        assert recovered_job is not None
        self.assertEqual(recovered_job["status"], "completed")
        tasks = self.app.task_matrix_service.list_sorted_tasks("recover_space", task_status="all")
        self.assertEqual(len(tasks), 1)
        self.app.ingest_worker.stop()

        # 2. FastAPI non-blocking check (when fastapi is installed)
        try:
            from fastapi.testclient import TestClient
            from trace_memory.api.app import create_fastapi_app
        except ImportError:
            return

        self.llm.delay_seconds = 0.25
        with TestClient(create_fastapi_app(self.app)) as client:
            h = client.get("/api/health")
            self.assertEqual(h.status_code, 200)
            h_json = h.json()
            self.assertEqual(h_json["timezone"], "Asia/Shanghai (UTC+8)")
            self.assertTrue(h_json["server_time"].endswith("+08:00"))
            self.assertTrue(h_json["async_ingest_ready"])

            # Fire a slow sync ingest on one thread and verify /api/health stays fast on another thread
            sync_done = threading.Event()
            sync_status: list[int] = []

            def _run_slow_sync():
                resp = client.post(
                    "/api/notes/record?sync=1",
                    json={"space_id": "nb_space", "content": "明天必须完成同步慢接口非阻塞验证"},
                )
                sync_status.append(resp.status_code)
                sync_done.set()

            t = threading.Thread(target=_run_slow_sync)
            t.start()
            time.sleep(0.05)
            t_h0 = time.perf_counter()
            h2 = client.get("/api/health")
            health_ms = (time.perf_counter() - t_h0) * 1000.0
            t.join(timeout=5.0)

            self.assertEqual(h2.status_code, 200)
            self.assertEqual(sync_status, [200])
            self.assertLess(health_ms, 150.0)


if __name__ == "__main__":
    unittest.main()
