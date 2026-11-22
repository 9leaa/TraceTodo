"""Low-cost defenses: local noise filtering, rate limiting, duplicate flood blocking, and daily quota guards."""

from __future__ import annotations

from collections import Counter, deque
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import re
import threading
import time
from typing import Any

from trace_memory.config import TraceMemoryConfig
from trace_memory.domain.notes import beijing_today_date, new_id, utc_now_iso
from trace_memory.domain.proposals import QuotaExceededError, RateLimitError, ValidationError
from trace_memory.storage.database import Database


def today_utc_date() -> str:
    """Daily usage bucket date anchored to Beijing Time (UTC+8, resets at 00:00 CST)."""
    return beijing_today_date()


_MEANINGFUL_CHAR_RE = re.compile(r"[a-zA-Z0-9\u4e00-\u9fff]")
_ASCII_WORD_RE = re.compile(r"^[a-zA-Z0-9_.-]+$")

_NOISE_PHRASES = frozenset(
    {
        "你好", "您好", "在吗", "在不在", "有人吗", "测试", "测一下", "测试一下",
        "test", "testing", "hello", "hi", "hey", "yo", "sup",
        "ok", "okay", "kk", "收到", "好的", "好滴", "好吧", "行", "行吧",
        "嗯", "嗯嗯", "嗯啊", "哦", "哦哦", "呵呵", "哈哈", "哈哈哈", "哈哈哈哈",
        "嘿嘿", "嘻嘻", "哇", "无聊", "随便说说", "打卡", "签到", "路过", "飘过",
        "111", "222", "666", "888", "123", "1234", "12345", "123456", "000",
        "asdf", "asdfg", "asdfgh", "asdfghjkl", "qwerty", "qwertyuiop", "zxcvbn",
        "aaa", "aaaa", "bbb", "ccc", "xxx", "yyy", "zzz", "null", "none", "undefined",
    }
)


@dataclass(frozen=True)
class InputGuardResult:
    allowed: bool
    reason: str = ""
    category: str = ""  # "" | "empty" | "too_long" | "noise_filler" | "gibberish" | "too_short"


class SystemSettingsStore:
    """Persistent runtime settings backed by SQLite `system_settings` + `TraceMemoryConfig` defaults."""

    EDITABLE_KEYS = (
        "allow_registration",
        "noise_filter_enabled",
        "max_note_chars",
        "min_meaningful_chars",
        "rate_limit_per_minute",
        "duplicate_window_seconds",
        "default_daily_note_limit",
        "default_daily_llm_limit",
        "default_daily_token_limit",
    )

    def __init__(self, db: Database, config: TraceMemoryConfig) -> None:
        self.db = db
        self.config = config
        self._lock = threading.Lock()
        self._cache: dict[str, Any] | None = None

    def _defaults(self) -> dict[str, Any]:
        return {
            "allow_registration": bool(self.config.allow_registration),
            "noise_filter_enabled": bool(self.config.noise_filter_enabled),
            "max_note_chars": int(self.config.max_note_chars),
            "min_meaningful_chars": int(self.config.min_meaningful_chars),
            "rate_limit_per_minute": int(self.config.rate_limit_per_minute),
            "duplicate_window_seconds": int(self.config.duplicate_window_seconds),
            "default_daily_note_limit": int(self.config.default_daily_note_limit),
            "default_daily_llm_limit": int(self.config.default_daily_llm_limit),
            "default_daily_token_limit": int(self.config.default_daily_token_limit),
        }

    def get_all(self) -> dict[str, Any]:
        with self._lock:
            if self._cache is not None:
                return dict(self._cache)
            merged = self._defaults()
            rows = self.db.query_all("SELECT key, value_json FROM system_settings")
            for r in rows:
                k = str(r["key"])
                if k in merged:
                    try:
                        merged[k] = json.loads(r["value_json"])
                    except Exception:
                        pass
            self._cache = merged
            return dict(merged)

    @staticmethod
    def _coerce_int_setting(key: str, val: Any, lo: int, hi: int) -> int:
        if isinstance(val, bool):
            raise ValidationError(f"设置项 {key} 必须为整数")
        try:
            parsed = int(val)
        except (TypeError, ValueError) as exc:
            raise ValidationError(f"设置项 {key} 必须为整数") from exc
        return max(lo, min(hi, parsed))

    def update(self, patch: dict[str, Any]) -> dict[str, Any]:
        current = self.get_all()
        clean: dict[str, Any] = {}
        int_bounds = {
            "max_note_chars": (100, 20000),
            "min_meaningful_chars": (1, 20),
            "rate_limit_per_minute": (3, 1000),
            "duplicate_window_seconds": (0, 3600),
            "default_daily_note_limit": (5, 100000),
            "default_daily_llm_limit": (5, 100000),
            "default_daily_token_limit": (1000, 50_000_000),
        }
        for k in self.EDITABLE_KEYS:
            if k not in patch:
                continue
            val = patch[k]
            if k in ("allow_registration", "noise_filter_enabled"):
                clean[k] = bool(val)
            elif k in int_bounds:
                lo, hi = int_bounds[k]
                clean[k] = self._coerce_int_setting(k, val, lo, hi)

        if clean:
            now = utc_now_iso()
            with self.db.transaction() as conn:
                for k, v in clean.items():
                    conn.execute(
                        """
                        INSERT INTO system_settings (key, value_json, updated_at)
                        VALUES (?, ?, ?)
                        ON CONFLICT(key) DO UPDATE SET
                            value_json = excluded.value_json,
                            updated_at = excluded.updated_at
                        """,
                        (k, json.dumps(v), now),
                    )
            with self._lock:
                self._cache = None
        return self.get_all()


