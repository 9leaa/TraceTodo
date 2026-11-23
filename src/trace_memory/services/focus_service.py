"""Pomodoro & Stopwatch Focus Center service (`focus_sessions` + 4-grid stats + task sync)."""

from __future__ import annotations

from datetime import datetime, timedelta
import sqlite3
from typing import Any

from trace_memory.domain.notes import beijing_now, beijing_today_date, new_id, to_beijing
from trace_memory.domain.proposals import NotFoundError, PermissionDeniedError, ValidationError
from trace_memory.services.sync_and_scheduler import SpaceSyncTracker
from trace_memory.services.task_matrix_service import TaskMatrixService
from trace_memory.storage.database import Database
from trace_memory.storage.repositories import MemoryRepository


VALID_FOCUS_MODES = ("pomodoro", "stopwatch")
VALID_FOCUS_STATUSES = ("completed", "aborted")


def _parse_beijing_dt(raw: str) -> datetime | None:
    clean = (raw or "").strip()
    if not clean:
        return None
    if clean.endswith("Z"):
        clean = clean[:-1] + "+00:00"
    try:
        return to_beijing(datetime.fromisoformat(clean))
    except ValueError:
        return None


def _row_to_focus_session(row: sqlite3.Row) -> dict[str, Any]:
    dur_sec = int(row["duration_seconds"] or 0)
    return {
        "session_id": str(row["session_id"]),
        "space_id": str(row["space_id"]),
        "task_memory_id": str(row["task_memory_id"] or ""),
        "task_title": str(row["task_title"] or "自由专注"),
        "mode": str(row["mode"] or "pomodoro"),
        "planned_minutes": int(row["planned_minutes"] or 25),
        "duration_seconds": dur_sec,
        "duration_minutes": round(dur_sec / 60.0, 1),
        "status": str(row["status"] or "completed"),
        "abort_reason": str(row["abort_reason"] or ""),
        "note": str(row["note"] or ""),
        "started_at": str(row["started_at"]),
        "ended_at": str(row["ended_at"]),
        "focus_date": str(row["focus_date"]),
        "actor_id": str(row["actor_id"] or "user"),
        "created_at": str(row["created_at"]),
    }


