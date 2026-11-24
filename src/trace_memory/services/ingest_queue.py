"""Persistent SQLite Async Ingest Job Queue & Fair Per-Space Worker Pool.

Design goals:
- Fast-Ack (`< 5ms`) on `POST /api/notes/record` while persisting `raw_notes` + `ingest_jobs`.
- Crash-safe recovery on process startup (`processing` -> `queued`).
- Strict Per-Space FIFO serialization (eliminates same-user burst causal disorder & CAS storms).
- Full Cross-Space parallelism + Fair Round-Robin across active spaces (1 heavy user cannot starve 99 others).
- Global LLM concurrency semaphore + bounded exponential backoff retries on transient 429/5xx/timeouts.
"""

from __future__ import annotations

from collections import deque
from contextlib import contextmanager
import inspect
import json
import random
import re
import threading
import time
from typing import Any, Callable, Iterator

from trace_memory.domain.exceptions import StructuredOutputError
from trace_memory.domain.notes import beijing_now_iso, new_id
from trace_memory.storage.database import Database


def _is_transient_llm_error(exc: Exception) -> bool:
    if isinstance(exc, StructuredOutputError):
        return True
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


# Reference / completion / cancellation / state-mutation cues requiring pre-extraction barrier
# so existing_memories in the LLM prompt already include prior in-flight items from the same space.
_SERIAL_BARRIER_SIGNAL_RE = re.compile(
    r"(?:"
    r"这个|那个|这件事|那件事|这项|那项|上面|刚才|上一条|前一条|继续做|"
    r"顺延|改期|改到|推迟到?|延后到?|提前到|改成|改为|换成|重开|恢复待办|"
    r"搬到|改住|不再|戒掉|不喝了|不喜欢了|"
    r"取消|作废|不用做了|不做了|算了不做|"
    r"(?:标记|标为|设为|改为).{0,6}(?:完成|取消|已完|待办)|"
    r"(?:已|已经|刚刚|终于)[^，,。！？!?\n]{0,18}(?:完成|搞定|做完|办完|写完|修好|买好|提交|交了|处理好|弄好|解决)|"
    r"(?:完成|搞定|做完|办完|写完|修好|买好|打印好|发送好|提交好|处理好|弄好)(?:了|啦|了呀|了呢)[。.!！\s]*$"
    r")",
    re.IGNORECASE,
)


class WorkerStopping(Exception):
    """Raised inside the commit barrier when shutdown starts: the job is requeued, never committed."""