class InputGuardService:
    """Zero-cost local heuristic filter catching useless/spam inputs before Embedding or LLM calls."""

    def __init__(self, settings: SystemSettingsStore) -> None:
        self.settings = settings

    @staticmethod
    def _normalize_plain(text: str) -> str:
        return "".join(_MEANINGFUL_CHAR_RE.findall(text)).lower()

    @staticmethod
    def _is_repetitive_spam(norm: str) -> bool:
        if len(norm) < 4:
            return False
        counts = Counter(norm)
        most_common_cnt = counts.most_common(1)[0][1]
        if most_common_cnt / len(norm) >= 0.72:
            return True
        if len(norm) >= 6 and len(counts) <= 2:
            return True
        return False

    @staticmethod
    def _is_keyboard_smash(raw: str, norm: str) -> bool:
        stripped = raw.strip()
        if not stripped or not _ASCII_WORD_RE.match(stripped):
            return False
        # Skip short identifiers/codes with digits (e.g., ERR_4029)
        if any(ch.isdigit() for ch in stripped) and ("_" in stripped or "-" in stripped):
            return False
        if len(norm) >= 8 and norm.isalpha():
            vowels = sum(1 for ch in norm if ch in "aeiou")
            if vowels / len(norm) < 0.14:
                return True
        return False

    def inspect_note(self, content: str) -> InputGuardResult:
        cfg = self.settings.get_all()
        text = content.strip()
        if not text:
            return InputGuardResult(False, "输入内容不能为空", "empty")

        max_chars = int(cfg.get("max_note_chars", 2000))
        if len(text) > max_chars:
            return InputGuardResult(
                False,
                f"单条内容过长（{len(text)} 字），上限 {max_chars} 字，请分段输入",
                "too_long",
            )

        if not bool(cfg.get("noise_filter_enabled", True)):
            return InputGuardResult(True)

        # Pure URL with no user annotation provides weak personal memory value unless described
        norm = self._normalize_plain(text)
        if not norm:
            return InputGuardResult(False, "仅包含标点或表情符号，未触发 AI 抽取", "noise_filler")

        if norm in _NOISE_PHRASES or text.lower() in _NOISE_PHRASES:
            return InputGuardResult(False, "识别为常见寒暄或测试水话，已本地拦截", "noise_filler")

        if self._is_repetitive_spam(norm):
            return InputGuardResult(False, "检测到高重复无意义字符刷屏，已本地拦截", "gibberish")

        if self._is_keyboard_smash(text, norm):
            return InputGuardResult(False, "检测到疑似随机键盘乱码，已本地拦截", "gibberish")

        min_chars = int(cfg.get("min_meaningful_chars", 3))
        if len(norm) < min_chars:
            return InputGuardResult(
                False,
                f"有效信息过短（不足 {min_chars} 个字），请补充具体待办或事实",
                "too_short",
            )

        return InputGuardResult(True)

    def inspect_ask(self, question: str) -> InputGuardResult:
        cfg = self.settings.get_all()
        text = question.strip()
        if not text:
            return InputGuardResult(False, "问题不能为空", "empty")

        max_chars = int(cfg.get("max_note_chars", 2000))
        if len(text) > max_chars:
            return InputGuardResult(False, f"问题过长（上限 {max_chars} 字）", "too_long")

        if not bool(cfg.get("noise_filter_enabled", True)):
            return InputGuardResult(True)

        norm = self._normalize_plain(text)
        if not norm or len(norm) < 2:
            return InputGuardResult(False, "请描述具体想检索的问题或关键词", "too_short")
        if norm in _NOISE_PHRASES or self._is_repetitive_spam(norm) or self._is_keyboard_smash(text, norm):
            return InputGuardResult(False, "检测到无效测试或寒暄，未消耗 AI 问答额度", "noise_filler")
        return InputGuardResult(True)


