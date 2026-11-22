"""RawNote domain model: immutable-by-default original user inputs with revision tracking."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
import re
from typing import Any
import uuid

from trace_memory.domain.exceptions import ValidationError


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


_TIME_RANGE_COLON_RE = re.compile(
    r"(?:(?P<p1>凌晨|早上|上午|中午|下午|傍晚|晚上|今晚|明晚|夜里)\s*)?"
    r"(?P<sh>\d{1,2}):(?P<sm>\d{2})"
    r"\s*(?:[-~～—–]|到|至)+\s*"
    r"(?:(?P<p2>凌晨|早上|上午|中午|下午|傍晚|晚上|今晚|明晚|夜里|次日|明天)\s*)?"
    r"(?P<eh>\d{1,2}):(?P<em>\d{2})"
)

_TIME_RANGE_CN_RE = re.compile(
    r"(?:(?P<p1>凌晨|早上|上午|中午|下午|傍晚|晚上|今晚|明晚|夜里)\s*)?"
    r"(?P<sh>\d{1,2})点(?:(?P<shalf>半)|(?P<sm>\d{1,2})分?)?"
    r"\s*(?:[-~～—–]|到|至)+\s*"
    r"(?:(?P<p2>凌晨|早上|上午|中午|下午|傍晚|晚上|今晚|明晚|夜里|次日|明天)\s*)?"
    r"(?P<eh>\d{1,2})点(?:(?P<ehalf>半)|(?P<em>\d{1,2})分?)?"
)

_EXPLICIT_YMD_RE = re.compile(r"(20\d{2})[-年/](\d{1,2})[-月/](\d{1,2})日?")


def _adjust_period_hour(hour: int, period: str) -> int:
    if period in ("下午", "傍晚", "晚上", "今晚", "明晚", "夜里") and 1 <= hour <= 11:
        return hour + 12
    if period == "中午" and 1 <= hour <= 10:
        return hour + 12
    if period == "凌晨" and hour == 12:
        return 0
    return hour


def extract_beijing_time_range(
    text: str, ref_dt: datetime | None = None
) -> tuple[str, str, int] | None:
    """Deterministically extract `(start_iso, end_iso, duration_minutes)` from a time range in text.

    Supports formats like:
    - `"今天21:00-23:00，我要写作业"`
    - `"晚上9点到11点写论文"`
    - `"明天下午2点半到4点开会"`
    - `"今天23:00-01:00部署发版"`
    """
    raw = str(text or "").strip()
    if not raw:
        return None

    m = _TIME_RANGE_COLON_RE.search(raw)
    is_colon = m is not None
    if m is None:
        m = _TIME_RANGE_CN_RE.search(raw)
    if m is None:
        return None

    base = to_beijing(ref_dt) if ref_dt is not None else beijing_now()
    base_d = base.date()

    # Anchor date in Beijing Time
    prefix_ctx = raw[: m.start() + 2]
    m_ymd = _EXPLICIT_YMD_RE.search(prefix_ctx) or _EXPLICIT_YMD_RE.search(raw)
    anchor_d: date | None = None
    if m_ymd:
        try:
            anchor_d = date(int(m_ymd.group(1)), int(m_ymd.group(2)), int(m_ymd.group(3)))
        except ValueError:
            anchor_d = None
    if anchor_d is None:
        if "后天" in raw:
            anchor_d = base_d + timedelta(days=2)
        elif any(k in raw for k in ("明天", "明日", "明早", "明晚")):
            anchor_d = base_d + timedelta(days=1)
        elif any(k in raw for k in ("昨天", "昨日")):
            anchor_d = base_d - timedelta(days=1)
        elif "前天" in raw:
            anchor_d = base_d - timedelta(days=2)
        else:
            anchor_d = base_d

    p1 = (m.group("p1") or "").strip()
    p2 = (m.group("p2") or "").strip()
    if not p1:
        # Look back a few chars for period cues immediately preceding digits (e.g. "明天晚上 21:00-23:00")
        tail_ctx = raw[max(0, m.start() - 4) : m.start()]
        for cue in ("凌晨", "早上", "上午", "中午", "下午", "傍晚", "晚上", "今晚", "明晚", "夜里"):
            if cue in tail_ctx:
                p1 = cue
                break

    try:
        sh_raw = int(m.group("sh"))
        eh_raw = int(m.group("eh"))
        if is_colon:
            sm = int(m.group("sm"))
            em = int(m.group("em"))
        else:
            sm = 30 if m.group("shalf") else int(m.group("sm") or 0)
            em = 30 if m.group("ehalf") else int(m.group("em") or 0)
    except (TypeError, ValueError):
        return None

    if not (0 <= sh_raw <= 23 and 0 <= sm <= 59 and 0 <= eh_raw <= 24 and 0 <= em <= 59):
        return None
    if eh_raw == 24 and em != 0:
        return None

    sh = _adjust_period_hour(sh_raw, p1)
    start_dt = datetime(anchor_d.year, anchor_d.month, anchor_d.day, sh, sm, 0, tzinfo=BEIJING_TZ)

    end_eh_str = m.group("eh")
    if eh_raw == 24:
        end_dt = datetime(anchor_d.year, anchor_d.month, anchor_d.day, 0, 0, 0, tzinfo=BEIJING_TZ) + timedelta(days=1)
    else:
        eh = _adjust_period_hour(eh_raw, p2) if p2 else eh_raw
        # Disambiguate omitted end-period (e.g. "晚上9点到11点" -> 21:00-23:00, "下午2点半到4点" -> 14:30-16:00)
        if not p2 and p1 in ("下午", "傍晚", "晚上", "今晚", "明晚", "夜里", "中午") and 1 <= eh <= 11:
            if not (sh >= 20 and eh <= 6) and (eh + 12) > sh:
                eh += 12
        end_dt = datetime(anchor_d.year, anchor_d.month, anchor_d.day, eh, em, 0, tzinfo=BEIJING_TZ)
        if p2 in ("次日", "明天") and end_dt <= start_dt:
            end_dt += timedelta(days=1)
        elif end_dt <= start_dt:
            # Handle ambiguous afternoon span without explicit cue (e.g. "2点-4点" vs "23:00-01:00")
            has_leading_zero = len(end_eh_str) == 2 and end_eh_str.startswith("0")
            if not is_colon and not has_leading_zero and sh < 20 and 1 <= eh <= 11 and (eh + 12) * 60 + em > sh * 60 + sm:
                eh += 12
                end_dt = datetime(anchor_d.year, anchor_d.month, anchor_d.day, eh, em, 0, tzinfo=BEIJING_TZ)
            else:
                end_dt += timedelta(days=1)

    dur_min = int((end_dt - start_dt).total_seconds() // 60)
    if not (15 <= dur_min <= 1440):
        return None
    return (
        start_dt.replace(microsecond=0).isoformat(),
        end_dt.replace(microsecond=0).isoformat(),
        dur_min,
    )


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
            raise ValidationError("space_id must not be empty")
        if self.source_type not in VALID_SOURCE_TYPES:
            raise ValidationError(f"Invalid source_type: {self.source_type}")
        if self.revision < 1:
            raise ValidationError("revision must be >= 1")
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
        for span in sorted((s for s in self.forgotten_spans if s), key=len, reverse=True):
            text = text.replace(span, "[REDACTED]")
        return text

    def to_dict(self) -> dict[str, Any]:
        if self.is_forgotten:
            return {
                "note_id": self.note_id,
                "space_id": self.space_id,
                "content": "",
                "source_type": self.source_type,
                "actor_id": self.actor_id,
                "revision": self.revision,
                "is_forgotten": True,
                "forgotten_spans": list(self.forgotten_spans),
                "event_time": self.event_time,
                "created_at": self.created_at,
                "updated_at": self.updated_at,
                "note_kind": self.note_kind,
                "title": "",
                "pinned": False,
                "tags": [],
                "mood": "",
                "journal_date": "",
                "extract_memory": self.extract_memory,
            }
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