def requires_serial_barrier(text: str) -> bool:
    """Return True when `text` references or mutates prior tasks/facts and should wait before extraction."""
    clean = str(text or "").strip()
    if not clean:
        return False
    return bool(_SERIAL_BARRIER_SIGNAL_RE.search(clean))


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
        content_hint: str = "",
        requires_barrier: bool | None = None,
        context_excludes: tuple[str, ...] | list[str] = (),
    ) -> dict[str, Any]:
        job_id = new_id("job")
        excludes_json = json.dumps([str(x) for x in (context_excludes or ())], ensure_ascii=False)
        now = beijing_now_iso()
        with self.db.transaction() as conn:
            if requires_barrier is None:
                eff_text = content_hint
                if not eff_text:
                    nrow = conn.execute(
                        "SELECT content FROM raw_notes WHERE space_id = ? AND note_id = ?",
                        (space_id, note_id),
                    ).fetchone()
                    if nrow is not None:
                        eff_text = str(nrow["content"] or "")
                barrier_flag = 1 if requires_serial_barrier(eff_text) else 0
            else:
                barrier_flag = 1 if requires_barrier else 0

            cur = conn.execute(
                """
                INSERT INTO ingest_jobs
                    (job_id, space_id, note_id, actor_id, idempotency_key, requires_barrier, status, attempts,
                     error_message, result_json, created_at, updated_at, context_excludes_json)
                VALUES (?, ?, ?, ?, ?, ?, 'queued', 0, '', '{}', ?, ?, ?)
                """,
                (job_id, space_id, note_id, actor_id, idempotency_key, barrier_flag, now, now, excludes_json),
            )
            seq_id = int(cur.lastrowid or 0)
        return {
            "seq_id": seq_id,
            "job_id": job_id,
            "space_id": space_id,
            "note_id": note_id,
            "actor_id": actor_id,
            "idempotency_key": idempotency_key,
            "requires_barrier": bool(barrier_flag),
            "status": "queued",
            "attempts": 0,
            "error_message": "",
            "created_at": now,
            "updated_at": now,
            "context_excludes": [str(x) for x in (context_excludes or ())],
        }

    def get_job(self, job_id: str) -> dict[str, Any] | None:
        row = self.db.query_one("SELECT rowid AS seq_id, * FROM ingest_jobs WHERE job_id = ?", (job_id,))
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
                WHERE space_id = ? AND note_id = ? AND status IN ('queued', 'extracted')
                """,
                (reason, now, space_id, note_id),
            )
            return int(cur.rowcount or 0)

    def claim_next_fair_job(
        self,
        active_spaces: set[str] | dict[str, int],
        last_scheduled: dict[str, float],
        max_parallel_per_space: int = 4,
    ) -> dict[str, Any] | None:
        """Atomically claim the next fair job, allowing same-space parallel extraction when independent."""
        now = beijing_now_iso()
        cap = max(1, int(max_parallel_per_space))

        def _active_count(sp: str) -> int:
            if isinstance(active_spaces, dict):
                return int(active_spaces.get(sp, 0))
            return cap if sp in active_spaces else 0

        with self.db.transaction() as conn:
            head_rows = conn.execute(
                """
                SELECT rowid AS seq_id, *
                FROM ingest_jobs
                WHERE rowid IN (
                    SELECT MIN(rowid)
                    FROM ingest_jobs
                    WHERE status = 'queued'
                    GROUP BY space_id
                )
                """
            ).fetchall()
            eligible_rows = []
            for r in head_rows:
                sp = str(r["space_id"])
                in_flight = _active_count(sp)
                if in_flight >= cap:
                    continue
                if int(r["requires_barrier"] or 0) != 0 and in_flight > 0:
                    # Dependent / completion utterance must wait until earlier in-flight jobs commit
                    continue
                eligible_rows.append(r)

            if not eligible_rows:
                return None

            # Fair Round-Robin: idle spaces (in_flight == 0) always beat already-active spaces;
            # ties break by longest wait since last scheduled turn, then global arrival order.
            row = min(
                eligible_rows,
                key=lambda r: (
                    _active_count(str(r["space_id"])) > 0,
                    last_scheduled.get(str(r["space_id"]), 0.0),
                    int(r["seq_id"]),
                ),
            )
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

    def increment_attempt(self, job_id: str, error_message: str) -> int:
        now = beijing_now_iso()
        with self.db.transaction() as conn:
            conn.execute(
                """
                UPDATE ingest_jobs
                SET attempts = attempts + 1, error_message = ?, updated_at = ?
                WHERE job_id = ?
                """,
                (error_message[:300], now, job_id),
            )
            row = conn.execute(
                "SELECT attempts FROM ingest_jobs WHERE job_id = ?", (job_id,)
            ).fetchone()
            return int(row["attempts"]) if row else 1

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

    def requeue_on_shutdown(self, job_id: str) -> None:
        """Put a claimed job back to `queued` without consuming a retry attempt."""
        now = beijing_now_iso()
        with self.db.transaction() as conn:
            conn.execute(
                """
                UPDATE ingest_jobs
                SET status = 'queued', attempts = MAX(0, attempts - 1),
                    error_message = 'worker_stopping', updated_at = ?
                WHERE job_id = ? AND status = 'processing'
                """,
                (now, job_id),
            )

    # ------------------------------------------------------------------
    # v2 two-stage pipeline (parallel extraction + ordered per-space commit)
    # ------------------------------------------------------------------

    def claim_batch(
        self,
        limit: int,
        in_flight: dict[str, int],
        last_scheduled: dict[str, float],
        space_cap: int,
    ) -> list[dict[str, Any]]:
        """Atomically claim up to `limit` due jobs in ONE transaction, fair round-robin across spaces.

        Within a space jobs are always claimed in seq order; ordering of the writes themselves is
        enforced later by the commit pump, so no reference barrier is needed here.
        """
        if limit <= 0:
            return []
        now_ts = time.time()
        now = beijing_now_iso()
        cap = max(1, int(space_cap))
        with self.db.transaction() as conn:
            rows = conn.execute(
                """
                SELECT rowid AS seq_id, * FROM ingest_jobs
                WHERE status = 'queued' AND next_run_ts <= ?
                ORDER BY rowid ASC
                LIMIT ?
                """,
                (now_ts, max(256, limit * 8)),
            ).fetchall()
            by_space: dict[str, list[Any]] = {}
            for r in rows:
                by_space.setdefault(str(r["space_id"]), []).append(r)
            order = sorted(
                by_space.keys(),
                key=lambda sp: (
                    in_flight.get(sp, 0) > 0,
                    last_scheduled.get(sp, 0.0),
                    int(by_space[sp][0]["seq_id"]),
                ),
            )
            taken = {sp: 0 for sp in order}
            picked: list[Any] = []
            progress = True
            while len(picked) < limit and progress:
                progress = False
                for sp in order:
                    if len(picked) >= limit:
                        break
                    q = by_space[sp]
                    if taken[sp] < len(q) and in_flight.get(sp, 0) + taken[sp] < cap:
                        picked.append(q[taken[sp]])
                        taken[sp] += 1
                        progress = True
            claimed: list[dict[str, Any]] = []
            for r in picked:
                cur = conn.execute(
                    """
                    UPDATE ingest_jobs
                    SET status = 'processing', attempts = attempts + 1, updated_at = ?
                    WHERE job_id = ? AND status = 'queued'
                    """,
                    (now, str(r["job_id"])),
                )
                if cur.rowcount == 1:
                    d = self._row_to_dict(r)
                    d["status"] = "processing"
                    d["attempts"] = int(d["attempts"]) + 1
                    claimed.append(d)
            return claimed

    def mark_extracted(self, job_id: str, artifact: dict[str, Any]) -> bool:
        now = beijing_now_iso()
        with self.db.transaction() as conn:
            cur = conn.execute(
                """
                UPDATE ingest_jobs
                SET status = 'extracted', extraction_json = ?, extracted_ts = ?,
                    error_message = '', updated_at = ?
                WHERE job_id = ? AND status = 'processing'
                """,
                (json.dumps(artifact, ensure_ascii=False), time.time(), now, job_id),
            )
            return int(cur.rowcount or 0) == 1

    def schedule_retry(self, job_id: str, delay_seconds: float, error_message: str) -> None:
        """Transient failure: back to `queued` with a not-before time; no thread sleeps on it."""
        now = beijing_now_iso()
        with self.db.transaction() as conn:
            conn.execute(
                """
                UPDATE ingest_jobs
                SET status = 'queued', next_run_ts = ?, error_message = ?, updated_at = ?
                WHERE job_id = ? AND status = 'processing'
                """,
                (time.time() + max(0.0, float(delay_seconds)), error_message[:300], now, job_id),
            )

    def back_to_queued(self, job_id: str, reason: str) -> None:
        """Discard a stale extraction artifact (context changed) so the job is extracted again."""
        now = beijing_now_iso()
        with self.db.transaction() as conn:
            conn.execute(
                """
                UPDATE ingest_jobs
                SET status = 'queued', extraction_json = '', extracted_ts = 0, next_run_ts = 0,
                    error_message = ?, updated_at = ?
                WHERE job_id = ? AND status = 'extracted'
                """,
                (reason[:300], now, job_id),
            )

    def mark_skipped_ahead(self, job_id: str) -> None:
        with self.db.transaction() as conn:
            conn.execute("UPDATE ingest_jobs SET skipped_ahead = 1 WHERE job_id = ?", (job_id,))

    def list_unsettled(self, space_id: str, limit: int = 64) -> list[dict[str, Any]]:
        """Head of the space's queue: every not-yet-terminal job in seq order."""
        rows = self.db.query_all(
            """
            SELECT rowid AS seq_id, * FROM ingest_jobs
            WHERE space_id = ? AND status IN ('queued', 'processing', 'extracted')
            ORDER BY rowid ASC
            LIMIT ?
            """,
            (space_id, max(1, int(limit))),
        )
        return [self._row_to_dict(r) for r in rows]

    def spaces_with_status(self, status: str) -> list[str]:
        rows = self.db.query_all(
            "SELECT DISTINCT space_id FROM ingest_jobs WHERE status = ?", (status,)
        )
        return [str(r["space_id"]) for r in rows]

    def next_retry_ts(self) -> float:
        row = self.db.query_one(
            "SELECT MIN(next_run_ts) AS ts FROM ingest_jobs WHERE status = 'queued' AND next_run_ts > ?",
            (time.time(),),
        )
        return float(row["ts"]) if row is not None and row["ts"] is not None else 0.0

    def reset_extracted_to_queued(self) -> int:
        """Rollback helper (v2 -> v1): legacy workers do not understand `extracted`."""
        now = beijing_now_iso()
        with self.db.transaction() as conn:
            cur = conn.execute(
                """
                UPDATE ingest_jobs
                SET status = 'queued', extraction_json = '', extracted_ts = 0, updated_at = ?
                WHERE status = 'extracted'
                """,
                (now,),
            )
            return int(cur.rowcount or 0)

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
            WHERE space_id = ? AND status IN ('queued', 'processing', 'extracted')
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
        # `extracted` (v2: waiting for its ordered commit) is still in progress from the user's view
        processing = counts.get("processing", 0) + counts.get("extracted", 0)
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
            SELECT job_id, note_id, status, attempts, error_message, result_json, created_at, updated_at
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
                    # Lets the client tell "AI found nothing actionable" apart from a real extraction
                    "changes_count": self._changes_count(r["result_json"]),
                    "created_at": str(r["created_at"]),
                    "updated_at": str(r["updated_at"]),
                }
                for r in recent_rows
            ],
        }

    @staticmethod
    def _changes_count(result_json: Any) -> int | None:
        try:
            data = json.loads(result_json or "{}")
        except Exception:
            return None
        val = data.get("changes_count") if isinstance(data, dict) else None
        return int(val) if isinstance(val, (int, float)) else None

    @staticmethod
    def _row_to_dict(row: Any) -> dict[str, Any]:
        try:
            result_data = json.loads(row["result_json"] or "{}")
        except Exception:
            result_data = {}
        keys = set(row.keys()) if hasattr(row, "keys") else set()
        return {
            "seq_id": int(row["seq_id"]) if "seq_id" in keys else 0,
            "job_id": str(row["job_id"]),
            "space_id": str(row["space_id"]),
            "note_id": str(row["note_id"]),
            "actor_id": str(row["actor_id"]),
            "idempotency_key": str(row["idempotency_key"]),
            "requires_barrier": bool(int(row["requires_barrier"] or 0)) if "requires_barrier" in keys else False,
            "status": str(row["status"]),
            "attempts": int(row["attempts"]),
            "error_message": str(row["error_message"]),
            "result": result_data,
            "created_at": str(row["created_at"]),
            "updated_at": str(row["updated_at"]),
            "next_run_ts": float(row["next_run_ts"] or 0) if "next_run_ts" in keys else 0.0,
            "extracted_ts": float(row["extracted_ts"] or 0) if "extracted_ts" in keys else 0.0,
            "skipped_ahead": bool(int(row["skipped_ahead"] or 0)) if "skipped_ahead" in keys else False,
            "context_excludes": IngestJobRepository._json_list(row["context_excludes_json"])
            if "context_excludes_json" in keys
            else [],
            "extraction": IngestJobRepository._json_obj(row["extraction_json"])
            if "extraction_json" in keys
            else None,
        }

    @staticmethod
    def _json_list(raw: Any) -> list[str]:
        try:
            data = json.loads(raw or "[]")
        except Exception:
            return []
        return [str(x) for x in data] if isinstance(data, list) else []

    @staticmethod
    def _json_obj(raw: Any) -> dict[str, Any] | None:
        if not raw:
            return None
        try:
            data = json.loads(raw)
        except Exception:
            return None
        return data if isinstance(data, dict) else None