class RateLimitAndQuotaService:
    """Thread-safe sliding-window rate limiter, duplicate flood guard, and SQLite daily quota ledger."""

    def __init__(self, db: Database, settings: SystemSettingsStore) -> None:
        self.db = db
        self.settings = settings
        self._lock = threading.Lock()
        self._rate_buckets: dict[str, deque[float]] = {}
        self._recent_notes: dict[str, deque[tuple[str, float]]] = {}

    def check_rate_limit(
        self,
        bucket_key: str,
        limit: int | None = None,
        window_seconds: int = 60,
        action_label: str = "请求",
    ) -> None:
        cfg = self.settings.get_all()
        eff_limit = int(limit if limit is not None else cfg.get("rate_limit_per_minute", 25))
        if eff_limit <= 0:
            return
        now = time.monotonic()
        cutoff = now - window_seconds
        with self._lock:
            if len(self._rate_buckets) > 4096:
                stale_keys = [
                    k for k, dq in self._rate_buckets.items()
                    if not dq or dq[-1] <= cutoff
                ]
                for k in stale_keys:
                    del self._rate_buckets[k]
                while len(self._rate_buckets) > 4096:
                    self._rate_buckets.pop(next(iter(self._rate_buckets)))
            q = self._rate_buckets.setdefault(bucket_key, deque())
            while q and q[0] <= cutoff:
                q.popleft()
            if len(q) >= eff_limit:
                retry_after = max(1, int(window_seconds - (now - q[0])) + 1)
                raise RateLimitError(
                    f"{action_label}过于频繁，请 {retry_after} 秒后再试",
                    retry_after_seconds=retry_after,
                )
            q.append(now)

    def check_duplicate_note(self, space_id: str, content: str) -> tuple[bool, str]:
        cfg = self.settings.get_all()
        window = int(cfg.get("duplicate_window_seconds", 120))
        if window <= 0:
            return False, ""
        digest = hashlib.sha256(re.sub(r"\s+", " ", content.strip().lower()).encode("utf-8")).hexdigest()[:24]
        now = time.monotonic()
        cutoff = now - window
        with self._lock:
            if len(self._recent_notes) > 2048:
                stale_spaces = [
                    sp for sp, dq in self._recent_notes.items()
                    if not dq or dq[-1][1] <= cutoff
                ]
                for sp in stale_spaces:
                    del self._recent_notes[sp]
                while len(self._recent_notes) > 2048:
                    self._recent_notes.pop(next(iter(self._recent_notes)))
            q = self._recent_notes.setdefault(space_id, deque())
            while q and q[0][1] <= cutoff:
                q.popleft()
            dup_count = sum(1 for h, _ in q if h == digest)
            if dup_count >= 1:
                return True, f"你在 {window} 秒内刚提交过完全相同的内容，已跳过重复 AI 处理"
            q.append((digest, now))
            if len(q) > 200:
                q.popleft()
        return False, ""

    def get_usage(self, scope_key: str, usage_date: str | None = None) -> dict[str, int]:
        day = usage_date or today_utc_date()
        row = self.db.query_one(
            """
            SELECT notes_count, blocked_noise_count, llm_calls, embed_calls, tokens_used
            FROM daily_usage WHERE usage_date = ? AND scope_key = ?
            """,
            (day, scope_key),
        )
        if row is None:
            return {
                "notes_count": 0,
                "blocked_noise_count": 0,
                "llm_calls": 0,
                "embed_calls": 0,
                "tokens_used": 0,
            }
        return {
            "notes_count": int(row["notes_count"]),
            "blocked_noise_count": int(row["blocked_noise_count"]),
            "llm_calls": int(row["llm_calls"]),
            "embed_calls": int(row["embed_calls"]),
            "tokens_used": int(row["tokens_used"]),
        }

    def record_usage(
        self,
        scope_key: str,
        *,
        notes: int = 0,
        blocked_noise: int = 0,
        llm_calls: int = 0,
        embed_calls: int = 0,
        tokens_used: int = 0,
    ) -> None:
        if not any((notes, blocked_noise, llm_calls, embed_calls, tokens_used)):
            return
        day = today_utc_date()
        now = utc_now_iso()
        with self.db.transaction() as conn:
            conn.execute(
                """
                INSERT INTO daily_usage
                    (usage_date, scope_key, notes_count, blocked_noise_count, llm_calls, embed_calls, tokens_used, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(usage_date, scope_key) DO UPDATE SET
                    notes_count = notes_count + excluded.notes_count,
                    blocked_noise_count = blocked_noise_count + excluded.blocked_noise_count,
                    llm_calls = llm_calls + excluded.llm_calls,
                    embed_calls = embed_calls + excluded.embed_calls,
                    tokens_used = tokens_used + excluded.tokens_used,
                    updated_at = excluded.updated_at
                """,
                (day, scope_key, notes, blocked_noise, llm_calls, embed_calls, tokens_used, now),
            )

    def check_daily_quota(
        self,
        scope_key: str,
        *,
        note_limit: int | None = None,
        llm_limit: int | None = None,
        token_limit: int | None = None,
        require_note_slot: bool = False,
        require_llm_slot: bool = True,
    ) -> dict[str, int]:
        cfg = self.settings.get_all()
        eff_note_lim = int(note_limit if note_limit is not None else cfg["default_daily_note_limit"])
        eff_llm_lim = int(llm_limit if llm_limit is not None else cfg["default_daily_llm_limit"])
        eff_tok_lim = int(token_limit if token_limit is not None else cfg["default_daily_token_limit"])

        usage = self.get_usage(scope_key)
        if require_note_slot and usage["notes_count"] >= eff_note_lim:
            raise QuotaExceededError(
                f"今日录入次数已达上限（{usage['notes_count']}/{eff_note_lim}），请明日再试或联系管理员提额"
            )
        if require_llm_slot and usage["llm_calls"] >= eff_llm_lim:
            raise QuotaExceededError(
                f"今日 AI 调用次数已达上限（{usage['llm_calls']}/{eff_llm_lim}），基础任务手动操作仍可正常使用"
            )
        if require_llm_slot and usage["tokens_used"] >= eff_tok_lim:
            raise QuotaExceededError(
                f"今日 AI Token 配额已用尽（{usage['tokens_used']}/{eff_tok_lim}），基础任务管理仍可正常使用"
            )
        return usage

    def log_security_event(
        self,
        *,
        action: str,
        status: str,
        actor_user_id: str = "",
        space_id: str = "",
        detail: dict[str, Any] | None = None,
        client_ip: str = "",
    ) -> None:
        with self.db.transaction() as conn:
            conn.execute(
                """
                INSERT INTO security_audit_logs
                    (log_id, actor_user_id, space_id, action, status, detail_json, client_ip, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    new_id("sec"),
                    actor_user_id,
                    space_id,
                    action,
                    status,
                    json.dumps(detail or {}, ensure_ascii=False),
                    client_ip,
                    utc_now_iso(),
                ),
            )
