"""Persistent SQLite Async Ingest Job Queue & Fair Per-Space Worker Pool.

Design goals:
- Fast-Ack (`< 5ms`) on `POST /api/notes/record` while persisting `raw_notes` + `ingest_jobs`.
- Crash-safe recovery on process startup (`processing` -> `queued`).
- Strict Per-Space FIFO serialization (eliminates same-user burst causal disorder & CAS storms).
- Full Cross-Space parallelism + Fair Round-Robin across active spaces (1 heavy user cannot starve 99 others).
- Global LLM concurrency semaphore + bounded exponential backoff retries on transient 429/5xx/timeouts.
"""

from __future__ import annotations

import json
import random
import threading
import time
from typing import Any, Callable

from trace_memory.domain.notes import beijing_now_iso, new_id
from trace_memory.storage.database import Database


def _is_transient_llm_error(exc: Exception) -> bool:
    msg = str(exc).lower()
    transient_markers = (
        "429",
        "rate limit",
        "too many requests",
        "http 500",
        "http 502",
        "http 503",
        "http 504",
        "timed out",
        "timeout",
        "temporarily unavailable",
        "connection reset",
        "remote end closed",
        "service unavailable",
    )
    return any(m in msg for m in transient_markers)


class IngestJobRepository:
    """SQLite-backed durable queue for asynchronous memory extraction jobs."""

    def __init__(self, db: Database) -> None:
        self.db = db

    def recover_interrupted_jobs(self) -> int:
        """Reset jobs left in `processing` from an abrupt crash/restart back to `queued`."""
        now = beijing_now_iso()
        with self.db.transaction() as conn:
            cur = conn.execute(
                """
                UPDATE ingest_jobs
                SET status = 'queued', updated_at = ?
                WHERE status = 'processing'
                """,
                (now,),
            )
            return int(cur.rowcount or 0)

    def enqueue(
        self,
        space_id: str,
        note_id: str,
        actor_id: str = "user",
        idempotency_key: str = "",
    ) -> dict[str, Any]:
        job_id = new_id("job")
        now = beijing_now_iso()
        with self.db.transaction() as conn:
            conn.execute(
                """
                INSERT INTO ingest_jobs
                    (job_id, space_id, note_id, actor_id, idempotency_key, status, attempts,
                     error_message, result_json, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, 'queued', 0, '', '{}', ?, ?)
                """,
                (job_id, space_id, note_id, actor_id, idempotency_key, now, now),
            )
        return {
            "job_id": job_id,
            "space_id": space_id,
            "note_id": note_id,
            "actor_id": actor_id,
            "idempotency_key": idempotency_key,
            "status": "queued",
            "attempts": 0,
            "error_message": "",
            "created_at": now,
            "updated_at": now,
        }

    def get_job(self, job_id: str) -> dict[str, Any] | None:
        row = self.db.query_one("SELECT * FROM ingest_jobs WHERE job_id = ?", (job_id,))
        if row is None:
            return None
        return self._row_to_dict(row)

    def cancel_jobs_for_note(
        self, space_id: str, note_id: str, reason: str = "note_forgotten"
    ) -> int:
        now = beijing_now_iso()
        with self.db.transaction() as conn:
            cur = conn.execute(
                """
                UPDATE ingest_jobs
                SET status = 'cancelled', error_message = ?, updated_at = ?
                WHERE space_id = ? AND note_id = ? AND status = 'queued'
                """,
                (reason, now, space_id, note_id),
            )
            return int(cur.rowcount or 0)

    def claim_next_fair_job(
        self,
        active_spaces: set[str],
        last_scheduled: dict[str, float],
    ) -> dict[str, Any] | None:
        """Atomically claim the oldest queued job for the least-recently-served idle space."""
        now = beijing_now_iso()
        with self.db.transaction() as conn:
            space_rows = conn.execute(
                "SELECT DISTINCT space_id FROM ingest_jobs WHERE status = 'queued'"
            ).fetchall()
            eligible = [
                str(r["space_id"])
                for r in space_rows
                if str(r["space_id"]) not in active_spaces
            ]
            if not eligible:
                return None
            # Fair Round-Robin: pick the space waiting the longest since its last scheduled turn
            chosen_space = min(eligible, key=lambda sp: last_scheduled.get(sp, 0.0))
            row = conn.execute(
                """
                SELECT * FROM ingest_jobs
                WHERE status = 'queued' AND space_id = ?
                ORDER BY created_at ASC, rowid ASC
                LIMIT 1
                """,
                (chosen_space,),
            ).fetchone()
            if row is None:
                return None
            cur = conn.execute(
                """
                UPDATE ingest_jobs
                SET status = 'processing', attempts = attempts + 1, updated_at = ?
                WHERE job_id = ? AND status = 'queued'
                """,
                (now, str(row["job_id"])),
            )
            if cur.rowcount != 1:
                return None
            claimed = self._row_to_dict(row)
            claimed["status"] = "processing"
            claimed["attempts"] = int(claimed["attempts"]) + 1
            claimed["updated_at"] = now
            return claimed

    def mark_completed(self, job_id: str, result: dict[str, Any]) -> None:
        now = beijing_now_iso()
        with self.db.transaction() as conn:
            conn.execute(
                """
                UPDATE ingest_jobs
                SET status = 'completed', error_message = '', result_json = ?, updated_at = ?
                WHERE job_id = ?
                """,
                (json.dumps(result, ensure_ascii=False), now, job_id),
            )

    def mark_cancelled(self, job_id: str, reason: str) -> None:
        now = beijing_now_iso()
        with self.db.transaction() as conn:
            conn.execute(
                """
                UPDATE ingest_jobs
                SET status = 'cancelled', error_message = ?, updated_at = ?
                WHERE job_id = ?
                """,
                (reason[:300], now, job_id),
            )

    def requeue_for_retry(self, job_id: str, error_message: str) -> None:
        now = beijing_now_iso()
        with self.db.transaction() as conn:
            conn.execute(
                """
                UPDATE ingest_jobs
                SET status = 'queued', error_message = ?, updated_at = ?
                WHERE job_id = ?
                """,
                (error_message[:300], now, job_id),
            )

    def mark_failed(self, job_id: str, error_message: str) -> None:
        now = beijing_now_iso()
        with self.db.transaction() as conn:
            conn.execute(
                """
                UPDATE ingest_jobs
                SET status = 'failed', error_message = ?, updated_at = ?
                WHERE job_id = ?
                """,
                (error_message[:500], now, job_id),
            )

    def list_pending_note_ids(self, space_id: str, limit: int = 5) -> list[str]:
        rows = self.db.query_all(
            """
            SELECT note_id FROM ingest_jobs
            WHERE space_id = ? AND status IN ('queued', 'processing')
            ORDER BY created_at DESC, rowid DESC
            LIMIT ?
            """,
            (space_id, max(1, int(limit))),
        )
        return [str(r["note_id"]) for r in rows]

    def get_space_status(self, space_id: str) -> dict[str, Any]:
        counts_rows = self.db.query_all(
            """
            SELECT status, COUNT(*) AS cnt
            FROM ingest_jobs
            WHERE space_id = ?
            GROUP BY status
            """,
            (space_id,),
        )
        counts = {str(r["status"]): int(r["cnt"]) for r in counts_rows}
        queued = counts.get("queued", 0)
        processing = counts.get("processing", 0)
        completed = counts.get("completed", 0)
        failed = counts.get("failed", 0)
        cancelled = counts.get("cancelled", 0)

        last_done = self.db.query_one(
            """
            SELECT updated_at FROM ingest_jobs
            WHERE space_id = ? AND status IN ('completed', 'failed', 'cancelled')
            ORDER BY updated_at DESC, rowid DESC
            LIMIT 1
            """,
            (space_id,),
        )
        recent_rows = self.db.query_all(
            """
            SELECT job_id, note_id, status, attempts, error_message, created_at, updated_at
            FROM ingest_jobs
            WHERE space_id = ?
            ORDER BY rowid DESC
            LIMIT 10
            """,
            (space_id,),
        )
        return {
            "space_id": space_id,
            "queued": queued,
            "processing": processing,
            "pending": queued + processing,
            "completed": completed,
            "failed": failed,
            "cancelled": cancelled,
            "last_completed_at": str(last_done["updated_at"]) if last_done else "",
            "pending_note_ids": self.list_pending_note_ids(space_id, limit=10),
            "recent_jobs": [
                {
                    "job_id": str(r["job_id"]),
                    "note_id": str(r["note_id"]),
                    "status": str(r["status"]),
                    "attempts": int(r["attempts"]),
                    "error_message": str(r["error_message"]),
                    "created_at": str(r["created_at"]),
                    "updated_at": str(r["updated_at"]),
                }
                for r in recent_rows
            ],
        }

    @staticmethod
    def _row_to_dict(row: Any) -> dict[str, Any]:
        try:
            result_data = json.loads(row["result_json"] or "{}")
        except Exception:
            result_data = {}
        return {
            "job_id": str(row["job_id"]),
            "space_id": str(row["space_id"]),
            "note_id": str(row["note_id"]),
            "actor_id": str(row["actor_id"]),
            "idempotency_key": str(row["idempotency_key"]),
            "status": str(row["status"]),
            "attempts": int(row["attempts"]),
            "error_message": str(row["error_message"]),
            "result": result_data,
            "created_at": str(row["created_at"]),
            "updated_at": str(row["updated_at"]),
        }


