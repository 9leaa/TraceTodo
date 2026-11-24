"""v2 ingest pipeline: parallel extraction + ordered per-space commit pump.

Stage 1 (extract) runs on a large thread pool and only talks to the model; its artifact is persisted
as `ingest_jobs.status = 'extracted'` and the thread is released immediately.
Stage 2 (commit) is driven by `CommitPump`: whoever fills the gap at the head of a space's queue
commits every consecutive extracted job, each in ONE SQLite transaction. No thread ever waits for
an earlier job. Optional skip-ahead lets clearly unrelated messages pass a slow head after a deadline.

Public surface mirrors `IngestWorkerPool` (start / stop / notify / is_running / wait_for_space_idle /
wait_for_job / _stop_event) so the API layer and tests can use either implementation.
"""

from __future__ import annotations

from collections import deque
from concurrent.futures import ThreadPoolExecutor
import heapq
import random
import re
import threading
import time
from typing import Any, Callable

from trace_memory.services.ingest_queue import IngestJobRepository, _is_transient_llm_error


_IMAGE_ONLY_RE = re.compile(r"^\s*(\[图片记录\])?\s*$")


def _has_meaningful_text(text: str) -> bool:
    clean = re.sub(r"\[图片记录\]|\s", "", text or "")
    return len(clean) >= 2 and not _IMAGE_ONLY_RE.match(text or "")


