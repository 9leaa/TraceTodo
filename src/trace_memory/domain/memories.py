"""Structured Memory domain models, 4-type schemas, and Eisenhower 4-Quadrant rules."""

from __future__ import annotations

from calendar import monthrange
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import hashlib
import math
from typing import Any

from trace_memory.domain.exceptions import ValidationError
from trace_memory.domain.notes import BEIJING_TZ, beijing_now, to_beijing, utc_now_iso


_QUADRANT_BASE = {
    "Q1_do_first": 80.0,
    "Q2_schedule": 60.0,
    "Q3_delegate": 40.0,
    "Q4_eliminate": 15.0,
}


def _opt_str(raw: Any, default: str = "") -> str:
    return default if raw is None else str(raw)


def _safe_int_field(raw: Any, name: str, default: int | None = 0) -> int | None:
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        return default
    if isinstance(raw, bool):
        raise ValidationError(f"Field {name!r} must be an integer, got bool")
    try:
        return int(raw)
    except (TypeError, ValueError) as exc:
        raise ValidationError(f"Field {name!r} must be an integer") from exc


def _safe_float_field(raw: Any, name: str, default: float = 0.0) -> float:
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        return default
    if isinstance(raw, bool):
        raise ValidationError(f"Field {name!r} must be numeric, got bool")
    try:
        val = float(raw)
    except (TypeError, ValueError) as exc:
        raise ValidationError(f"Field {name!r} must be numeric") from exc
    if not math.isfinite(val):
        raise ValidationError(f"Field {name!r} must be finite")
    return val


def _safe_str_tuple(raw: Any, name: str) -> tuple[str, ...]:
    if raw is None:
        return ()
    if not isinstance(raw, (list, tuple)):
        raise ValidationError(f"Field {name!r} must be a list of strings")
    return tuple(str(x) for x in raw if x is not None and str(x))


def validate_safe_permalink(permalink: str) -> str:
    """Validate that a permalink cannot escape export roots or collide with index.md."""
    raw = str(permalink or "")
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in raw):
        raise ValidationError("permalink must not contain control characters")
    if "\\" in raw:
        raise ValidationError("permalink must not contain backslashes")
    clean = raw.strip().strip("/")
    if not clean:
        raise ValidationError("permalink must not be empty")
    segs = clean.split("/")
    if any(not seg or seg in (".", "..") for seg in segs):
        raise ValidationError("permalink contains invalid path segment")
    if clean.lower() == "index":
        raise ValidationError("permalink 'index' is reserved")
    return clean

MEMORY_TYPES = ("task", "preference", "semantic", "episodic")
ASSERTION_KINDS = ("stated", "inferred")
MEMORY_STATUSES = ("active", "superseded", "pending_review", "forgotten")

TASK_STATUSES = ("todo", "done")
TASK_CLOSE_REASONS = ("", "completed", "cancelled", "dropped")

COLOR_THEMES = ("lavender", "pink", "sky", "butter", "mint", "peach")
TASK_MODES = ("personal", "multiplayer")
REPEAT_RULES = ("never", "daily", "weekdays", "weekly", "monthly")

QUADRANTS = (
    "Q1_do_first",   # 重要且紧急 (Important & Urgent)
    "Q2_schedule",   # 重要不紧急 (Important & Not Urgent)
    "Q3_delegate",   # 紧急不重要 (Not Important & Urgent)
    "Q4_eliminate",  # 不重要不紧急 (Not Important & Not Urgent)
)

QUADRANT_LABELS = {
    "Q1_do_first": "Q1 重要且紧急（马上做）",
    "Q2_schedule": "Q2 重要不紧急（计划做）",
    "Q3_delegate": "Q3 紧急不重要（速办/授权）",
    "Q4_eliminate": "Q4 不重要不紧急（稍后/精简）",
}


