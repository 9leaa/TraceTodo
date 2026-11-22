"""Space-level Sync Version Tracker (SSE + Polling) and Beijing-Time Daily Task Scheduler.

Design goals:
- Maintain a durable, monotonically increasing `sync_version` per `space_id` in SQLite so
  Web, macOS, iOS, and MCP clients can detect cross-port writes in <1ms (`GET /api/sync/state`)
  or receive instant push notifications via SSE (`GET /api/sync/stream`).
- Run a zero-dependency background scheduler anchored strictly to Beijing Time (UTC+8) that:
  1. Executes an initial maintenance sweep shortly after startup;
  2. Sweeps due-date urgency escalations hourly;
  3. Runs a dedicated rollover + status refresh sweep right after Beijing midnight (`00:01`).
"""

from __future__ import annotations

from datetime import datetime, timedelta
import threading
import time
from typing import Any

from trace_memory.domain.notes import beijing_now, beijing_now_iso, beijing_today_date, to_beijing
from trace_memory.storage.database import Database


class SpaceSyncTracker:
    """Tracks per-space monotonic sync versions and notifies waiting SSE subscribers."""

    def __init__(self, db: Database) -> None:
        self.db = db
        self._cond = threading.Condition()

    def bump(self, space_id: str, reason: str = "mutation") -> int:
        sp = (space_id or "default").strip() or "default"
        now = beijing_now_iso()
        clean_reason = (reason or "mutation").strip()[:64]
        with self.db.transaction() as conn:
            conn.execute(
                """
                INSERT INTO space_sync_state (space_id, sync_version, last_reason, updated_at)
                VALUES (?, 1, ?, ?)
                ON CONFLICT(space_id) DO UPDATE SET
                    sync_version = space_sync_state.sync_version + 1,
                    last_reason = excluded.last_reason,
                    updated_at = excluded.updated_at
                """,
                (sp, clean_reason, now),
            )
            row = conn.execute(
                "SELECT sync_version FROM space_sync_state WHERE space_id = ?",
                (sp,),
            ).fetchone()
            new_ver = int(row["sync_version"]) if row else 1
        with self._cond:
            self._cond.notify_all()
        return new_ver

    def get_state(self, space_id: str) -> dict[str, Any]:
        sp = (space_id or "default").strip() or "default"
        row = self.db.query_one(
            "SELECT space_id, sync_version, last_reason, updated_at FROM space_sync_state WHERE space_id = ?",
            (sp,),
        )
        now = beijing_now()
        if row is None:
            return {
                "space_id": sp,
                "sync_version": 0,
                "last_reason": "",
                "updated_at": "",
                "server_time": now.replace(microsecond=0).isoformat(),
                "beijing_date": beijing_today_date(now),
            }
        return {
            "space_id": str(row["space_id"]),
            "sync_version": int(row["sync_version"]),
            "last_reason": str(row["last_reason"]),
            "updated_at": str(row["updated_at"]),
            "server_time": now.replace(microsecond=0).isoformat(),
            "beijing_date": beijing_today_date(now),
        }

    def wait_for_update(
        self, space_id: str, known_version: int, timeout_seconds: float = 20.0
    ) -> dict[str, Any]:
        """Block a worker thread cleanly until `sync_version > known_version` or timeout."""
        deadline = time.monotonic() + max(0.2, float(timeout_seconds))
        while True:
            st = self.get_state(space_id)
            if int(st["sync_version"]) > known_version:
                return st
            rem = deadline - time.monotonic()
            if rem <= 0:
                return st
            with self._cond:
                self._cond.wait(timeout=min(rem, 1.0))


class DailyTaskMaintenanceScheduler:
    """Beijing-Time background scheduler for daily rollover and due-date urgency escalation."""

    def __init__(
        self,
        db: Database,
        task_matrix_service: Any,
        sync_tracker: SpaceSyncTracker,
        check_interval_seconds: float = 3600.0,
    ) -> None:
        self.db = db
        self.task_matrix_service = task_matrix_service
        self.sync_tracker = sync_tracker
        self.check_interval_seconds = max(60.0, float(check_interval_seconds))
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._running = False
        self.last_run_report: dict[str, Any] = {}

    @property
    def is_running(self) -> bool:
        with self._lock:
            return self._running

    def start(self) -> None:
        with self._lock:
            if self._running:
                return
            self._stop_event.clear()
            self._running = True
            self._thread = threading.Thread(
                target=self._run_loop,
                name="tm-daily-task-scheduler",
                daemon=True,
            )
            self._thread.start()

    def stop(self, timeout: float = 3.0) -> None:
        with self._lock:
            if not self._running:
                return
            self._running = False
            self._stop_event.set()
            t = self._thread
            self._thread = None
        if t is not None:
            t.join(timeout=timeout)

    def run_once(
        self,
        now: datetime | None = None,
        space_id: str | None = None,
    ) -> dict[str, Any]:
        """Run rollover + due-date escalation check across all active spaces (or one space)."""
        ref_now = to_beijing(now) if now is not None else beijing_now()
        if space_id:
            spaces = [space_id]
        else:
            rows = self.db.query_all(
                "SELECT DISTINCT space_id FROM memories WHERE memory_type = 'task' AND status = 'active'"
            )
            spaces = [str(r["space_id"]) for r in rows]

        total_rolled = 0
        total_refreshed = 0
        touched_spaces: list[str] = []
        failed_spaces: list[str] = []

        for sp in spaces:
            try:
                rolled = self.task_matrix_service.apply_auto_rollover(sp, now=ref_now)
                refreshed = self.task_matrix_service.refresh_due_task_statuses(sp, now=ref_now)
            except Exception:
                failed_spaces.append(sp)
                continue
            if rolled or refreshed:
                total_rolled += len(rolled)
                total_refreshed += len(refreshed)
                touched_spaces.append(sp)
                self.sync_tracker.bump(sp, reason="daily_due_check")

        report = {
            "checked_spaces": len(spaces),
            "touched_spaces": touched_spaces,
            "failed_spaces": failed_spaces,
            "rolled_count": total_rolled,
            "refreshed_count": total_refreshed,
            "beijing_date": beijing_today_date(ref_now),
            "server_time": ref_now.replace(microsecond=0).isoformat(),
        }
        self.last_run_report = report
        return report

    def _seconds_until_next_wake(self, now: datetime | None = None) -> float:
        ref = to_beijing(now) if now is not None else beijing_now()
        next_midnight = (ref + timedelta(days=1)).replace(
            hour=0, minute=1, second=0, microsecond=0
        )
        until_midnight = max(5.0, (next_midnight - ref).total_seconds())
        return min(self.check_interval_seconds, until_midnight)

    def _run_loop(self) -> None:
        try:
            # Run a quick initial sweep shortly after server startup
            if not self._stop_event.wait(timeout=2.0):
                try:
                    self.run_once()
                except Exception:
                    pass

            while not self._stop_event.is_set():
                wait_s = self._seconds_until_next_wake()
                if self._stop_event.wait(timeout=wait_s):
                    break
                try:
                    self.run_once()
                except Exception:
                    pass
        finally:
            self.db.close()