class IngestPipelineV2:
    def __init__(
        self,
        job_repo: IngestJobRepository,
        ingest_service: Any,
        worker_count: int = 96,
        max_parallel_per_space: int = 8,
        llm_max_concurrency: int = 80,
        max_retries: int = 3,
        retry_base_delay_seconds: float = 0.35,
        commit_deadline_seconds: float = 10.0,
        vector_worker_count: int = 8,
        on_job_settled: Callable[[str, str], None] | None = None,
    ) -> None:
        self.job_repo = job_repo
        self.db = job_repo.db
        self.ingest_service = ingest_service
        self.worker_count = max(1, int(worker_count))
        self.max_parallel_per_space = max(1, int(max_parallel_per_space))
        self.extract_cap = max(1, min(self.worker_count, int(llm_max_concurrency)))
        self.max_retries = max(1, int(max_retries))
        self.retry_base_delay_seconds = max(0.01, float(retry_base_delay_seconds))
        self.commit_deadline_seconds = max(0.0, float(commit_deadline_seconds))
        self.vector_worker_count = max(1, int(vector_worker_count))
        self.on_job_settled = on_job_settled

        self._lock = threading.Lock()
        self._cond = threading.Condition(self._lock)
        self._stop_event = threading.Event()
        self._wake = False
        self._running = False
        self._in_flight: dict[str, int] = {}
        self._in_flight_total = 0
        self._last_scheduled: dict[str, float] = {}
        self._executor: ThreadPoolExecutor | None = None
        self._dispatcher: threading.Thread | None = None
        self._vec_threads: list[threading.Thread] = []
        self._vec_queue: deque[tuple[str, Callable[[], None]]] = deque()
        self._pending_vector_counts: dict[str, int] = {}

        # Commit pump state
        self._meta = threading.Lock()
        self._space_locks: dict[str, threading.Lock] = {}
        self._dirty: set[str] = set()
        self._committing: dict[str, int] = {}
        self._timers: list[tuple[float, str]] = []
        self._blocked_logged: set[str] = set()

    # ------------------------------------------------------------------ lifecycle
    @property
    def is_running(self) -> bool:
        with self._lock:
            return self._running

    def start(self) -> None:
        with self._lock:
            if self._running:
                return
            self._stop_event.clear()
            self.job_repo.recover_interrupted_jobs()
            self._running = True
            self._executor = ThreadPoolExecutor(
                max_workers=self.worker_count, thread_name_prefix="tm-ingest-extract"
            )
            self._dispatcher = threading.Thread(
                target=self._dispatch_loop, name="tm-ingest-dispatcher", daemon=True
            )
            self._vec_threads = [
                threading.Thread(target=self._vector_worker_loop, name=f"tm-vec-worker-{i}", daemon=True)
                for i in range(self.vector_worker_count)
            ]
            self._dispatcher.start()
            for t in self._vec_threads:
                t.start()
        # Extraction artifacts survive restarts: resume ordered commits right away
        for sp in self.job_repo.spaces_with_status("extracted"):
            self.kick(sp)

    def stop(self, timeout: float = 5.0) -> None:
        with self._lock:
            if not self._running:
                return
            self._running = False
            self._stop_event.set()
            self._wake = True
            self._cond.notify_all()
            dispatcher = self._dispatcher
            executor = self._executor
            vec_threads = list(self._vec_threads)
            self._dispatcher = None
            self._executor = None
            self._vec_threads = []
        deadline = time.monotonic() + timeout
        if dispatcher is not None:
            dispatcher.join(timeout=max(0.05, deadline - time.monotonic()))
        if executor is not None:
            executor.shutdown(wait=False, cancel_futures=True)
            while time.monotonic() < deadline:
                with self._lock:
                    if self._in_flight_total == 0:
                        break
                time.sleep(0.01)
        for t in vec_threads:
            t.join(timeout=max(0.05, deadline - time.monotonic()))

    def notify(self) -> None:
        with self._lock:
            self._wake = True
            self._cond.notify_all()

    # ------------------------------------------------------------------ waiting helpers
    def wait_for_space_idle(self, space_id: str, timeout: float = 10.0) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            st = self.job_repo.get_space_status(space_id)
            if st["pending"] == 0:
                with self._lock:
                    busy = self._in_flight.get(space_id, 0) or self._pending_vector_counts.get(space_id, 0)
                with self._meta:
                    busy = busy or self._committing.get(space_id, 0)
                if not busy:
                    return True
            time.sleep(0.015)
        return False

    def wait_for_job(self, job_id: str, timeout: float = 10.0) -> dict[str, Any] | None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            job = self.job_repo.get_job(job_id)
            if job is not None and job["status"] in ("completed", "failed", "cancelled"):
                return job
            time.sleep(0.015)
        return self.job_repo.get_job(job_id)

    # ------------------------------------------------------------------ stage 1: dispatch + extract
    def _dispatch_loop(self) -> None:
        try:
            while not self._stop_event.is_set():
                self._fire_due_timers()
                with self._lock:
                    free = self.extract_cap - self._in_flight_total
                    in_flight = dict(self._in_flight)
                    last = dict(self._last_scheduled)
                jobs: list[dict[str, Any]] = []
                if free > 0:
                    try:
                        jobs = self.job_repo.claim_batch(
                            free, in_flight, last, self.max_parallel_per_space
                        )
                    except Exception:
                        jobs = []
                executor = self._executor
                for job in jobs:
                    sp = job["space_id"]
                    with self._lock:
                        self._in_flight[sp] = self._in_flight.get(sp, 0) + 1
                        self._in_flight_total += 1
                        self._last_scheduled[sp] = time.monotonic()
                    try:
                        if executor is None:
                            raise RuntimeError("executor stopped")
                        executor.submit(self._run_extract, job)
                    except RuntimeError:
                        self._release(sp)
                        self.job_repo.requeue_on_shutdown(job["job_id"])
                if jobs and len(jobs) >= free:
                    continue  # pool saturated by this batch; loop to re-check capacity
                timeout = 1.0
                nxt = self._next_timer_ts()
                retry_ts = 0.0
                try:
                    retry_ts = self.job_repo.next_retry_ts()
                except Exception:
                    pass
                now = time.time()
                for ts in (nxt, retry_ts):
                    if ts > 0:
                        timeout = min(timeout, max(0.01, ts - now))
                with self._lock:
                    if not self._wake and not self._stop_event.is_set():
                        self._cond.wait(timeout=timeout)
                    self._wake = False
        finally:
            self.db.close_thread_connection()

    def _release(self, space_id: str) -> None:
        with self._lock:
            rem = self._in_flight.get(space_id, 0) - 1
            if rem <= 0:
                self._in_flight.pop(space_id, None)
            else:
                self._in_flight[space_id] = rem
            self._in_flight_total = max(0, self._in_flight_total - 1)
            self._wake = True
            self._cond.notify_all()

    def _run_extract(self, job: dict[str, Any]) -> None:
        sp = job["space_id"]
        job_id = job["job_id"]
        attempts = int(job.get("attempts", 1))
        settled: str | None = None
        try:
            art = self.ingest_service.extract_stage(
                sp,
                job["note_id"],
                raise_on_transient_llm_error=(attempts < self.max_retries),
                context_excludes=tuple(job.get("context_excludes") or ()),
            )
            self.job_repo.mark_extracted(job_id, art)
        except Exception as exc:
            if attempts < self.max_retries and _is_transient_llm_error(exc):
                backoff = min(8.0, self.retry_base_delay_seconds * (2 ** max(0, attempts - 1)))
                self.job_repo.schedule_retry(
                    job_id, backoff + random.uniform(0.0, 0.15), f"transient_retry_{attempts}: {exc}"
                )
            else:
                self.job_repo.mark_failed(job_id, str(exc))
                settled = "failed"
        finally:
            self._release(sp)
        if settled and self.on_job_settled is not None:
            try:
                self.on_job_settled(sp, settled)
            except Exception:
                pass
        self.kick(sp)

    # ------------------------------------------------------------------ stage 2: ordered commit pump
    def _space_lock(self, space_id: str) -> threading.Lock:
        with self._meta:
            lk = self._space_locks.get(space_id)
            if lk is None:
                lk = threading.Lock()
                self._space_locks[space_id] = lk
            return lk

    def kick(self, space_id: str) -> None:
        """Advance `space_id`'s commit head. Never blocks: a busy pump just gets marked dirty."""
        lock = self._space_lock(space_id)
        with self._meta:
            self._dirty.add(space_id)
        while True:
            if not lock.acquire(blocking=False):
                return
            try:
                while True:
                    with self._meta:
                        if space_id not in self._dirty:
                            break
                        self._dirty.discard(space_id)
                    try:
                        self._advance(space_id)
                    except Exception:
                        pass
            finally:
                lock.release()
            with self._meta:
                if space_id not in self._dirty:
                    return

    def _advance(self, space_id: str) -> None:
        if self._stop_event.is_set():
            return  # extracted artifacts persist; the next start() resumes ordered commits
        rows = self.job_repo.list_unsettled(space_id, limit=64)
        i = 0
        while i < len(rows) and rows[i]["status"] == "extracted":
            if not self._commit_one(rows[i], skipped_ahead=False):
                return  # head went back to the queue (stale context): everything behind it waits
            i += 1
        if i >= len(rows) or self.commit_deadline_seconds <= 0:
            return
        blockers: list[dict[str, Any]] = []
        now = time.time()
        for row in rows[i:]:
            if row["status"] != "extracted":
                blockers.append(row)
                continue
            ready_at = float(row.get("extracted_ts") or 0.0) + self.commit_deadline_seconds
            if now < ready_at:
                self._add_timer(ready_at, space_id)
                blockers.append(row)
                continue
            if self._may_skip_ahead(space_id, row, blockers) and self._commit_one(
                row, skipped_ahead=True
            ):
                continue
            else:
                self._log_blocked(space_id, row, blockers, now)
                blockers.append(row)

    def _may_skip_ahead(
        self, space_id: str, row: dict[str, Any], blockers: list[dict[str, Any]]
    ) -> bool:
        """Unrelated = earlier notes all have text, X only creates new cards, titles don't overlap."""
        if row.get("requires_barrier"):
            return False
        art = row.get("extraction") or {}
        cands = art.get("candidates", ()) if art.get("kind") == "ok" else ()
        for c in cands:
            if (c.get("relation") or "new") != "new" or c.get("refers_to_label"):
                return False
        coverage = getattr(self.ingest_service, "_title_coverage", None)
        for b in blockers:
            b_note = self.ingest_service.note_repo.get(space_id, b["note_id"], include_forgotten=True)
            if b_note is None or b_note.is_forgotten:
                continue  # will be cancelled; cannot conflict
            text = b_note.visible_content()
            if not _has_meaningful_text(text):
                return False  # image-only earlier note: always wait for its OCR/caption
            for c in cands:
                if coverage is not None and coverage(str(c.get("title", "")), text) >= 0.5:
                    return False
        return True

    def _log_blocked(
        self, space_id: str, row: dict[str, Any], blockers: list[dict[str, Any]], now: float
    ) -> None:
        if row["job_id"] in self._blocked_logged:
            return
        self._blocked_logged.add(row["job_id"])
        try:
            self.ingest_service.trace_repo.record_trace(
                space_id=space_id,
                operation="ingest_head_blocked",
                input_summary="",
                details={
                    "job_id": row["job_id"],
                    "blocked_by": [b["job_id"] for b in blockers[:5]],
                    "waited_s": round(now - float(row.get("extracted_ts") or now), 2),
                    "skipped_ahead": False,
                },
            )
        except Exception:
            pass

    def _commit_one(self, job: dict[str, Any], *, skipped_ahead: bool) -> bool:
        """Commit one extracted job. Returns False when it was sent back to the queue instead."""
        sp = job["space_id"]
        job_id = job["job_id"]
        note_id = job["note_id"]
        art = job.get("extraction") or {"kind": "error", "error": "missing extraction artifact"}
        svc = self.ingest_service
        with self._meta:
            self._committing[sp] = self._committing.get(sp, 0) + 1
        settled = "completed"
        res = None
        try:
            if art.get("kind") == "ok" and svc.stale_reference_in(sp, art):
                self.job_repo.back_to_queued(job_id, "stale_context: referenced note changed")
                self.notify()
                return False
            art = svc.prepare_references(sp, note_id, art)
            with self.db.transaction():
                res = svc.commit_artifact(
                    sp,
                    note_id,
                    art,
                    idempotency_key=str(job.get("idempotency_key", "") or ""),
                    count_note_in_quota=False,
                    defer_embeddings=True,
                    defer_error_embedding=True,
                )
                status = res.extraction_status
                if status == "cancelled_forgotten":
                    self.job_repo.mark_cancelled(job_id, res.error_message or "note_forgotten")
                    settled = "cancelled"
                else:
                    result: dict[str, Any] = {
                        "extraction_status": status,
                        "changes_count": len(res.changes),
                        "trace_id": res.trace_id,
                        "skipped_locked_count": sum(
                            1 for c in res.changes if c.status == "skipped_user_locked"
                        ),
                        "unresolved_reference_count": sum(
                            1 for c in res.changes if c.status == "unresolved_reference"
                        ),
                    }
                    if status == "degraded_error":
                        result["error_message"] = res.error_message
                    self.job_repo.mark_completed(job_id, result)
                if skipped_ahead:
                    self.job_repo.mark_skipped_ahead(job_id)
                    svc.trace_repo.record_trace(
                        space_id=sp,
                        operation="ingest_head_blocked",
                        input_summary="",
                        details={"job_id": job_id, "skipped_ahead": True},
                    )
        except Exception as exc:
            res = None
            settled = "failed"
            try:
                self.job_repo.mark_failed(job_id, f"commit_error: {exc}")
            except Exception:
                pass
        finally:
            with self._meta:
                rem = self._committing.get(sp, 0) - 1
                if rem <= 0:
                    self._committing.pop(sp, None)
                else:
                    self._committing[sp] = rem
        deferred = getattr(res, "deferred_vector_sync", None) if res is not None else None
        if callable(deferred):
            self._enqueue_vector(sp, deferred)
        if self.on_job_settled is not None:
            try:
                self.on_job_settled(sp, settled)
            except Exception:
                pass
        return True

    # ------------------------------------------------------------------ deadline timers
    def _add_timer(self, ts: float, space_id: str) -> None:
        with self._meta:
            heapq.heappush(self._timers, (ts, space_id))
        self.notify()

    def _next_timer_ts(self) -> float:
        with self._meta:
            return self._timers[0][0] if self._timers else 0.0

    def _fire_due_timers(self) -> None:
        due: list[str] = []
        now = time.time()
        with self._meta:
            while self._timers and self._timers[0][0] <= now:
                _, sp = heapq.heappop(self._timers)
                if sp not in due:
                    due.append(sp)
        for sp in due:
            self.kick(sp)

    # ------------------------------------------------------------------ deferred vectors
    def _enqueue_vector(self, space_id: str, fn: Callable[[], None]) -> None:
        with self._lock:
            self._pending_vector_counts[space_id] = self._pending_vector_counts.get(space_id, 0) + 1
            self._vec_queue.append((space_id, fn))
            self._cond.notify_all()

    def _vector_worker_loop(self) -> None:
        try:
            while True:
                item: tuple[str, Callable[[], None]] | None = None
                with self._lock:
                    while not self._vec_queue and not self._stop_event.is_set():
                        self._cond.wait(timeout=0.5)
                    if self._vec_queue:
                        item = self._vec_queue.popleft()
                    elif self._stop_event.is_set():
                        break
                if item is None:
                    continue
                sp, fn = item
                try:
                    fn()
                except Exception:
                    pass
                finally:
                    with self._lock:
                        rem = self._pending_vector_counts.get(sp, 0) - 1
                        if rem <= 0:
                            self._pending_vector_counts.pop(sp, None)
                        else:
                            self._pending_vector_counts[sp] = rem
                        self._cond.notify_all()
        finally:
            self.db.close_thread_connection()