def parse_iso_datetime(value: str) -> datetime | None:
    if not value or not value.strip():
        return None
    raw = value.strip()
    # Handle plain YYYY-MM-DD BEFORE datetime.fromisoformat (Python 3.11+ parses YYYY-MM-DD as 00:00:00!)
    if len(raw) == 10 and raw[4] == "-" and raw[7] == "-":
        try:
            return datetime.strptime(raw, "%Y-%m-%d").replace(
                hour=23, minute=59, second=59, tzinfo=BEIJING_TZ
            )
        except ValueError:
            return None
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(raw)
        return to_beijing(dt)
    except ValueError:
        try:
            dt = datetime.strptime(raw[:10], "%Y-%m-%d").replace(
                hour=23, minute=59, second=59, tzinfo=BEIJING_TZ
            )
            return dt
        except ValueError:
            return None


def compute_effective_urgency_and_quadrant(
    importance: int,
    base_urgency: int,
    due_at: str = "",
    now: datetime | None = None,
    has_blockers: bool = False,
) -> tuple[int, str, float]:
    """Compute effective urgency (1..5), Eisenhower quadrant, and continuous priority score.

    Why dynamic deadline escalation exists:
    A task marked non-urgent last week becomes urgent when its deadline is within 48 hours.
    Computing effective urgency dynamically prevents stale urgency scores while keeping
    importance grounded in long-term goals/preferences.
    """
    imp = max(1, min(5, int(importance)))
    urg = max(1, min(5, int(base_urgency)))
    ref_now = to_beijing(now) if now is not None else beijing_now()

    deadline_bonus = 0.0
    due_dt = parse_iso_datetime(due_at)
    if due_dt is not None:
        hours_left = (due_dt - ref_now).total_seconds() / 3600.0
        if hours_left <= 0:
            # Overdue
            urg = 5
            deadline_bonus = 15.0
        elif hours_left <= 24:
            urg = max(urg, 5)
            deadline_bonus = 10.0
        elif hours_left <= 48:
            urg = max(urg, 4)
            deadline_bonus = 6.0
        elif hours_left <= 168:  # Within 7 days
            urg = max(urg, 3)
            deadline_bonus = 2.5

    is_important = imp >= 4
    is_urgent = urg >= 4

    if is_important and is_urgent:
        quadrant = "Q1_do_first"
        quadrant_base = 80.0
    elif is_important and not is_urgent:
        quadrant = "Q2_schedule"
        quadrant_base = 60.0
    elif not is_important and is_urgent:
        quadrant = "Q3_delegate"
        quadrant_base = 40.0
    else:
        quadrant = "Q4_eliminate"
        quadrant_base = 15.0

    blocker_penalty = -3.0 if has_blockers else 0.0
    priority_score = round(
        quadrant_base + imp * 5.0 + urg * 4.0 + deadline_bonus + blocker_penalty, 2
    )
    return urg, quadrant, priority_score


def _normalize_subtasks(raw: Any) -> tuple[dict[str, Any], ...]:
    if not raw or not isinstance(raw, (list, tuple)):
        return ()
    items: list[dict[str, Any]] = []
    for idx, entry in enumerate(raw):
        if isinstance(entry, str):
            title = entry.strip()
            if not title:
                continue
            sid = "sub_" + hashlib.md5(f"{idx}:{title}".encode("utf-8")).hexdigest()[:8]
            items.append({"id": sid, "title": title, "done": False})
        elif isinstance(entry, dict):
            title = str(entry.get("title") or "").strip()
            if not title:
                continue
            sid = str(entry.get("id") or "").strip() or (
                "sub_" + hashlib.md5(f"{idx}:{title}".encode("utf-8")).hexdigest()[:8]
            )
            items.append({"id": sid, "title": title, "done": bool(entry.get("done", False))})
    return tuple(items)


def _normalize_dict_tuple(raw: Any) -> tuple[dict[str, Any], ...]:
    if not raw or not isinstance(raw, (list, tuple)):
        return ()
    return tuple(dict(x) for x in raw if isinstance(x, dict))


