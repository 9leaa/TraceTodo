"""RawNote domain model: immutable-by-default original user inputs with revision tracking."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any
import uuid


VALID_SOURCE_TYPES = ("cli", "mcp", "api", "web", "markdown_edit", "system")
VALID_NOTE_KINDS = ("note", "memo", "journal")

# Fixed China Standard Time (Beijing Time, Asia/Shanghai, UTC+8) for Mainland China users
BEIJING_TZ = timezone(timedelta(hours=8), name="CST")
_CN_WEEKDAYS = ("星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日")


def beijing_now() -> datetime:
    """Return current wall-clock datetime anchored to Beijing Time (UTC+8)."""
    return datetime.now(BEIJING_TZ)


def to_beijing(dt: datetime) -> datetime:
    """Convert an aware datetime to Beijing Time, or attach Beijing Time if naive."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=BEIJING_TZ)
    return dt.astimezone(BEIJING_TZ)


def beijing_now_iso() -> str:
    """Return current Beijing Time as second-precision ISO-8601 string with `+08:00`."""
    return beijing_now().replace(microsecond=0).isoformat()


def beijing_today_date(now: datetime | None = None) -> str:
    """Return `YYYY-MM-DD` date string anchored to Beijing Time (UTC+8)."""
    ref = to_beijing(now) if now is not None else beijing_now()
    return ref.strftime("%Y-%m-%d")


def format_beijing_prompt_clock(now: datetime | None = None) -> str:
    """Return a clear Beijing Time clock & relative-date anchor block for LLM prompts."""
    ref = to_beijing(now) if now is not None else beijing_now()
    weekday = _CN_WEEKDAYS[ref.weekday()]
    day_before_yesterday = (ref - timedelta(days=2)).strftime("%Y-%m-%d")
    yesterday = (ref - timedelta(days=1)).strftime("%Y-%m-%d")
    today = ref.strftime("%Y-%m-%d")
    tomorrow = (ref + timedelta(days=1)).strftime("%Y-%m-%d")
    day_after_tomorrow = (ref + timedelta(days=2)).strftime("%Y-%m-%d")
    return (
        f"{ref.strftime('%Y-%m-%d %H:%M:%S')}（{weekday}，北京时间 Asia/Shanghai UTC+8 / +08:00）\n"
        f"相对日期锚点（严格以北京时间为准）："
        f"前天={day_before_yesterday}、昨天={yesterday}、今天={today}、明天={tomorrow}、后天={day_after_tomorrow}"
    )


def utc_now_iso() -> str:
    """Backward-compatible timestamp helper now unified on Beijing Time (`+08:00`)."""
    return beijing_now_iso()


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


@dataclass(frozen=True)
class RawNote:
    """Original input note representing ground-truth evidence.

    Why `revision` and `event_time` both exist:
    - `created_at` is wall-clock ingestion time; `event_time` captures when the user says
      the event happened (e.g. backfilling an older event so C01 late-arriving old history
      never overwrites newer state).
    - `revision` increments whenever the user edits the raw note (C02), invalidating
      memories that depended on an older revision.
    """

    note_id: str
    space_id: str
    content: str
    source_type: str = "cli"
    actor_id: str = "user"
    revision: int = 1
    is_forgotten: bool = False
    forgotten_spans: tuple[str, ...] = field(default_factory=tuple)
    event_time: str = ""
    created_at: str = ""
    updated_at: str = ""
    note_kind: str = "note"
    title: str = ""
    pinned: bool = False
    tags: tuple[str, ...] = field(default_factory=tuple)
    mood: str = ""
    journal_date: str = ""
    extract_memory: bool = True

    def __post_init__(self) -> None:
        if not self.space_id or not self.space_id.strip():
            raise ValueError("space_id must not be empty")
        if self.source_type not in VALID_SOURCE_TYPES:
            raise ValueError(f"Invalid source_type: {self.source_type}")
        if self.revision < 1:
            raise ValueError("revision must be >= 1")
        now = utc_now_iso()
        if not self.created_at:
            object.__setattr__(self, "created_at", now)
        if not self.updated_at:
            object.__setattr__(self, "updated_at", self.created_at)
        if not self.event_time:
            object.__setattr__(self, "event_time", self.created_at)
        nk = str(self.note_kind or "note").strip().lower()
        if nk not in VALID_NOTE_KINDS:
            nk = "note"
        object.__setattr__(self, "note_kind", nk)
        object.__setattr__(self, "title", str(self.title or "").strip()[:200])
        object.__setattr__(self, "pinned", bool(self.pinned))
        clean_tags = tuple(dict.fromkeys(str(t).strip().lstrip("#")[:32] for t in (self.tags or ()) if str(t).strip().lstrip("#")))
        object.__setattr__(self, "tags", clean_tags)
        object.__setattr__(self, "mood", str(self.mood or "").strip()[:24])
        j_date = str(self.journal_date or "").strip()[:10]
        if nk == "journal" and not j_date:
            j_date = str(self.event_time or now)[:10]
        object.__setattr__(self, "journal_date", j_date)
        object.__setattr__(self, "extract_memory", bool(self.extract_memory))

    def visible_content(self) -> str:
        """Return content with any partially forgotten spans redacted (C07)."""
        if self.is_forgotten:
            return ""
        text = self.content
        for span in self.forgotten_spans:
            if span:
                text = text.replace(span, "[REDACTED]")
        return text

    def to_dict(self) -> dict[str, Any]:
        return {
            "note_id": self.note_id,
            "space_id": self.space_id,
            "content": self.visible_content(),
            "source_type": self.source_type,
            "actor_id": self.actor_id,
            "revision": self.revision,
            "is_forgotten": self.is_forgotten,
            "forgotten_spans": list(self.forgotten_spans),
            "event_time": self.event_time,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "note_kind": self.note_kind,
            "title": self.title,
            "pinned": self.pinned,
            "tags": list(self.tags),
            "mood": self.mood,
            "journal_date": self.journal_date,
            "extract_memory": self.extract_memory,
        }
