"""Countdown & Anniversary Days service with Solar/Lunar conversion and Calendar projection."""

from __future__ import annotations

import calendar
from datetime import date, timedelta
import sqlite3
from typing import Any

from trace_memory.domain.notes import beijing_now_iso, beijing_today_date, new_id
from trace_memory.domain.proposals import NotFoundError, ValidationError
from trace_memory.services.lunar_calendar import (
    LUNAR_END_YEAR,
    LUNAR_START_YEAR,
    lunar_to_solar,
    next_lunar_occurrence,
    solar_to_lunar,
)
from trace_memory.services.sync_and_scheduler import SpaceSyncTracker
from trace_memory.storage.database import Database


VALID_COUNTDOWN_KINDS = ("countdown", "anniversary")
VALID_CALENDAR_TYPES = ("solar", "lunar")
VALID_COUNTDOWN_REPEATS = ("none", "yearly", "monthly")
VALID_COUNTDOWN_COLORS = ("sky", "lavender", "pink", "mint", "peach", "butter", "crimson")
WEEKDAY_CN = ("周一", "周二", "周三", "周四", "周五", "周六", "周日")


def _safe_solar_date(year: int, month: int, day: int) -> date:
    max_d = calendar.monthrange(year, month)[1]
    return date(year, month, min(day, max_d))


def _next_solar_yearly(base: date, ref_today: date) -> date:
    cand = _safe_solar_date(ref_today.year, base.month, base.day)
    if cand < ref_today:
        cand = _safe_solar_date(ref_today.year + 1, base.month, base.day)
    return cand


def _next_solar_monthly(base: date, ref_today: date) -> date:
    cand = _safe_solar_date(ref_today.year, ref_today.month, base.day)
    if cand < ref_today:
        if ref_today.month == 12:
            cand = _safe_solar_date(ref_today.year + 1, 1, base.day)
        else:
            cand = _safe_solar_date(ref_today.year, ref_today.month + 1, base.day)
    return cand


def _parse_ymd_strict(raw: str) -> date:
    clean = (raw or "").strip()[:10]
    try:
        return date.fromisoformat(clean)
    except ValueError as exc:
        raise ValidationError(f"日期格式错误（需为 YYYY-MM-DD）：{raw!r}") from exc