def compute_next_repeat_due_at(
    due_at: str, repeat_rule: str, ref_now: datetime | None = None
) -> str:
    """Compute the next cycle's ISO due_at timestamp for recurring tasks."""
    rule = repeat_rule if repeat_rule in REPEAT_RULES else "never"
    if rule == "never":
        return due_at
    base_now = to_beijing(ref_now) if ref_now is not None else beijing_now()
    dt = parse_iso_datetime(due_at)
    if dt is None:
        dt = base_now.replace(hour=23, minute=59, second=59, microsecond=0)
    else:
        dt = to_beijing(dt)

    if rule == "daily":
        next_dt = dt + timedelta(days=1)
    elif rule == "weekdays":
        next_dt = dt + timedelta(days=1)
        while next_dt.weekday() >= 5:
            next_dt += timedelta(days=1)
    elif rule == "weekly":
        next_dt = dt + timedelta(days=7)
    elif rule == "monthly":
        year = dt.year + (1 if dt.month == 12 else 0)
        month = 1 if dt.month == 12 else dt.month + 1
        max_day = monthrange(year, month)[1]
        day = min(dt.day, max_day)
        next_dt = dt.replace(year=year, month=month, day=day)
    else:
        next_dt = dt
    return next_dt.replace(microsecond=0).isoformat()