class IngestWorkerPool:
    """Bounded background worker pool with Per-Space serialization, Fair Round-Robin, and LLM throttling."""

    def __init__(
        self,
        job_repo: IngestJobRepository,
        processor: Callable[..., Any],
        worker_count: int = 8,
        llm_max_concurrency: int = 12,
        max_retries: int = 3,
        retry_base_delay_seconds: float = 0.35,
    ) -> None:
        self.job_repo = job_repo
        self.processor = processor
        self.worker_count = max(1, int(worker_count))
        self.max_retries = max(1, int(max_retries))
        self.retry_base_delay_seconds = max(0.01, float(retry_base_delay_seconds))
        self.llm_semaphore = threading.Semaphore(max(1, int(llm_max_concurrency)))

        self._sched_lock = threading.Lock()
        self._cond = threading.Condition(self._sched_lock)
        self._stop_event = threading.Event()
        self._active_spaces: set[str] = set()
        self._last_scheduled: dict[str, float] = {}
        self._threads: list[threading.Thread] = []
        self._running = False

    @property
    def is_running(self) -> bool:
        with self._sched_lock:
            return self._running

    def start(self) -> None:
        with self._sched_lock:
            if self._running:
                return
            self._stop_event.clear()
            self.job_repo.recover_interrupted_jobs()
            self._running = True
            self._threads = [
                threading.Thread(
                    target=self._worker_loop,
                    name=f"tm-ingest-worker-{i}",
                    daemon=True,
                )
                for i in range(self.worker_count)
            ]
            for t in self._threads:
                t.start()

    def stop(self, timeout: float = 5.0) -> None:
        with self._sched_lock:
            if not self._running:
                return
            self._running = False
            self._stop_event.set()
            threads = list(self._threads)
            self._threads.clear()
            self._cond.notify_all()
        deadline = time.monotonic() + timeout
        for t in threads:
            rem = max(0.05, deadline - time.monotonic())
            t.join(timeout=rem)

    def notify(self) -> None:
        with self._sched_lock:
            self._cond.notify_all()

    def wait_for_space_idle(self, space_id: str, timeout: float = 10.0) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            st = self.job_repo.get_space_status(space_id)
            if st["pending"] == 0:
                with self._sched_lock:
                    if space_id not in self._active_spaces:
                        return True
            time.sleep(0.02)
        return False

    def wait_for_job(self, job_id: str, timeout: float = 10.0) -> dict[str, Any] | None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            job = self.job_repo.get_job(job_id)
            if job is not None and job["status"] in ("completed", "failed", "cancelled"):
                return job
            time.sleep(0.02)
        return self.job_repo.get_job(job_id)

    def _worker_loop(self) -> None:
        try:
            while not self._stop_event.is_set():
                job: dict[str, Any] | None = None
                with self._sched_lock:
                    if self._stop_event.is_set():
                        break
                    try:
                        job = self.job_repo.claim_next_fair_job(
                            self._active_spaces, self._last_scheduled
                        )
                    except Exception:
                        job = None
                    if job is not None:
                        sp = job["space_id"]
                        self._active_spaces.add(sp)
                        self._last_scheduled[sp] = time.monotonic()
                    else:
                        self._cond.wait(timeout=0.4)
                        continue

                if job is None:
                    continue

                sp = job["space_id"]
                try:
                    self._execute_job(job)
                finally:
                    with self._sched_lock:
                        self._active_spaces.discard(sp)
                        self._cond.notify_all()
        finally:
            self.job_repo.db.close()

    def _execute_job(self, job: dict[str, Any]) -> None:
        job_id = job["job_id"]
        space_id = job["space_id"]
        note_id = job["note_id"]
        attempts = int(job.get("attempts", 1))
        idem = str(job.get("idempotency_key", "") or "")

        try:
            res = self.processor(
                space_id=space_id,
                note_id=note_id,
                idempotency_key=idem,
                count_note_in_quota=False,
                llm_semaphore=self.llm_semaphore,
                raise_on_transient_llm_error=(attempts < self.max_retries),
            )
            status = getattr(res, "extraction_status", "completed")
            if status == "cancelled_forgotten":
                self.job_repo.mark_cancelled(job_id, getattr(res, "error_message", "note_forgotten"))
            elif status == "degraded_error":
                err_msg = getattr(res, "error_message", "")
                self.job_repo.mark_completed(
                    job_id,
                    {
                        "extraction_status": status,
                        "changes_count": len(getattr(res, "changes", ())),
                        "error_message": err_msg,
                        "trace_id": getattr(res, "trace_id", ""),
                    },
                )
            else:
                self.job_repo.mark_completed(
                    job_id,
                    {
                        "extraction_status": status,
                        "changes_count": len(getattr(res, "changes", ())),
                        "trace_id": getattr(res, "trace_id", ""),
                    },
                )
        except Exception as exc:
            if attempts < self.max_retries and _is_transient_llm_error(exc):
                backoff = min(
                    8.0,
                    self.retry_base_delay_seconds * (2 ** max(0, attempts - 1)),
                ) + random.uniform(0.0, 0.15)
                self._stop_event.wait(timeout=backoff)
                if not self._stop_event.is_set():
                    self.job_repo.requeue_for_retry(job_id, f"transient_retry_{attempts}: {exc}")
                    self.notify()
                return
            self.job_repo.mark_failed(job_id, str(exc))