class IngestWorkerPool:
    """Bounded worker pool with Reference-Aware Same-Space Parallelism, Sequence Commit Barrier, and Fair Round-Robin."""

    def __init__(
        self,
        job_repo: IngestJobRepository,
        processor: Callable[..., Any],
        worker_count: int = 8,
        max_parallel_per_space: int = 4,
        llm_max_concurrency: int = 12,
        max_retries: int = 3,
        retry_base_delay_seconds: float = 0.35,
        on_job_settled: Callable[[str, str], None] | None = None,
    ) -> None:
        self.job_repo = job_repo
        self.processor = processor
        self.worker_count = max(1, int(worker_count))
        self.max_parallel_per_space = max(1, int(max_parallel_per_space))
        self.max_retries = max(1, int(max_retries))
        self.retry_base_delay_seconds = max(0.01, float(retry_base_delay_seconds))
        self.llm_semaphore = threading.Semaphore(max(1, int(llm_max_concurrency)))
        self.on_job_settled = on_job_settled
        try:
            proc_params = inspect.signature(processor).parameters
            self._supports_defer_embeddings = "defer_embeddings" in proc_params
            self._supports_commit_barrier = "commit_barrier" in proc_params
            self._supports_context_excludes = "context_excludes" in proc_params
        except (TypeError, ValueError):
            self._supports_defer_embeddings = False
            self._supports_commit_barrier = False
            self._supports_context_excludes = False

        self._sched_lock = threading.Lock()
        self._cond = threading.Condition(self._sched_lock)
        self._stop_event = threading.Event()
        self._active_space_counts: dict[str, int] = {}
        self._uncommitted_seqs: dict[str, set[int]] = {}
        self._last_scheduled: dict[str, float] = {}
        self._threads: list[threading.Thread] = []
        self._vec_queue: deque[tuple[str, Callable[[], None]]] = deque()
        self._pending_vector_counts: dict[str, int] = {}
        self._vec_threads: list[threading.Thread] = []
        self._running = False

    @property
    def _active_spaces(self) -> set[str]:
        return set(self._active_space_counts.keys())

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
            self.job_repo.reset_extracted_to_queued()
            self._running = True
            self._threads = [
                threading.Thread(
                    target=self._worker_loop,
                    name=f"tm-ingest-worker-{i}",
                    daemon=True,
                )
                for i in range(self.worker_count)
            ]
            vec_worker_cnt = max(1, min(4, self.worker_count))
            self._vec_threads = [
                threading.Thread(
                    target=self._vector_worker_loop,
                    name=f"tm-vec-worker-{i}",
                    daemon=True,
                )
                for i in range(vec_worker_cnt)
            ]
            for t in self._threads:
                t.start()
            for vt in self._vec_threads:
                vt.start()

    def stop(self, timeout: float = 5.0) -> None:
        with self._sched_lock:
            if not self._running:
                return
            self._running = False
            self._stop_event.set()
            threads = list(self._threads) + list(self._vec_threads)
            self._threads.clear()
            self._vec_threads.clear()
            self._cond.notify_all()
        deadline = time.monotonic() + timeout
        for t in threads:
            rem = max(0.05, deadline - time.monotonic())
            t.join(timeout=rem)

    def notify(self) -> None:
        with self._sched_lock:
            self._cond.notify_all()

    def _enqueue_deferred_vector(self, space_id: str, fn: Callable[[], None]) -> None:
        with self._sched_lock:
            self._pending_vector_counts[space_id] = self._pending_vector_counts.get(space_id, 0) + 1
            self._vec_queue.append((space_id, fn))
            self._cond.notify_all()

    def _vector_worker_loop(self) -> None:
        try:
            while True:
                item: tuple[str, Callable[[], None]] | None = None
                with self._sched_lock:
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
                    with self._sched_lock:
                        rem = self._pending_vector_counts.get(sp, 0) - 1
                        if rem <= 0:
                            self._pending_vector_counts.pop(sp, None)
                        else:
                            self._pending_vector_counts[sp] = rem
                        self._cond.notify_all()
        finally:
            self.job_repo.db.close_thread_connection()

    def wait_for_space_idle(self, space_id: str, timeout: float = 10.0) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            st = self.job_repo.get_space_status(space_id)
            if st["pending"] == 0:
                with self._sched_lock:
                    if (
                        self._active_space_counts.get(space_id, 0) == 0
                        and self._pending_vector_counts.get(space_id, 0) == 0
                    ):
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

    @contextmanager
    def _commit_barrier(self, space_id: str, seq_id: int) -> Iterator[None]:
        """Wait until all earlier in-flight jobs (`other_seq < seq_id`) in `space_id` have settled.

        A job that is free to commit is always let through, even during shutdown. A job that would
        still have to wait when shutdown begins raises `WorkerStopping` and must NOT commit, otherwise
        it could land before an earlier, still-running job of the same space.
        """
        with self._sched_lock:
            while True:
                uncommitted = self._uncommitted_seqs.get(space_id)
                if not uncommitted or min(uncommitted) >= seq_id:
                    break
                if self._stop_event.is_set():
                    raise WorkerStopping(f"shutdown while waiting for earlier jobs in {space_id}")
                self._cond.wait(timeout=0.2)
        yield

    def _worker_loop(self) -> None:
        try:
            while not self._stop_event.is_set():
                job: dict[str, Any] | None = None
                with self._sched_lock:
                    if self._stop_event.is_set():
                        break
                    try:
                        job = self.job_repo.claim_next_fair_job(
                            self._active_space_counts,
                            self._last_scheduled,
                            max_parallel_per_space=min(self.worker_count, self.max_parallel_per_space),
                        )
                    except Exception:
                        job = None
                    if job is not None:
                        sp = job["space_id"]
                        seq_id = int(job.get("seq_id") or 0)
                        self._active_space_counts[sp] = self._active_space_counts.get(sp, 0) + 1
                        self._uncommitted_seqs.setdefault(sp, set()).add(seq_id)
                        self._last_scheduled[sp] = time.monotonic()
                    else:
                        self._cond.wait(timeout=0.4)
                        continue

                if job is None:
                    continue

                sp = job["space_id"]
                seq_id = int(job.get("seq_id") or 0)
                try:
                    self._execute_job(job)
                finally:
                    with self._sched_lock:
                        seqs = self._uncommitted_seqs.get(sp)
                        if seqs is not None:
                            seqs.discard(seq_id)
                            if not seqs:
                                self._uncommitted_seqs.pop(sp, None)
                        rem_active = self._active_space_counts.get(sp, 0) - 1
                        if rem_active <= 0:
                            self._active_space_counts.pop(sp, None)
                        else:
                            self._active_space_counts[sp] = rem_active
                        self._cond.notify_all()
        finally:
            self.job_repo.db.close_thread_connection()

    def _execute_job(self, job: dict[str, Any]) -> None:
        job_id = job["job_id"]
        space_id = job["space_id"]
        note_id = job["note_id"]
        seq_id = int(job.get("seq_id") or 0)
        attempts = int(job.get("attempts", 1))
        idem = str(job.get("idempotency_key", "") or "")

        while True:
            try:
                call_kwargs: dict[str, Any] = {
                    "space_id": space_id,
                    "note_id": note_id,
                    "idempotency_key": idem,
                    "count_note_in_quota": False,
                    "llm_semaphore": self.llm_semaphore,
                    "raise_on_transient_llm_error": (attempts < self.max_retries),
                }
                if self._supports_defer_embeddings:
                    call_kwargs["defer_embeddings"] = True
                if self._supports_commit_barrier:
                    call_kwargs["commit_barrier"] = lambda: self._commit_barrier(space_id, seq_id)
                if self._supports_context_excludes and job.get("context_excludes"):
                    call_kwargs["context_excludes"] = tuple(job["context_excludes"])
                res = self.processor(**call_kwargs)
                deferred_vec = getattr(res, "deferred_vector_sync", None)
                if callable(deferred_vec):
                    self._enqueue_deferred_vector(space_id, deferred_vec)

                status = getattr(res, "extraction_status", "completed")
                if status == "cancelled_forgotten":
                    self.job_repo.mark_cancelled(job_id, getattr(res, "error_message", "note_forgotten"))
                    settled_status = "cancelled"
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
                    settled_status = "completed"
                else:
                    self.job_repo.mark_completed(
                        job_id,
                        {
                            "extraction_status": status,
                            "changes_count": len(getattr(res, "changes", ())),
                            "trace_id": getattr(res, "trace_id", ""),
                        },
                    )
                    settled_status = "completed"
                if self.on_job_settled is not None:
                    try:
                        self.on_job_settled(space_id, settled_status)
                    except Exception:
                        pass
                return
            except WorkerStopping:
                self.job_repo.requeue_on_shutdown(job_id)
                return
            except Exception as exc:
                if attempts < self.max_retries and _is_transient_llm_error(exc):
                    backoff = min(
                        8.0,
                        self.retry_base_delay_seconds * (2 ** max(0, attempts - 1)),
                    ) + random.uniform(0.0, 0.15)
                    self._stop_event.wait(timeout=backoff)
                    if self._stop_event.is_set():
                        return
                    attempts = self.job_repo.increment_attempt(
                        job_id, f"transient_retry_{attempts}: {exc}"
                    )
                    continue
                self.job_repo.mark_failed(job_id, str(exc))
                if self.on_job_settled is not None:
                    try:
                        self.on_job_settled(space_id, "failed")
                    except Exception:
                        pass
                return