class CountdownService:
    """Manages countdowns & positive anniversaries (`countdown_days`) with Lunar/Solar projection."""

    def __init__(self, *, db: Database, sync_tracker: SpaceSyncTracker | None = None) -> None:
        self.db = db
        self.sync_tracker = sync_tracker

    def _project_row(self, row: sqlite3.Row, today: date) -> dict[str, Any]:
        cid = str(row["countdown_id"])
        title = str(row["title"])
        kind = str(row["kind"] or "countdown")
        cal_type = str(row["calendar_type"] or "solar")
        repeat_rule = str(row["repeat_rule"] or "none")
        base_date = _parse_ymd_strict(str(row["target_date"]))
        l_year = int(row["lunar_year"] or 0)
        l_month = int(row["lunar_month"] or 0)
        l_day = int(row["lunar_day"] or 0)
        l_leap = bool(row["is_leap_month"])
        icon = str(row["icon"] or "🎯")
        color = str(row["color_theme"] or "sky")
        if color not in VALID_COUNTDOWN_COLORS:
            color = "sky"
        pinned = bool(row["pinned"])
        note = str(row["note"] or "")

        if cal_type == "lunar" and (l_month <= 0 or l_day <= 0):
            inferred = solar_to_lunar(base_date)
            l_year, l_month, l_day, l_leap = inferred.year, inferred.month, inferred.day, inferred.is_leap_month

        next_anniv_days: int | None = None
        if kind == "anniversary":
            origin_date = base_date
            elapsed = (today - origin_date).days
            if elapsed == 0:
                badge_label = "今天"
                days_value = 0
                display_number = "今天"
                unit_label = ""
                is_today = True
            elif elapsed > 0:
                badge_label = "已经"
                days_value = elapsed
                display_number = str(elapsed)
                unit_label = "天"
                is_today = False
            else:
                badge_label = "还有"
                days_value = abs(elapsed)
                display_number = str(abs(elapsed))
                unit_label = "天"
                is_today = False

            if cal_type == "lunar":
                eff_date = next_lunar_occurrence(l_month, l_day, today, is_leap_month=l_leap)
            else:
                eff_date = _next_solar_yearly(origin_date, today)
            next_anniv_days = (eff_date - today).days
            if next_anniv_days == 0:
                is_today = True
            display_date = origin_date
        else:
            if repeat_rule == "yearly":
                if cal_type == "lunar":
                    eff_date = next_lunar_occurrence(l_month, l_day, today, is_leap_month=l_leap)
                else:
                    eff_date = _next_solar_yearly(base_date, today)
            elif repeat_rule == "monthly":
                eff_date = _next_solar_monthly(base_date, today)
            else:
                eff_date = base_date

            delta = (eff_date - today).days
            is_today = delta == 0
            if delta == 0:
                badge_label = "今天"
                days_value = 0
                display_number = "今天"
                unit_label = ""
            elif delta > 0:
                badge_label = "还有"
                days_value = delta
                display_number = str(delta)
                unit_label = "天"
            else:
                badge_label = "已过"
                days_value = abs(delta)
                display_number = str(abs(delta))
                unit_label = "天"
            display_date = eff_date

        lunar_obj = solar_to_lunar(display_date)
        eff_lunar_obj = solar_to_lunar(eff_date)
        weekday_str = WEEKDAY_CN[display_date.weekday()]

        if badge_label == "今天":
            capsule_text = f"{icon} {title} · 今天"
            header_banner = f"{title} · 今天"
        elif badge_label == "还有":
            capsule_text = f"{icon} {title} · 还有 {days_value} 天"
            header_banner = f"距离 {title} 还有"
        elif badge_label == "已经":
            capsule_text = f"{icon} {title} · 已经 {days_value} 天"
            header_banner = f"{title} 已经"
        else:
            capsule_text = f"{icon} {title} · 已过 {days_value} 天"
            header_banner = f"{title} 已过"

        return {
            "countdown_id": cid,
            "space_id": str(row["space_id"]),
            "title": title,
            "kind": kind,
            "calendar_type": cal_type,
            "target_date": base_date.isoformat(),
            "effective_solar_date": eff_date.isoformat(),
            "display_solar_date": display_date.isoformat(),
            "weekday_label": weekday_str,
            "lunar_year": l_year or lunar_obj.year,
            "lunar_month": l_month or lunar_obj.month,
            "lunar_day": l_day or lunar_obj.day,
            "is_leap_month": l_leap,
            "lunar_label": lunar_obj.short_label,
            "effective_lunar_label": eff_lunar_obj.short_label,
            "repeat_rule": repeat_rule,
            "icon": icon,
            "color_theme": color,
            "pinned": pinned,
            "note": note,
            "is_today": is_today,
            "badge_label": badge_label,
            "days_value": days_value,
            "display_number": display_number,
            "unit_label": unit_label,
            "next_anniversary_days": next_anniv_days,
            "capsule_text": capsule_text,
            "header_banner": header_banner,
            "created_at": str(row["created_at"]),
            "updated_at": str(row["updated_at"]),
        }

    def list_countdowns(self, space_id: str, ref_today: date | None = None) -> dict[str, Any]:
        sp = (space_id or "default").strip() or "default"
        today = ref_today or date.fromisoformat(beijing_today_date())
        rows = self.db.query_all(
            "SELECT * FROM countdown_days WHERE space_id = ? ORDER BY pinned DESC, created_at ASC",
            (sp,),
        )
        items = [self._project_row(r, today) for r in rows]

        def _sort_key(it: dict[str, Any]) -> tuple[int, int, int, int]:
            pin_rank = 0 if it["pinned"] else 1
            today_rank = 0 if it["is_today"] else 1
            if it["badge_label"] in ("今天", "还有"):
                cat_rank = 0
                day_rank = int(it["days_value"])
            elif it["badge_label"] == "已经":
                cat_rank = 1
                day_rank = int(it.get("next_anniversary_days") or 9999)
            else:
                cat_rank = 2
                day_rank = int(it["days_value"])
            return (pin_rank, today_rank, cat_rank, day_rank)

        items.sort(key=_sort_key)
        return {
            "space_id": sp,
            "today_date": today.isoformat(),
            "today_lunar": solar_to_lunar(today).full_label,
            "items": items,
            "top_capsules": items[:3],
            "total": len(items),
        }

    def save_countdown(self, space_id: str, data: dict[str, Any]) -> dict[str, Any]:
        sp = (space_id or "default").strip() or "default"
        cid = str(data.get("countdown_id") or "").strip()
        existing = (
            self.db.query_one(
                "SELECT * FROM countdown_days WHERE space_id = ? AND countdown_id = ?",
                (sp, cid),
            )
            if cid
            else None
        )
        if cid and existing is None and not data.get("allow_custom_id"):
            raise NotFoundError(f"倒数日 {cid!r} 不存在")

        title = str(data.get("title") if "title" in data else (existing["title"] if existing else "")).strip()[:80]
        if not title:
            raise ValidationError("倒数纪念日标题不能为空")

        kind = str(data.get("kind") if "kind" in data else (existing["kind"] if existing else "countdown")).strip().lower()
        if kind not in VALID_COUNTDOWN_KINDS:
            kind = "countdown"

        cal_type = str(
            data.get("calendar_type")
            if "calendar_type" in data
            else (existing["calendar_type"] if existing else "solar")
        ).strip().lower()
        if cal_type not in VALID_CALENDAR_TYPES:
            cal_type = "solar"

        repeat_rule = str(
            data.get("repeat_rule")
            if "repeat_rule" in data
            else (existing["repeat_rule"] if existing else ("yearly" if kind == "anniversary" else "none"))
        ).strip().lower()
        if repeat_rule not in VALID_COUNTDOWN_REPEATS:
            repeat_rule = "none"

        def _parse_cd_int(raw: Any, default_val: int, name: str) -> int:
            if raw is None or raw == "":
                return default_val
            if isinstance(raw, bool):
                raise ValidationError(f"参数 {name} 必须为整数")
            try:
                return int(raw)
            except (TypeError, ValueError) as exc:
                raise ValidationError(f"参数 {name} 格式错误：{raw!r}") from exc

        today = date.fromisoformat(beijing_today_date())
        has_lunar_fields = (
            "lunar_month" in data
            and "lunar_day" in data
            and data.get("lunar_month") is not None
            and data.get("lunar_day") is not None
        )
        preserve_existing_lunar = (
            cal_type == "lunar"
            and existing is not None
            and not has_lunar_fields
            and "target_date" not in data
            and int(existing["lunar_month"] or 0) > 0
            and int(existing["lunar_day"] or 0) > 0
        )
        if cal_type == "lunar" and (has_lunar_fields or preserve_existing_lunar):
            if has_lunar_fields:
                l_year = _parse_cd_int(data.get("lunar_year"), today.year, "lunar_year")
                l_month = _parse_cd_int(data.get("lunar_month"), 1, "lunar_month")
                l_day = _parse_cd_int(data.get("lunar_day"), 1, "lunar_day")
                l_leap = bool(data.get("is_leap_month", False))
            else:
                assert existing is not None
                l_year = int(existing["lunar_year"] or today.year)
                l_month = int(existing["lunar_month"] or 1)
                l_day = int(existing["lunar_day"] or 1)
                l_leap = bool(data.get("is_leap_month", existing["is_leap_month"]))
            l_year = max(LUNAR_START_YEAR, min(LUNAR_END_YEAR, l_year))
            if l_month < 1 or l_month > 12:
                raise ValidationError(f"农历月份无效（需在 1..12 之间）：{l_month}")
            if l_day < 1 or l_day > 30:
                raise ValidationError(f"农历日期无效（需在 1..30 之间）：{l_day}")
            try:
                resolved_solar = lunar_to_solar(l_year, l_month, l_day, is_leap_month=l_leap)
            except ValueError as exc:
                raise ValidationError(str(exc)) from exc
            target_ymd = resolved_solar.isoformat()
        else:
            raw_target = str(
                data.get("target_date")
                if "target_date" in data
                else (existing["target_date"] if existing else today.isoformat())
            ).strip()
            solar_d = _parse_ymd_strict(raw_target)
            target_ymd = solar_d.isoformat()
            lunar_d = solar_to_lunar(solar_d)
            l_year = lunar_d.year
            l_month = lunar_d.month
            l_day = lunar_d.day
            l_leap = lunar_d.is_leap_month

        icon = str(data.get("icon") if "icon" in data else (existing["icon"] if existing else "🎯")).strip()[:8] or "🎯"
        color = str(
            data.get("color_theme") if "color_theme" in data else (existing["color_theme"] if existing else "sky")
        ).strip().lower()
        if color not in VALID_COUNTDOWN_COLORS:
            color = "sky"
        pinned = (
            1
            if bool(data["pinned"] if "pinned" in data else (existing["pinned"] if existing else False))
            else 0
        )
        note = str(data.get("note") if "note" in data else (existing["note"] if existing else "")).strip()[:300]
        now_iso = beijing_now_iso()

        final_id = cid or new_id("cd")
        with self.db.transaction() as conn:
            if existing is not None:
                conn.execute(
                    """
                    UPDATE countdown_days
                       SET title = ?, kind = ?, calendar_type = ?, target_date = ?,
                           lunar_year = ?, lunar_month = ?, lunar_day = ?, is_leap_month = ?,
                           repeat_rule = ?, icon = ?, color_theme = ?, pinned = ?, note = ?,
                           updated_at = ?
                     WHERE space_id = ? AND countdown_id = ?
                    """,
                    (
                        title, kind, cal_type, target_ymd,
                        l_year, l_month, l_day, 1 if l_leap else 0,
                        repeat_rule, icon, color, pinned, note,
                        now_iso, sp, final_id,
                    ),
                )
            else:
                conn.execute(
                    """
                    INSERT INTO countdown_days (
                        countdown_id, space_id, title, kind, calendar_type, target_date,
                        lunar_year, lunar_month, lunar_day, is_leap_month,
                        repeat_rule, icon, color_theme, pinned, note, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        final_id, sp, title, kind, cal_type, target_ymd,
                        l_year, l_month, l_day, 1 if l_leap else 0,
                        repeat_rule, icon, color, pinned, note, now_iso, now_iso,
                    ),
                )

        if self.sync_tracker is not None:
            self.sync_tracker.bump(sp, reason="countdown_saved")

        saved_row = self.db.query_one(
            "SELECT * FROM countdown_days WHERE space_id = ? AND countdown_id = ?",
            (sp, final_id),
        )
        assert saved_row is not None
        return self._project_row(saved_row, today)

    def delete_countdown(self, space_id: str, countdown_id: str) -> bool:
        sp = (space_id or "default").strip() or "default"
        cid = (countdown_id or "").strip()
        if not cid:
            return False
        with self.db.transaction() as conn:
            cur = conn.execute(
                "DELETE FROM countdown_days WHERE space_id = ? AND countdown_id = ?",
                (sp, cid),
            )
            deleted = cur.rowcount > 0
        if deleted and self.sync_tracker is not None:
            self.sync_tracker.bump(sp, reason="countdown_deleted")
        return deleted

    def project_range(self, space_id: str, range_start: date, range_end: date) -> dict[str, list[dict[str, Any]]]:
        """Project countdowns & recurring solar/lunar anniversaries onto `[range_start, range_end]`."""
        sp = (space_id or "default").strip() or "default"
        rows = self.db.query_all(
            "SELECT * FROM countdown_days WHERE space_id = ? ORDER BY pinned DESC, created_at ASC",
            (sp,),
        )
        today = date.fromisoformat(beijing_today_date())
        by_ymd: dict[str, list[dict[str, Any]]] = {}

        def _append_hit(hit_d: date, row: sqlite3.Row) -> None:
            if hit_d < range_start or hit_d > range_end:
                return
            ymd = hit_d.isoformat()
            by_ymd.setdefault(ymd, []).append(
                {
                    "countdown_id": str(row["countdown_id"]),
                    "title": str(row["title"]),
                    "icon": str(row["icon"] or "🎯"),
                    "kind": str(row["kind"] or "countdown"),
                    "color_theme": str(row["color_theme"] or "sky"),
                    "days_delta": (hit_d - today).days,
                }
            )

        years_in_range = set(range(max(LUNAR_START_YEAR, range_start.year - 1), min(LUNAR_END_YEAR, range_end.year + 1) + 1))

        for row in rows:
            kind = str(row["kind"] or "countdown")
            cal_type = str(row["calendar_type"] or "solar")
            repeat_rule = str(row["repeat_rule"] or "none")
            base_d = _parse_ymd_strict(str(row["target_date"]))
            is_yearly = (repeat_rule == "yearly") or (kind == "anniversary")

            if is_yearly:
                if cal_type == "lunar":
                    lm = int(row["lunar_month"] or 1)
                    ld = int(row["lunar_day"] or 1)
                    ll = bool(row["is_leap_month"])
                    for ly in sorted(years_in_range):
                        _append_hit(lunar_to_solar(ly, lm, ld, is_leap_month=ll), row)
                else:
                    for sy in range(range_start.year, range_end.year + 1):
                        _append_hit(_safe_solar_date(sy, base_d.month, base_d.day), row)
            elif repeat_rule == "monthly":
                cur_y, cur_m = range_start.year, range_start.month
                while (cur_y, cur_m) <= (range_end.year, range_end.month):
                    _append_hit(_safe_solar_date(cur_y, cur_m, base_d.day), row)
                    if cur_m == 12:
                        cur_y, cur_m = cur_y + 1, 1
                    else:
                        cur_m += 1
            else:
                _append_hit(base_d, row)

        return by_ymd