class FocusService:
    """Unified Pomodoro & Stopwatch session store, 4-grid aggregator, and task-card bridge."""

    def __init__(
        self,
        db: Database,
        memory_repo: MemoryRepository,
        task_matrix_service: TaskMatrixService,
        sync_tracker: SpaceSyncTracker | None = None,
    ) -> None:
        self.db = db
        self.memory_repo = memory_repo
        self.task_matrix_service = task_matrix_service
        self.sync_tracker = sync_tracker

    def record_session(
        self,
        *,
        space_id: str,
        mode: str = "pomodoro",
        status: str = "completed",
        planned_minutes: int = 25,
        duration_seconds: int = 0,
        task_memory_id: str = "",
        task_title: str = "",
        abort_reason: str = "",
        note: str = "",
        started_at: str = "",
        ended_at: str = "",
        expected_version: int | None = None,
        complete_task: bool = False,
        actor_id: str = "user",
        session_id: str = "",
    ) -> dict[str, Any]:
        sp = (space_id or "default").strip() or "default"
        clean_mode = (mode or "pomodoro").strip().lower()
        if clean_mode not in VALID_FOCUS_MODES:
            raise ValidationError(f"不支持的专注模式: {clean_mode}")

        clean_status = (status or "completed").strip().lower()
        if clean_status not in VALID_FOCUS_STATUSES:
            raise ValidationError(f"不支持的专注状态: {clean_status}")

        plan_min = max(1, min(240, int(planned_minutes or 25)))
        raw_dur = int(duration_seconds or 0)
        if raw_dur <= 0:
            if clean_mode == "pomodoro" and clean_status == "completed":
                eff_dur = plan_min * 60
            else:
                eff_dur = 60
        else:
            eff_dur = min(86400, max(1, raw_dur))

        end_dt = _parse_beijing_dt(ended_at) or beijing_now().replace(microsecond=0)
        start_dt = _parse_beijing_dt(started_at) or (end_dt - timedelta(seconds=eff_dur))
        if start_dt > end_dt:
            start_dt = end_dt - timedelta(seconds=eff_dur)

        end_iso = end_dt.replace(microsecond=0).isoformat()
        start_iso = start_dt.replace(microsecond=0).isoformat()
        focus_date = beijing_today_date(end_dt)

        mem_id = (task_memory_id or "").strip()
        resolved_title = (task_title or "").strip()[:200]
        updated_task_dict: dict[str, Any] | None = None

        if mem_id:
            any_mem = self.memory_repo.get_any_space(mem_id)
            if any_mem is not None and any_mem.space_id != sp:
                raise PermissionDeniedError("无权关联其他空间的待办任务")
            existing = self.memory_repo.get(sp, mem_id)
            if existing is None:
                raise NotFoundError(f"待办任务不存在: {mem_id}")
            if not resolved_title:
                resolved_title = existing.title[:200]

            ev_kind = "complete_session" if clean_status == "completed" else "abort_session"
            focus_chg = self.task_matrix_service.record_focus_event(
                space_id=sp,
                memory_id=mem_id,
                event=ev_kind,
                reason=abort_reason,
                elapsed_seconds=eff_dur,
                expected_version=expected_version,
                actor_id=actor_id,
            )
            next_ver = focus_chg.memory.version if focus_chg.memory else None
            if complete_task and clean_status == "completed":
                trans = self.task_matrix_service.transition_task_state_with_repeat(
                    space_id=sp,
                    memory_id=mem_id,
                    action="complete_task",
                    expected_version=next_ver,
                    actor_id=actor_id,
                )
                updated_task_dict = trans.get("memory")
            elif focus_chg.memory is not None:
                updated_task_dict = focus_chg.memory.to_dict()
        else:
            if not resolved_title:
                resolved_title = "自由专注"

        sid = (session_id or "").strip() or new_id("fcs")
        clean_reason = (abort_reason or "").strip()[:200] if clean_status == "aborted" else ""
        clean_note = (note or "").strip()[:500]
        now_iso = beijing_now().replace(microsecond=0).isoformat()

        with self.db.transaction() as conn:
            conn.execute(
                """
                INSERT INTO focus_sessions (
                    session_id, space_id, task_memory_id, task_title, mode,
                    planned_minutes, duration_seconds, status, abort_reason,
                    note, started_at, ended_at, focus_date, actor_id, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(session_id) DO NOTHING
                """,
                (
                    sid,
                    sp,
                    mem_id,
                    resolved_title,
                    clean_mode,
                    plan_min,
                    eff_dur,
                    clean_status,
                    clean_reason,
                    clean_note,
                    start_iso,
                    end_iso,
                    focus_date,
                    (actor_id or "user")[:64],
                    now_iso,
                ),
            )

        if self.sync_tracker is not None:
            self.sync_tracker.bump(sp, reason="focus_session")

        row = self.db.query_one(
            "SELECT * FROM focus_sessions WHERE session_id = ? AND space_id = ?",
            (sid, sp),
        )
        sess_dict = _row_to_focus_session(row) if row is not None else {}
        stats = self.get_stats(sp)
        return {
            "session": sess_dict,
            "stats": stats,
            "task": updated_task_dict,
        }

    def get_stats(self, space_id: str, now: datetime | None = None) -> dict[str, Any]:
        sp = (space_id or "default").strip() or "default"
        today_str = beijing_today_date(now)

        agg = self.db.query_one(
            """
            SELECT
                COALESCE(SUM(CASE WHEN status = 'completed' AND mode = 'pomodoro' AND focus_date = ? THEN 1 ELSE 0 END), 0) AS today_pomodoros,
                COALESCE(SUM(CASE WHEN status = 'completed' AND mode = 'pomodoro' THEN 1 ELSE 0 END), 0) AS total_pomodoros,
                COALESCE(SUM(CASE WHEN status = 'completed' AND focus_date = ? THEN duration_seconds ELSE 0 END), 0) AS today_seconds,
                COALESCE(SUM(CASE WHEN status = 'completed' THEN duration_seconds ELSE 0 END), 0) AS total_seconds,
                COALESCE(SUM(CASE WHEN status = 'completed' AND focus_date = ? THEN 1 ELSE 0 END), 0) AS today_sessions,
                COALESCE(SUM(CASE WHEN status = 'completed' THEN 1 ELSE 0 END), 0) AS total_sessions,
                COALESCE(SUM(CASE WHEN status = 'aborted' THEN 1 ELSE 0 END), 0) AS aborted_sessions
            FROM focus_sessions
            WHERE space_id = ?
            """,
            (today_str, today_str, today_str, sp),
        )

        today_sec = int(agg["today_seconds"]) if agg else 0
        total_sec = int(agg["total_seconds"]) if agg else 0
        return {
            "today_date": today_str,
            "today_pomodoros": int(agg["today_pomodoros"]) if agg else 0,
            "total_pomodoros": int(agg["total_pomodoros"]) if agg else 0,
            "today_seconds": today_sec,
            "total_seconds": total_sec,
            "today_minutes": round(today_sec / 60.0, 1),
            "total_minutes": round(total_sec / 60.0, 1),
            "today_sessions": int(agg["today_sessions"]) if agg else 0,
            "total_sessions": int(agg["total_sessions"]) if agg else 0,
            "aborted_sessions": int(agg["aborted_sessions"]) if agg else 0,
        }

    def get_overview(
        self,
        space_id: str,
        *,
        limit: int = 50,
        offset: int = 0,
        focus_date: str = "",
        now: datetime | None = None,
    ) -> dict[str, Any]:
        sp = (space_id or "default").strip() or "default"
        lim = max(1, min(200, int(limit or 50)))
        off = max(0, int(offset or 0))
        clean_date = (focus_date or "").strip()[:10]
        if clean_date:
            cnt_row = self.db.query_one(
                "SELECT COUNT(*) AS cnt FROM focus_sessions WHERE space_id = ? AND focus_date = ?",
                (sp, clean_date),
            )
            rows = self.db.query_all(
                """
                SELECT * FROM focus_sessions
                WHERE space_id = ? AND focus_date = ?
                ORDER BY ended_at DESC
                LIMIT ? OFFSET ?
                """,
                (sp, clean_date, lim, off),
            )
        else:
            cnt_row = self.db.query_one(
                "SELECT COUNT(*) AS cnt FROM focus_sessions WHERE space_id = ?",
                (sp,),
            )
            rows = self.db.query_all(
                """
                SELECT * FROM focus_sessions
                WHERE space_id = ?
                ORDER BY ended_at DESC
                LIMIT ? OFFSET ?
                """,
                (sp, lim, off),
            )
        total_cnt = int(cnt_row["cnt"] or 0) if cnt_row else 0
        sessions = [_row_to_focus_session(r) for r in rows]
        return {
            "space_id": sp,
            "stats": self.get_stats(sp, now=now),
            "sessions": sessions,
            "total": total_cnt,
            "limit": lim,
            "offset": off,
            "has_more": (off + len(sessions)) < total_cnt,
        }

    def insert_session_row(
        self,
        *,
        space_id: str,
        task_memory_id: str = "",
        task_title: str = "自由专注",
        mode: str = "pomodoro",
        planned_minutes: int = 25,
        duration_seconds: int = 1500,
        status: str = "completed",
        abort_reason: str = "",
        note: str = "",
        started_at: str = "",
        ended_at: str = "",
        actor_id: str = "user",
        session_id: str = "",
    ) -> dict[str, Any]:
        """Insert a `focus_sessions` row directly (used when task payload was already updated)."""
        sp = (space_id or "default").strip() or "default"
        clean_mode = (mode or "pomodoro").strip().lower()
        if clean_mode not in VALID_FOCUS_MODES:
            clean_mode = "pomodoro"
        clean_status = (status or "completed").strip().lower()
        if clean_status not in VALID_FOCUS_STATUSES:
            clean_status = "completed"

        plan_min = max(1, min(240, int(planned_minutes or 25)))
        raw_dur = int(duration_seconds or 0)
        eff_dur = (plan_min * 60) if (raw_dur <= 0 and clean_status == "completed") else max(1, min(86400, raw_dur or 60))

        end_dt = _parse_beijing_dt(ended_at) or beijing_now().replace(microsecond=0)
        start_dt = _parse_beijing_dt(started_at) or (end_dt - timedelta(seconds=eff_dur))
        end_iso = end_dt.replace(microsecond=0).isoformat()
        start_iso = start_dt.replace(microsecond=0).isoformat()
        focus_date = beijing_today_date(end_dt)
        sid = (session_id or "").strip() or new_id("fcs")
        now_iso = beijing_now().replace(microsecond=0).isoformat()

        with self.db.transaction() as conn:
            conn.execute(
                """
                INSERT INTO focus_sessions (
                    session_id, space_id, task_memory_id, task_title, mode,
                    planned_minutes, duration_seconds, status, abort_reason,
                    note, started_at, ended_at, focus_date, actor_id, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(session_id) DO NOTHING
                """,
                (
                    sid,
                    sp,
                    (task_memory_id or "").strip(),
                    (task_title or "自由专注").strip()[:200] or "自由专注",
                    clean_mode,
                    plan_min,
                    eff_dur,
                    clean_status,
                    (abort_reason or "").strip()[:200] if clean_status == "aborted" else "",
                    (note or "").strip()[:500],
                    start_iso,
                    end_iso,
                    focus_date,
                    (actor_id or "user")[:64],
                    now_iso,
                ),
            )
        row = self.db.query_one("SELECT * FROM focus_sessions WHERE session_id = ? AND space_id = ?", (sid, sp))
        return _row_to_focus_session(row) if row is not None else {}

    def delete_session(self, space_id: str, session_id: str) -> bool:
        sp = (space_id or "default").strip() or "default"
        sid = (session_id or "").strip()
        if not sid:
            raise ValidationError("session_id 不能为空")
        with self.db.transaction() as conn:
            cur = conn.execute(
                "DELETE FROM focus_sessions WHERE space_id = ? AND session_id = ?",
                (sp, sid),
            )
            deleted = cur.rowcount > 0
        if deleted and self.sync_tracker is not None:
            self.sync_tracker.bump(sp, reason="focus_session_deleted")
        return deleted
