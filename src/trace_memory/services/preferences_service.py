"""Anti-bloat space/user UI preferences: Feature Toggles & Token-driven Theme Gallery."""

from __future__ import annotations

import json
from typing import Any

from trace_memory.domain.notes import beijing_now_iso
from trace_memory.domain.proposals import ValidationError
from trace_memory.services.sync_and_scheduler import SpaceSyncTracker
from trace_memory.storage.database import Database


THEMES_CATALOG: tuple[dict[str, Any], ...] = (
    {
        "id": "classic",
        "name": "纯净晴空",
        "subtitle": "清爽高对比白蓝 · 默认极简",
        "primary": "#3b82f6",
        "accent": "#7c3aed",
        "bg": "#f4f6fb",
        "surface": "#ffffff",
        "is_dark": False,
    },
    {
        "id": "westlake",
        "name": "西湖烟雨青",
        "subtitle": "江南青瓷碧水 · 远山断桥微插画",
        "primary": "#0d9488",
        "accent": "#0284c7",
        "bg": "#eff8f6",
        "surface": "#ffffff",
        "is_dark": False,
    },
    {
        "id": "autumn",
        "name": "秋林枫叶橙",
        "subtitle": "暖阳银杏琥珀 · 层林尽染微插画",
        "primary": "#ea580c",
        "accent": "#d97706",
        "bg": "#fdf6f0",
        "surface": "#ffffff",
        "is_dark": False,
    },
    {
        "id": "golden_gate",
        "name": "金门落日红",
        "subtitle": "朱砂海湾霞光 · 长桥剪影微插画",
        "primary": "#dc2626",
        "accent": "#e11d48",
        "bg": "#fef4f4",
        "surface": "#ffffff",
        "is_dark": False,
    },
    {
        "id": "snow_peak",
        "name": "雪山冰川蓝",
        "subtitle": "凛冽日照雪峰 · 冰川折线微插画",
        "primary": "#0284c7",
        "accent": "#4f46e5",
        "bg": "#eff6ff",
        "surface": "#ffffff",
        "is_dark": False,
    },
    {
        "id": "deep_forest",
        "name": "深林暗夜",
        "subtitle": "沉浸护眼松石暗色 · 星夜松影微插画",
        "primary": "#10b981",
        "accent": "#38bdf8",
        "bg": "#0b1512",
        "surface": "#13221d",
        "is_dark": True,
    },
)

VALID_THEME_IDS: frozenset[str] = frozenset(t["id"] for t in THEMES_CATALOG)

DEFAULT_ENABLED_MODULES: dict[str, bool] = {
    "calendar": True,
    "focus": True,
    "notes": True,
    "journal": True,
    "countdown": True,
    "supervision": True,
}


class PreferencesService:
    """Manages cross-device theme tokens and pluggable feature toggles per space."""

    def __init__(self, db: Database, sync_tracker: SpaceSyncTracker | None = None) -> None:
        self.db = db
        self.sync_tracker = sync_tracker

    def get_preferences(self, space_id: str) -> dict[str, Any]:
        sp = (space_id or "default").strip() or "default"
        row = self.db.query_one(
            "SELECT * FROM space_preferences WHERE space_id = ?",
            (sp,),
        )
        modules = dict(DEFAULT_ENABLED_MODULES)
        theme_id = "classic"
        pomodoro_minutes = 25
        extra: dict[str, Any] = {}
        updated_at = ""

        if row is not None:
            raw_theme = str(row["theme_id"] or "classic").strip()
            if raw_theme in VALID_THEME_IDS:
                theme_id = raw_theme
            try:
                stored_mods = json.loads(row["enabled_modules_json"] or "{}")
                if isinstance(stored_mods, dict):
                    for k in DEFAULT_ENABLED_MODULES:
                        if k in stored_mods:
                            modules[k] = bool(stored_mods[k])
            except (TypeError, json.JSONDecodeError):
                pass
            pomodoro_minutes = max(5, min(180, int(row["pomodoro_minutes"] or 25)))
            try:
                parsed_extra = json.loads(row["extra_json"] or "{}")
                if isinstance(parsed_extra, dict):
                    extra = parsed_extra
            except (TypeError, json.JSONDecodeError):
                pass
            updated_at = str(row["updated_at"] or "")

        return {
            "space_id": sp,
            "theme_id": theme_id,
            "enabled_modules": modules,
            "pomodoro_minutes": pomodoro_minutes,
            "extra": extra,
            "updated_at": updated_at,
            "themes_catalog": list(THEMES_CATALOG),
        }

    def update_preferences(self, space_id: str, patch: dict[str, Any]) -> dict[str, Any]:
        sp = (space_id or "default").strip() or "default"
        current = self.get_preferences(sp)

        theme_id = current["theme_id"]
        if "theme_id" in patch and patch["theme_id"] is not None:
            cand = str(patch["theme_id"]).strip()
            if cand not in VALID_THEME_IDS:
                raise ValidationError(f"不支持的主题标识: {cand}")
            theme_id = cand

        modules = dict(current["enabled_modules"])
        raw_mods = patch.get("enabled_modules")
        if raw_mods is not None:
            if not isinstance(raw_mods, dict):
                raise ValidationError("enabled_modules 必须为对象")
            for k, v in raw_mods.items():
                if k in DEFAULT_ENABLED_MODULES:
                    modules[k] = bool(v)

        # Support single-key toggle shorthand: {"module_key": "journal", "enabled": false}
        mod_key = str(patch.get("module_key") or "").strip()
        if mod_key:
            if mod_key not in DEFAULT_ENABLED_MODULES:
                raise ValidationError(f"不支持的功能模块开关: {mod_key}")
            modules[mod_key] = bool(patch.get("enabled", True))

        pomodoro_minutes = int(current["pomodoro_minutes"])
        if "pomodoro_minutes" in patch and patch["pomodoro_minutes"] is not None:
            try:
                pm = int(patch["pomodoro_minutes"])
            except (TypeError, ValueError) as exc:
                raise ValidationError("pomodoro_minutes 必须为整数") from exc
            if pm < 1 or pm > 180:
                raise ValidationError("番茄钟时长须在 1~180 分钟之间")
            pomodoro_minutes = pm

        extra = dict(current["extra"])
        if isinstance(patch.get("extra"), dict):
            for k, v in patch["extra"].items():
                if isinstance(k, str) and len(k) <= 48:
                    extra[k] = v

        now = beijing_now_iso()
        with self.db.transaction() as conn:
            conn.execute(
                """
                INSERT INTO space_preferences (
                    space_id, theme_id, enabled_modules_json, pomodoro_minutes, extra_json, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(space_id) DO UPDATE SET
                    theme_id = excluded.theme_id,
                    enabled_modules_json = excluded.enabled_modules_json,
                    pomodoro_minutes = excluded.pomodoro_minutes,
                    extra_json = excluded.extra_json,
                    updated_at = excluded.updated_at
                """,
                (
                    sp,
                    theme_id,
                    json.dumps(modules, ensure_ascii=False),
                    pomodoro_minutes,
                    json.dumps(extra, ensure_ascii=False),
                    now,
                ),
            )

        if self.sync_tracker is not None:
            self.sync_tracker.bump(sp, reason="preferences_updated")

        return self.get_preferences(sp)
