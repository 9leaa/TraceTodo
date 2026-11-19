"""RawNote domain model: immutable-by-default original user inputs with revision tracking."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
import uuid


VALID_SOURCE_TYPES = ("cli", "mcp", "api", "web", "markdown_edit", "system")


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


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
        }
