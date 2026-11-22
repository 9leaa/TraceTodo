"""Zero-duplication Calendar Timeline & Month Panorama projection service.

Projects tasks (`due_at` + `is_all_day` + Macaron `color_theme`), completed
`focus_sessions`, daily `raw_notes` badges, and optional countdowns onto a
`00:00-24:00` weekly timeline or 42-cell monthly panorama without creating an
isolated calendar schedule table.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any, Callable

from trace_memory.domain.notes import BEIJING_TZ, beijing_now, beijing_today_date, to_beijing
from trace_memory.domain.proposals import ValidationError
from trace_memory.services.task_matrix_service import (
    COLOR_THEMES,
    QUADRANT_LABELS,
    TaskMatrixService,
)
from trace_memory.storage.database import Database
from trace_memory.storage.repositories import MemoryRepository


WEEKDAY_LABELS = ("周一", "周二", "周三", "周四", "周五", "周六", "周日")


def _parse_ymd(raw: str) -> date:
    clean = (raw or "").strip()[:10]
    if clean:
        try:
            return date.fromisoformat(clean)
        except ValueError as exc:
            raise ValidationError(f"日期格式错误，应为 YYYY-MM-DD：{raw!r}") from exc
    return date.fromisoformat(beijing_today_date())


def _parse_iso_beijing(raw: str) -> datetime | None:
    clean = (raw or "").strip()
    if not clean:
        return None
    if clean.endswith("Z"):
        clean = clean[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(clean)
    except ValueError:
        return None
    return to_beijing(dt)


def _fmt_hhmm(minute_of_day: int) -> str:
    clamped = max(0, min(1440, int(minute_of_day)))
    if clamped >= 1440:
        return "24:00"
    return f"{clamped // 60:02d}:{clamped % 60:02d}"


def _merge_intervals(intervals: list[tuple[int, int]], clip_start: int = 0, clip_end: int = 1440) -> int:
    clipped: list[tuple[int, int]] = []
    for s, e in intervals:
        cs = max(clip_start, min(clip_end, int(s)))
        ce = max(clip_start, min(clip_end, int(e)))
        if ce > cs:
            clipped.append((cs, ce))
    if not clipped:
        return 0
    clipped.sort(key=lambda x: (x[0], x[1]))
    merged = [clipped[0]]
    for cs, ce in clipped[1:]:
        ps, pe = merged[-1]
        if cs <= pe:
            merged[-1] = (ps, max(pe, ce))
        else:
            merged.append((cs, ce))
    return sum(e - s for s, e in merged)


def _assign_overlap_columns(blocks: list[dict[str, Any]]) -> None:
    """Assign non-overlapping `col_idx` and `col_total` in-place for timeline blocks."""
    if not blocks:
        return
    blocks.sort(key=lambda b: (int(b["start_minute"]), -int(b["duration_minutes"]), str(b.get("title", ""))))

    # Partition into connected overlap clusters
    clusters: list[list[dict[str, Any]]] = []
    cur_cluster: list[dict[str, Any]] = []
    cluster_end = -1
    for blk in blocks:
        s = int(blk["start_minute"])
        e = int(blk["end_minute"])
        if not cur_cluster or s < cluster_end:
            cur_cluster.append(blk)
            cluster_end = max(cluster_end, e)
        else:
            clusters.append(cur_cluster)
            cur_cluster = [blk]
            cluster_end = e
    if cur_cluster:
        clusters.append(cur_cluster)

    for cluster in clusters:
        col_ends: list[int] = []
        for blk in cluster:
            s = int(blk["start_minute"])
            e = int(blk["end_minute"])
            placed_col = -1
            for idx, c_end in enumerate(col_ends):
                if c_end <= s:
                    placed_col = idx
                    col_ends[idx] = e
                    break
            if placed_col < 0:
                placed_col = len(col_ends)
                col_ends.append(e)
            blk["col_idx"] = placed_col
        total_cols = max(1, len(col_ends))
        for blk in cluster:
            blk["col_total"] = total_cols


class CalendarService:
    """Zero-duplication Calendar Timeline & Slot Scheduling service."""

    def __init__(
        self,
        *,
        db: Database,
        memory_repo: MemoryRepository,
        task_matrix_service: TaskMatrixService,
        countdown_provider: Callable[[str, date, date], dict[str, list[dict[str, Any]]]] | None = None,
    ) -> None:
        self.db = db
        self.memory_repo = memory_repo
        self.task_matrix_service = task_matrix_service
        self.countdown_provider = countdown_provider

    def set_countdown_provider(
        self,
        provider: Callable[[str, date, date], dict[str, list[dict[str, Any]]]] | None,
    ) -> None:
        self.countdown_provider = provider

    def get_overview(
        self,
        *,
        space_id: str,
        view: str = "week",
        anchor_date: str = "",
        include_done: bool = True,
    ) -> dict[str, Any]:
        sp = (space_id or "default").strip() or "default"
        clean_view = (view or "week").strip().lower()
        if clean_view not in ("week", "month"):
            clean_view = "week"

        anchor = _parse_ymd(anchor_date)
        today_d = date.fromisoformat(beijing_today_date())
        now_bj = beijing_now()
        now_minute = now_bj.hour * 60 + now_bj.minute

        if clean_view == "week":
            range_start = anchor - timedelta(days=anchor.weekday())
            range_end = range_start + timedelta(days=6)
            period_label = f"{range_start.strftime('%Y.%m.%d')} - {range_end.strftime('%m.%d')}"
        else:
            first_of_month = date(anchor.year, anchor.month, 1)
            range_start = first_of_month - timedelta(days=first_of_month.weekday())
            range_end = range_start + timedelta(days=41)
            period_label = f"{anchor.year} 年 {anchor.month} 月"

        start_ymd = range_start.isoformat()
        end_ymd = range_end.isoformat()
        total_days = (range_end - range_start).days + 1

        # Build day buckets
        days_by_ymd: dict[str, dict[str, Any]] = {}
        ordered_ymds: list[str] = []
        for offset in range(total_days):
            cur_d = range_start + timedelta(days=offset)
            ymd = cur_d.isoformat()
            ordered_ymds.append(ymd)
            days_by_ymd[ymd] = {
                "date": ymd,
                "year": cur_d.year,
                "month": cur_d.month,
                "day": cur_d.day,
                "weekday": cur_d.weekday(),
                "weekday_label": WEEKDAY_LABELS[cur_d.weekday()],
                "is_today": cur_d == today_d,
                "is_current_month": cur_d.month == anchor.month and cur_d.year == anchor.year,
                "is_weekend": cur_d.weekday() >= 5,
                "all_day_tasks": [],
                "timed_blocks": [],
                "focus_blocks": [],
                "countdowns": [],
                "note_count": 0,
                "journal_count": 0,
                "busy_minutes": 0,
                "free_minutes": 1440,
                "awake_free_minutes": 840,
                "focus_minutes": 0,
            }

        # 1. Project tasks from TaskMatrixService (single source of truth)
        all_tasks = self.task_matrix_service.list_sorted_tasks(
            space_id=sp,
            task_status="all" if include_done else "todo",
            sort_by="priority",
        )
        unscheduled_tasks: list[dict[str, Any]] = []
        total_scheduled_tasks = 0
        total_all_day_tasks = 0
        total_timed_blocks = 0

        for task in all_tasks:
            p = task.get("payload") or {}
            start_raw = str(p.get("start_at") or "").strip()
            due_raw = str(p.get("due_at") or "").strip()
            is_done = p.get("task_status") == "done"
            if not due_raw and not start_raw:
                if not is_done:
                    unscheduled_tasks.append(
                        {
                            "memory_id": task["memory_id"],
                            "version": task["version"],
                            "title": task["title"],
                            "quadrant": p.get("quadrant", "Q4_eliminate"),
                            "quadrant_label": p.get("quadrant_label", QUADRANT_LABELS.get(p.get("quadrant", "Q4_eliminate"), "")),
                            "priority_score": p.get("priority_score", 0),
                            "color_theme": p.get("color_theme", "sky") if p.get("color_theme") in COLOR_THEMES else "sky",
                            "focus_minutes": int(p.get("focus_minutes", 25) or 25),
                            "duration_minutes": int(p.get("duration_minutes") or max(30, int(p.get("focus_minutes", 45) or 45))),
                        }
                    )
                continue

            anchor_raw = due_raw or start_raw
            color_theme = str(p.get("color_theme") or "sky")
            if color_theme not in COLOR_THEMES:
                color_theme = "sky"
            is_all_day = bool(p.get("is_all_day", False)) or (not start_raw and len(due_raw) <= 10)
            start_dt = None if is_all_day else _parse_iso_beijing(start_raw)
            due_dt = None if is_all_day else _parse_iso_beijing(due_raw)

            if is_all_day or (start_dt is None and due_dt is None):
                due_ymd = (due_dt or start_dt).date().isoformat() if (due_dt or start_dt) is not None else anchor_raw[:10]
                day_bucket = days_by_ymd.get(due_ymd)
                if day_bucket is None:
                    continue
                total_scheduled_tasks += 1
                total_all_day_tasks += 1
                day_bucket["all_day_tasks"].append(
                    {
                        "kind": "all_day_task",
                        "memory_id": task["memory_id"],
                        "version": task["version"],
                        "title": task["title"],
                        "summary": task.get("summary", ""),
                        "quadrant": p.get("quadrant", "Q4_eliminate"),
                        "quadrant_label": p.get("quadrant_label", ""),
                        "task_status": p.get("task_status", "todo"),
                        "is_done": is_done,
                        "is_overdue": bool(p.get("is_overdue", False)),
                        "color_theme": color_theme,
                        "start_at": start_raw,
                        "due_at": due_raw,
                        "is_all_day": True,
                    }
                )
            else:
                raw_dur = p.get("duration_minutes")
                if raw_dur is not None:
                    dur_min = max(15, min(1440, int(raw_dur)))
                else:
                    dur_min = max(30, min(240, int(p.get("focus_minutes", 60) or 60)))

                if start_dt is not None and due_dt is not None and due_dt > start_dt:
                    blk_start = start_dt
                    blk_end = min(due_dt, start_dt + timedelta(minutes=1440))
                    if (blk_end - blk_start) < timedelta(minutes=15):
                        blk_end = blk_start + timedelta(minutes=15)
                elif start_dt is not None:
                    blk_start = start_dt
                    blk_end = start_dt + timedelta(minutes=dur_min)
                else:
                    assert due_dt is not None
                    blk_start = due_dt
                    blk_end = due_dt + timedelta(minutes=dur_min)

                counted_task = False
                cur_d = blk_start.date()
                while cur_d <= blk_end.date():
                    if cur_d == blk_end.date() and blk_end.hour == 0 and blk_end.minute == 0 and blk_end.date() > blk_start.date():
                        break
                    cur_ymd = cur_d.isoformat()
                    day_bucket = days_by_ymd.get(cur_ymd)
                    if day_bucket is not None:
                        if not counted_task:
                            total_scheduled_tasks += 1
                            counted_task = True
                        total_timed_blocks += 1
                        seg_start_raw = (blk_start.hour * 60 + blk_start.minute) if cur_d == blk_start.date() else 0
                        seg_end_raw = (blk_end.hour * 60 + blk_end.minute) if cur_d == blk_end.date() else 1440
                        start_min = max(0, min(1425, seg_start_raw))
                        end_min = max(start_min + 15, min(1440, seg_end_raw))
                        eff_dur = max(15, end_min - start_min)
                        day_bucket["timed_blocks"].append(
                            {
                                "kind": "task",
                                "memory_id": task["memory_id"],
                                "version": task["version"],
                                "title": task["title"],
                                "summary": task.get("summary", ""),
                                "quadrant": p.get("quadrant", "Q4_eliminate"),
                                "quadrant_label": p.get("quadrant_label", ""),
                                "task_status": p.get("task_status", "todo"),
                                "is_done": is_done,
                                "is_overdue": bool(p.get("is_overdue", False)),
                                "color_theme": color_theme,
                                "start_at": start_raw or blk_start.replace(microsecond=0).isoformat(),
                                "due_at": due_raw or blk_end.replace(microsecond=0).isoformat(),
                                "is_all_day": False,
                                "start_minute": start_min,
                                "end_minute": end_min,
                                "duration_minutes": eff_dur,
                                "time_range_label": f"{_fmt_hhmm(start_min)} - {_fmt_hhmm(end_min)}",
                                "col_idx": 0,
                                "col_total": 1,
                            }
                        )
                    cur_d += timedelta(days=1)

        # 2. Project completed focus_sessions in [start_ymd, end_ymd]
        focus_rows = self.db.query_all(
            """
            SELECT session_id, task_memory_id, task_title, mode, duration_seconds,
                   started_at, ended_at, focus_date
            FROM focus_sessions
            WHERE space_id = ? AND status = 'completed'
              AND focus_date >= ? AND focus_date <= ?
            ORDER BY started_at ASC
            """,
            (sp, start_ymd, end_ymd),
        )
        total_focus_sessions = 0
        total_focus_minutes = 0
        for f_row in focus_rows:
            f_ymd = str(f_row["focus_date"] or "")[:10]
            day_bucket = days_by_ymd.get(f_ymd)
            if day_bucket is None:
                continue
            dur_sec = max(60, int(f_row["duration_seconds"] or 0))
            dur_min = max(1, round(dur_sec / 60))
            st_dt = _parse_iso_beijing(str(f_row["started_at"] or ""))
            en_dt = _parse_iso_beijing(str(f_row["ended_at"] or ""))
            if st_dt is not None:
                start_min = max(0, min(1435, st_dt.hour * 60 + st_dt.minute))
            elif en_dt is not None:
                en_m = en_dt.hour * 60 + en_dt.minute
                start_min = max(0, en_m - dur_min)
            else:
                start_min = 9 * 60
            end_min = min(1440, max(start_min + 5, start_min + dur_min))
            total_focus_sessions += 1
            total_focus_minutes += dur_min
            day_bucket["focus_minutes"] += dur_min
            day_bucket["focus_blocks"].append(
                {
                    "kind": "focus",
                    "session_id": str(f_row["session_id"]),
                    "task_memory_id": str(f_row["task_memory_id"] or ""),
                    "title": str(f_row["task_title"] or "自由专注"),
                    "mode": str(f_row["mode"] or "pomodoro"),
                    "start_minute": start_min,
                    "end_minute": end_min,
                    "duration_minutes": dur_min,
                    "time_range_label": f"{_fmt_hhmm(start_min)} - {_fmt_hhmm(end_min)}",
                }
            )

        # 3. Project active RawNote / Memo / Journal counts by date
        note_rows = self.db.query_all(
            """
            SELECT CASE WHEN note_kind = 'journal' AND journal_date != '' THEN journal_date ELSE substr(event_time, 1, 10) END AS ymd,
                   note_kind, source_type, COUNT(*) AS cnt
            FROM raw_notes
            WHERE space_id = ? AND is_forgotten = 0
              AND content NOT LIKE '[Task %'
              AND (CASE WHEN note_kind = 'journal' AND journal_date != '' THEN journal_date ELSE substr(event_time, 1, 10) END) >= ?
              AND (CASE WHEN note_kind = 'journal' AND journal_date != '' THEN journal_date ELSE substr(event_time, 1, 10) END) <= ?
            GROUP BY ymd, note_kind, source_type
            """,
            (sp, start_ymd, end_ymd),
        )
        for n_row in note_rows:
            n_ymd = str(n_row["ymd"] or "")
            day_bucket = days_by_ymd.get(n_ymd)
            if day_bucket is None:
                continue
            cnt = int(n_row["cnt"] or 0)
            nk = str(n_row["note_kind"] or "note")
            src = str(n_row["source_type"] or "")
            day_bucket["note_count"] += cnt
            if nk == "journal" or src == "journal":
                day_bucket["journal_count"] += cnt

        # 4. Project countdown / anniversary badges if countdown_provider is wired
        if self.countdown_provider is not None:
            cd_map = self.countdown_provider(sp, range_start, range_end)
            for c_ymd, items in cd_map.items():
                if c_ymd in days_by_ymd:
                    days_by_ymd[c_ymd]["countdowns"] = items

        # 5. Finalize overlap layout & busy/free time stats per day
        total_busy_minutes = 0
        for ymd in ordered_ymds:
            b = days_by_ymd[ymd]
            _assign_overlap_columns(b["timed_blocks"])
            intervals = [
                (int(tb["start_minute"]), int(tb["end_minute"]))
                for tb in b["timed_blocks"]
                if not tb.get("is_done", False)
            ]
            # Include completed task blocks in visual busy time too so past days reflect real schedule load
            all_intervals = [
                (int(tb["start_minute"]), int(tb["end_minute"]))
                for tb in b["timed_blocks"]
            ]
            busy_m = _merge_intervals(all_intervals, 0, 1440)
            awake_busy_m = _merge_intervals(all_intervals, 8 * 60, 22 * 60)
            b["busy_minutes"] = busy_m
            b["free_minutes"] = max(0, 1440 - busy_m)
            b["awake_free_minutes"] = max(0, (14 * 60) - awake_busy_m)
            b["todo_busy_minutes"] = _merge_intervals(intervals, 0, 1440)
            total_busy_minutes += busy_m

        return {
            "space_id": sp,
            "view": clean_view,
            "anchor_date": anchor.isoformat(),
            "today_date": today_d.isoformat(),
            "now_minute": now_minute,
            "period_label": period_label,
            "range_start": start_ymd,
            "range_end": end_ymd,
            "days": [days_by_ymd[ymd] for ymd in ordered_ymds],
            "unscheduled_tasks": unscheduled_tasks[:30],
            "summary": {
                "total_scheduled_tasks": total_scheduled_tasks,
                "total_all_day_tasks": total_all_day_tasks,
                "total_timed_blocks": total_timed_blocks,
                "total_busy_minutes": total_busy_minutes,
                "total_busy_hours": round(total_busy_minutes / 60.0, 1),
                "total_focus_sessions": total_focus_sessions,
                "total_focus_minutes": total_focus_minutes,
                "unscheduled_count": len(unscheduled_tasks),
            },
        }

    def schedule_slot(
        self,
        *,
        space_id: str,
        payload: dict[str, Any],
        actor_id: str = "user",
    ) -> dict[str, Any]:
        """Schedule a new or existing task onto a specific date/hour slot (or unschedule it)."""
        sp = (space_id or "default").strip() or "default"
        target_id = str(payload.get("memory_id") or payload.get("target_memory_id") or "").strip()

        if bool(payload.get("unschedule", False)):
            if not target_id:
                raise ValidationError("取消排期必须指定任务 memory_id")
            res = self.task_matrix_service.save_task_card(
                space_id=sp,
                data={
                    "target_memory_id": target_id,
                    "start_at": "",
                    "due_at": "",
                    "is_all_day": True,
                },
                actor_id=actor_id,
            )
            return res.to_dict()

        raw_date = str(payload.get("date") or "").strip()
        if not raw_date:
            raise ValidationError("排期日期 date (YYYY-MM-DD) 不能为空")
        slot_date = _parse_ymd(raw_date)
        is_all_day = bool(payload.get("is_all_day", False))

        def _parse_int_field(raw: Any, default_val: int, lo: int, hi: int, name: str, *, strict_bounds: bool = False) -> int:
            if raw is None or raw == "":
                return default_val
            if isinstance(raw, bool):
                raise ValidationError(f"参数 {name} 必须为整数")
            try:
                val = int(raw)
            except (TypeError, ValueError) as exc:
                raise ValidationError(f"参数 {name} 格式错误：{raw!r}") from exc
            if strict_bounds and (val < lo or val > hi):
                raise ValidationError(f"参数 {name} 超出有效范围 [{lo}, {hi}]：{val}")
            return max(lo, min(hi, val))

        if is_all_day:
            start_iso = ""
            due_iso = slot_date.isoformat()
            dur_min = None
        else:
            raw_time = str(payload.get("time") or "").strip()
            if raw_time:
                if ":" not in raw_time:
                    raise ValidationError(f"时间格式错误，应为 HH:MM：{raw_time!r}")
                parts = raw_time.split(":", 1)
                hh = _parse_int_field(parts[0], 9, 0, 23, "hour", strict_bounds=True)
                mm = _parse_int_field(parts[1], 0, 0, 59, "minute", strict_bounds=True)
            else:
                hh = _parse_int_field(payload.get("hour"), 9, 0, 23, "hour", strict_bounds=True)
                mm = _parse_int_field(payload.get("minute"), 0, 0, 59, "minute", strict_bounds=True)

            dt_bj = datetime(slot_date.year, slot_date.month, slot_date.day, hh, mm, 0, tzinfo=BEIJING_TZ)
            start_iso = dt_bj.isoformat()

            raw_end_time = str(payload.get("end_time") or "").strip()
            if raw_end_time:
                if ":" not in raw_end_time:
                    raise ValidationError(f"结束时间格式错误，应为 HH:MM：{raw_end_time!r}")
                eparts = raw_end_time.split(":", 1)
                ehh = _parse_int_field(eparts[0], hh, 0, 23, "end_hour", strict_bounds=True)
                emm = _parse_int_field(eparts[1], 0, 0, 59, "end_minute", strict_bounds=True)
                end_dt = datetime(slot_date.year, slot_date.month, slot_date.day, ehh, emm, 0, tzinfo=BEIJING_TZ)
                if end_dt <= dt_bj:
                    end_dt += timedelta(days=1)
                dur_min = max(15, min(1440, int((end_dt - dt_bj).total_seconds() // 60)))
                due_iso = end_dt.isoformat()
            else:
                due_iso = dt_bj.isoformat()
                dur_min = _parse_int_field(payload.get("duration_minutes"), 60, 15, 1440, "duration_minutes")

        card_data: dict[str, Any] = {
            "start_at": start_iso,
            "due_at": due_iso,
            "is_all_day": is_all_day,
            "duration_minutes": dur_min,
        }

        if target_id:
            card_data["target_memory_id"] = target_id
            if "color_theme" in payload and payload["color_theme"]:
                card_data["color_theme"] = str(payload["color_theme"])
        else:
            title = str(payload.get("title") or "").strip()
            if not title:
                raise ValidationError("新建排期事项标题不能为空")
            card_data["title"] = title
            card_data["summary"] = str(payload.get("summary") or title).strip()
            card_data["color_theme"] = str(payload.get("color_theme") or "sky")
            card_data["importance"] = _parse_int_field(payload.get("importance"), 4, 1, 5, "importance")
            card_data["urgency"] = _parse_int_field(payload.get("urgency"), 3, 1, 5, "urgency")
            if dur_min is not None:
                card_data["focus_minutes"] = min(240, dur_min)

        res = self.task_matrix_service.save_task_card(
            space_id=sp,
            data=card_data,
            actor_id=actor_id,
        )
        return res.to_dict()