@dataclass(frozen=True)
class TaskPayload:
    """Two-state task protocol (`todo`/`done`) + Eisenhower 4-Quadrant priority & DuoDo metadata."""

    task_status: str = "todo"  # "todo" | "done"
    close_reason: str = ""     # "" | "completed" | "cancelled" | "dropped"
    importance: int = 3        # 1..5
    urgency: int = 3           # 1..5 (base urgency)
    effective_urgency: int = 3
    quadrant: str = "Q4_eliminate"
    priority_score: float = 0.0
    priority_reason: str = ""
    start_at: str = ""
    due_at: str = ""
    assignee: str = "self"
    blockers: tuple[str, ...] = field(default_factory=tuple)
    progress_notes: tuple[str, ...] = field(default_factory=tuple)
    # DuoDo-inspired visual, schedule, checklist, rollover, focus-lock & supervision fields
    color_theme: str = "sky"
    task_mode: str = "personal"
    supervision_enabled: bool = False
    is_all_day: bool = True
    repeat_rule: str = "never"
    reminder_offset_minutes: int | None = None
    auto_rollover: bool = False
    rollover_count: int = 0
    last_rollover_date: str = ""
    subtasks: tuple[dict[str, Any], ...] = field(default_factory=tuple)
    focus_lock_enabled: bool = False
    focus_minutes: int = 25
    duration_minutes: int | None = None
    focus_sessions_completed: int = 0
    focus_interruptions: tuple[dict[str, Any], ...] = field(default_factory=tuple)
    nudges: tuple[dict[str, Any], ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        if self.task_status not in TASK_STATUSES:
            raise ValidationError(
                f"task_status must be one of {TASK_STATUSES}, got {self.task_status!r}"
            )
        if self.close_reason not in TASK_CLOSE_REASONS:
            raise ValidationError(f"Invalid close_reason: {self.close_reason!r}")
        if self.task_status == "done" and not self.close_reason:
            object.__setattr__(self, "close_reason", "completed")
        if self.task_status == "todo" and self.close_reason:
            object.__setattr__(self, "close_reason", "")
        imp = max(1, min(5, _safe_int_field(self.importance, "importance", 3) or 3))
        urg = max(1, min(5, _safe_int_field(self.urgency, "urgency", 3) or 3))
        object.__setattr__(self, "importance", imp)
        object.__setattr__(self, "urgency", urg)
        eff_urg, auto_quad, auto_score = compute_effective_urgency_and_quadrant(
            imp, urg, self.due_at, has_blockers=bool(self.blockers)
        )
        object.__setattr__(self, "effective_urgency", eff_urg)
        pscore = _safe_float_field(self.priority_score, "priority_score", 0.0)
        if self.quadrant not in QUADRANTS or pscore <= 0.0:
            if self.quadrant in QUADRANTS and self.quadrant != auto_quad:
                coherent = round(
                    _QUADRANT_BASE[self.quadrant] + imp * 5.0 + eff_urg * 4.0, 2
                )
                object.__setattr__(self, "quadrant", self.quadrant)
                object.__setattr__(self, "priority_score", coherent)
            else:
                object.__setattr__(self, "quadrant", auto_quad)
                object.__setattr__(self, "priority_score", auto_score)
        else:
            object.__setattr__(self, "priority_score", pscore)

        color = self.color_theme if self.color_theme in COLOR_THEMES else "sky"
        mode = self.task_mode if self.task_mode in TASK_MODES else "personal"
        repeat = self.repeat_rule if self.repeat_rule in REPEAT_RULES else "never"
        rem_min = _safe_int_field(
            self.reminder_offset_minutes, "reminder_offset_minutes", None
        )
        if rem_min is not None and rem_min < 0:
            rem_min = 0
        focus_min = max(1, min(240, _safe_int_field(self.focus_minutes, "focus_minutes", 25) or 25))
        raw_dur = _safe_int_field(self.duration_minutes, "duration_minutes", None)
        dur_min = max(15, min(1440, raw_dur)) if raw_dur is not None else None
        raw_start = _opt_str(self.start_at).strip()
        if raw_start:
            parsed_start = parse_iso_datetime(raw_start)
            norm_start = (
                to_beijing(parsed_start).replace(microsecond=0).isoformat()
                if parsed_start is not None
                else raw_start
            )
        else:
            norm_start = ""
        roll_cnt = max(0, _safe_int_field(self.rollover_count, "rollover_count", 0) or 0)
        f_comp = max(
            0,
            _safe_int_field(self.focus_sessions_completed, "focus_sessions_completed", 0) or 0,
        )
        object.__setattr__(self, "start_at", norm_start)
        object.__setattr__(self, "color_theme", color)
        object.__setattr__(self, "task_mode", mode)
        object.__setattr__(self, "supervision_enabled", bool(self.supervision_enabled or mode == "multiplayer"))
        object.__setattr__(self, "is_all_day", bool(self.is_all_day))
        object.__setattr__(self, "repeat_rule", repeat)
        object.__setattr__(self, "reminder_offset_minutes", rem_min)
        object.__setattr__(self, "auto_rollover", bool(self.auto_rollover))
        object.__setattr__(self, "rollover_count", roll_cnt)
        object.__setattr__(self, "last_rollover_date", _opt_str(self.last_rollover_date))
        object.__setattr__(self, "subtasks", _normalize_subtasks(self.subtasks))
        object.__setattr__(self, "focus_lock_enabled", bool(self.focus_lock_enabled))
        object.__setattr__(self, "focus_minutes", focus_min)
        object.__setattr__(self, "duration_minutes", dur_min)
        object.__setattr__(self, "focus_sessions_completed", f_comp)
        object.__setattr__(self, "focus_interruptions", _normalize_dict_tuple(self.focus_interruptions))
        object.__setattr__(self, "nudges", _normalize_dict_tuple(self.nudges))

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_status": self.task_status,
            "close_reason": self.close_reason,
            "importance": self.importance,
            "urgency": self.urgency,
            "effective_urgency": self.effective_urgency,
            "quadrant": self.quadrant,
            "quadrant_label": QUADRANT_LABELS.get(self.quadrant, self.quadrant),
            "priority_score": self.priority_score,
            "priority_reason": self.priority_reason,
            "start_at": self.start_at,
            "due_at": self.due_at,
            "assignee": self.assignee,
            "blockers": list(self.blockers),
            "progress_notes": list(self.progress_notes),
            "color_theme": self.color_theme,
            "task_mode": self.task_mode,
            "supervision_enabled": self.supervision_enabled,
            "is_all_day": self.is_all_day,
            "repeat_rule": self.repeat_rule,
            "reminder_offset_minutes": self.reminder_offset_minutes,
            "auto_rollover": self.auto_rollover,
            "rollover_count": self.rollover_count,
            "last_rollover_date": self.last_rollover_date,
            "subtasks": [dict(s) for s in self.subtasks],
            "focus_lock_enabled": self.focus_lock_enabled,
            "focus_minutes": self.focus_minutes,
            "duration_minutes": self.duration_minutes,
            "focus_sessions_completed": self.focus_sessions_completed,
            "focus_interruptions": [dict(x) for x in self.focus_interruptions],
            "nudges": [dict(n) for n in self.nudges],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TaskPayload:
        imp = _safe_int_field(data.get("importance"), "importance", 3) or 3
        urg = _safe_int_field(data.get("urgency"), "urgency", 3) or 3
        raw_start = _opt_str(data.get("start_at")).strip()
        if raw_start:
            parsed_start = parse_iso_datetime(raw_start)
            start_at = (
                to_beijing(parsed_start).replace(microsecond=0).isoformat()
                if parsed_start is not None
                else raw_start
            )
        else:
            start_at = ""
        due_at = _opt_str(data.get("due_at"))
        blockers = _safe_str_tuple(data.get("blockers"), "blockers")
        progress_notes = _safe_str_tuple(data.get("progress_notes"), "progress_notes")
        eff_urg, auto_quad, auto_score = compute_effective_urgency_and_quadrant(
            imp, urg, due_at, has_blockers=bool(blockers)
        )
        raw_quad = _opt_str(data.get("quadrant"))
        quadrant = raw_quad if raw_quad in QUADRANTS else auto_quad
        raw_score = _safe_float_field(data.get("priority_score"), "priority_score", 0.0)
        if raw_score > 0.0:
            score = raw_score
        elif quadrant != auto_quad:
            score = round(
                _QUADRANT_BASE[quadrant] + max(1, min(5, imp)) * 5.0 + eff_urg * 4.0, 2
            )
        else:
            score = auto_score
        rem_val = _safe_int_field(data.get("reminder_offset_minutes"), "reminder_offset_minutes", None)
        dur_val = _safe_int_field(data.get("duration_minutes"), "duration_minutes", None)
        return cls(
            task_status=_opt_str(data.get("task_status"), "todo") or "todo",
            close_reason=_opt_str(data.get("close_reason")),
            importance=imp,
            urgency=urg,
            effective_urgency=eff_urg,
            quadrant=quadrant,
            priority_score=score,
            priority_reason=_opt_str(data.get("priority_reason")),
            start_at=start_at,
            due_at=due_at,
            assignee=_opt_str(data.get("assignee"), "self") or "self",
            blockers=blockers,
            progress_notes=progress_notes,
            color_theme=_opt_str(data.get("color_theme"), "sky") or "sky",
            task_mode=_opt_str(data.get("task_mode"), "personal") or "personal",
            supervision_enabled=bool(data.get("supervision_enabled", False)),
            is_all_day=bool(data.get("is_all_day", True)),
            repeat_rule=_opt_str(data.get("repeat_rule"), "never") or "never",
            reminder_offset_minutes=rem_val,
            auto_rollover=bool(data.get("auto_rollover", False)),
            rollover_count=_safe_int_field(data.get("rollover_count"), "rollover_count", 0) or 0,
            last_rollover_date=_opt_str(data.get("last_rollover_date")),
            subtasks=_normalize_subtasks(data.get("subtasks")),
            focus_lock_enabled=bool(data.get("focus_lock_enabled", False)),
            focus_minutes=_safe_int_field(data.get("focus_minutes"), "focus_minutes", 25) or 25,
            duration_minutes=dur_val,
            focus_sessions_completed=(
                _safe_int_field(data.get("focus_sessions_completed"), "focus_sessions_completed", 0)
                or 0
            ),
            focus_interruptions=_normalize_dict_tuple(data.get("focus_interruptions")),
            nudges=_normalize_dict_tuple(data.get("nudges")),
        )


@dataclass(frozen=True)
class PreferencePayload:
    """Preference card preserving conditions and exceptions simultaneously (C03)."""

    target: str
    polarity: str = "positive"  # "positive" | "negative" | "conditional"
    conditions: tuple[str, ...] = field(default_factory=tuple)
    exceptions: tuple[str, ...] = field(default_factory=tuple)
    details: str = ""

    def __post_init__(self) -> None:
        if not self.target.strip():
            raise ValidationError("Preference target must not be empty")
        if self.polarity not in ("positive", "negative", "conditional"):
            raise ValidationError(f"Invalid preference polarity: {self.polarity}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "target": self.target,
            "polarity": self.polarity,
            "conditions": list(self.conditions),
            "exceptions": list(self.exceptions),
            "details": self.details,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PreferencePayload:
        return cls(
            target=_opt_str(data.get("target")).strip(),
            polarity=_opt_str(data.get("polarity"), "positive") or "positive",
            conditions=_safe_str_tuple(data.get("conditions"), "conditions"),
            exceptions=_safe_str_tuple(data.get("exceptions"), "exceptions"),
            details=_opt_str(data.get("details")),
        )


@dataclass(frozen=True)
class SemanticPayload:
    """Semantic fact with temporal causal ordering (`event_time`) so old backfills don't clobber current truth (C01)."""

    subject: str
    predicate: str
    value: str
    event_time: str = ""
    previous_values: tuple[str, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        if not self.subject.strip() or not self.predicate.strip():
            raise ValidationError("SemanticPayload requires non-empty subject and predicate")

    def to_dict(self) -> dict[str, Any]:
        return {
            "subject": self.subject,
            "predicate": self.predicate,
            "value": self.value,
            "event_time": self.event_time,
            "previous_values": list(self.previous_values),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SemanticPayload:
        return cls(
            subject=_opt_str(data.get("subject"), "user").strip() or "user",
            predicate=_opt_str(data.get("predicate"), "fact").strip() or "fact",
            value=_opt_str(data.get("value")),
            event_time=_opt_str(data.get("event_time")),
            previous_values=_safe_str_tuple(data.get("previous_values"), "previous_values"),
        )


@dataclass(frozen=True)
class EpisodicPayload:
    """Episodic experience capturing one-off historical events."""

    event_summary: str
    occurred_at: str = ""
    participants: tuple[str, ...] = field(default_factory=tuple)
    location: str = ""
    outcome: str = ""

    def __post_init__(self) -> None:
        if not self.event_summary.strip():
            raise ValidationError("EpisodicPayload requires event_summary")

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_summary": self.event_summary,
            "occurred_at": self.occurred_at,
            "participants": list(self.participants),
            "location": self.location,
            "outcome": self.outcome,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> EpisodicPayload:
        return cls(
            event_summary=_opt_str(data.get("event_summary")).strip(),
            occurred_at=_opt_str(data.get("occurred_at")),
            participants=_safe_str_tuple(data.get("participants"), "participants"),
            location=_opt_str(data.get("location")),
            outcome=_opt_str(data.get("outcome")),
        )


def validate_and_normalize_payload(memory_type: str, raw_payload: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(raw_payload, dict):
        raise ValidationError("payload must be an object")
    if memory_type == "task":
        return TaskPayload.from_dict(raw_payload).to_dict()
    if memory_type == "preference":
        return PreferencePayload.from_dict(raw_payload).to_dict()
    if memory_type == "semantic":
        return SemanticPayload.from_dict(raw_payload).to_dict()
    if memory_type == "episodic":
        return EpisodicPayload.from_dict(raw_payload).to_dict()
    raise ValidationError(f"Unsupported memory_type: {memory_type!r}")


@dataclass(frozen=True)
class MemoryEvidence:
    """Immutable provenance link binding a MemoryVersion to a specific RawNote revision."""

    note_id: str
    note_revision: int
    quote: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "note_id": self.note_id,
            "note_revision": self.note_revision,
            "quote": self.quote,
        }


@dataclass(frozen=True)
class MemoryVersion:
    """Append-only audit snapshot created on every memory mutation."""

    memory_id: str
    version: int
    title: str
    summary: str
    payload: dict[str, Any]
    assertion_kind: str
    status: str
    change_reason: str
    actor_id: str
    origin: str
    evidences: tuple[MemoryEvidence, ...] = field(default_factory=tuple)
    created_at: str = ""

    def __post_init__(self) -> None:
        if not self.created_at:
            object.__setattr__(self, "created_at", utc_now_iso())

    def to_dict(self) -> dict[str, Any]:
        return {
            "memory_id": self.memory_id,
            "version": self.version,
            "title": self.title,
            "summary": self.summary,
            "payload": self.payload,
            "assertion_kind": self.assertion_kind,
            "status": self.status,
            "change_reason": self.change_reason,
            "actor_id": self.actor_id,
            "origin": self.origin,
            "evidences": [e.to_dict() for e in self.evidences],
            "created_at": self.created_at,
        }


@dataclass(frozen=True)
class MemoryItem:
    """Canonical structured memory card with versioned state and provenance."""

    memory_id: str
    space_id: str
    permalink: str
    memory_type: str
    title: str
    summary: str
    payload: dict[str, Any]
    canonical_key: str = ""
    tags: tuple[str, ...] = field(default_factory=tuple)
    assertion_kind: str = "stated"
    status: str = "active"
    version: int = 1
    user_locked: bool = False
    rejected_inferences: tuple[str, ...] = field(default_factory=tuple)
    evidences: tuple[MemoryEvidence, ...] = field(default_factory=tuple)
    event_time: str = ""
    created_at: str = ""
    updated_at: str = ""

    def __post_init__(self) -> None:
        if self.memory_type not in MEMORY_TYPES:
            raise ValidationError(f"Invalid memory_type: {self.memory_type}")
        if self.assertion_kind not in ASSERTION_KINDS:
            raise ValidationError(f"Invalid assertion_kind: {self.assertion_kind}")
        if self.status not in MEMORY_STATUSES:
            raise ValidationError(f"Invalid status: {self.status}")
        if self.permalink:
            validate_safe_permalink(self.permalink)
        if self.status == "forgotten":
            object.__setattr__(self, "payload", {})
        else:
            normalized = validate_and_normalize_payload(self.memory_type, self.payload)
            object.__setattr__(self, "payload", normalized)
        now = utc_now_iso()
        if not self.created_at:
            object.__setattr__(self, "created_at", now)
        if not self.updated_at:
            object.__setattr__(self, "updated_at", self.created_at)
        if not self.event_time:
            object.__setattr__(self, "event_time", self.created_at)

    @property
    def uri(self) -> str:
        return f"memory://{self.permalink}"

    def searchable_text(self) -> str:
        parts = [self.title, self.summary, " ".join(self.tags)]
        if self.memory_type == "task":
            parts.extend(self.payload.get("blockers", []))
            parts.extend(self.payload.get("progress_notes", []))
            parts.extend(
                str(s.get("title", ""))
                for s in self.payload.get("subtasks", [])
                if isinstance(s, dict) and s.get("title")
            )
            if self.payload.get("priority_reason"):
                parts.append(str(self.payload["priority_reason"]))
            assignee = str(self.payload.get("assignee", "") or "").strip()
            if assignee and assignee != "self":
                parts.extend([assignee, f"负责人 {assignee}"])
            t_status = str(self.payload.get("task_status", "todo") or "todo")
            parts.append("done 已完成" if t_status == "done" else "todo 待办 未完成")
            quad = str(self.payload.get("quadrant", "") or "")
            if quad in QUADRANT_LABELS:
                parts.append(QUADRANT_LABELS[quad])
            due_raw = str(self.payload.get("due_at", "") or "").strip()
            if due_raw:
                parts.append(due_raw[:10])
        elif self.memory_type == "preference":
            parts.append(str(self.payload.get("target", "")))
            parts.extend(self.payload.get("conditions", []))
            parts.extend(self.payload.get("exceptions", []))
            parts.append(str(self.payload.get("details", "")))
        elif self.memory_type == "semantic":
            parts.extend(
                [
                    str(self.payload.get("subject", "")),
                    str(self.payload.get("predicate", "")),
                    str(self.payload.get("value", "")),
                ]
            )
            parts.extend(str(pv) for pv in self.payload.get("previous_values", []) if pv)
        elif self.memory_type == "episodic":
            parts.append(str(self.payload.get("event_summary", "")))
            parts.append(str(self.payload.get("location", "")))
            parts.append(str(self.payload.get("outcome", "")))
            occ = str(self.payload.get("occurred_at", "") or "").strip()
            if occ:
                parts.append(occ[:10])
            parts.extend(str(pt) for pt in self.payload.get("participants", []) if pt)
        return " | ".join(p for p in parts if p)

    def to_dict(self) -> dict[str, Any]:
        return {
            "memory_id": self.memory_id,
            "space_id": self.space_id,
            "permalink": self.permalink,
            "uri": self.uri,
            "memory_type": self.memory_type,
            "title": self.title,
            "summary": self.summary,
            "payload": self.payload,
            "canonical_key": self.canonical_key,
            "tags": list(self.tags),
            "assertion_kind": self.assertion_kind,
            "status": self.status,
            "version": self.version,
            "user_locked": self.user_locked,
            "rejected_inferences": list(self.rejected_inferences),
            "evidences": [e.to_dict() for e in self.evidences],
            "event_time": self.event_time,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }
