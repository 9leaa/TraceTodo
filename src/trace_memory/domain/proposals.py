"""Unified Change Proposal contract, CAS preconditions, and domain exceptions."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from trace_memory.domain.exceptions import (
    BudgetExceededError,
    NotFoundError,
    PermissionDeniedError,
    QuotaExceededError,
    RateLimitError,
    TraceMemoryError,
    UnauthorizedError,
    UserLockedError,
    ValidationError,
    VersionConflictError,
)
from trace_memory.domain.memories import (
    ASSERTION_KINDS,
    MEMORY_TYPES,
    MemoryEvidence,
    MemoryItem,
    validate_safe_permalink,
)


CHANGE_ACTIONS = (
    "create",
    "update",
    "complete_task",
    "cancel_task",
    "reopen_task",
    "update_quadrant",
    "approve",
    "reject",
    "forget",
)

CHANGE_ORIGINS = (
    "user_direct",
    "ingest_extractor",
    "mcp_agent",
    "markdown_sync",
    "dream_consolidator",
    "system_rollover",
    "system_daily_check",
    "friend_supervision",
)


@dataclass(frozen=True)
class RelationSpec:
    """Outgoing wikilink relation declared on a memory proposal."""

    relation_type: str
    target: str  # target permalink or title
    context: str = ""


@dataclass(frozen=True)
class ChangeProposal:
    """Unified write envelope for all internal and external memory mutations.

    Inspired by Basic Memory's explicit preconditions (`expected_version` CAS)
    and Suixinji's traceable domain rules (`evidences`, `assertion_kind`, `user_locked`).
    """

    space_id: str
    action: str
    memory_type: str = "semantic"
    target_memory_id: str = ""
    expected_version: int | None = None
    idempotency_key: str = ""
    actor_id: str = "user"
    origin: str = "user_direct"
    title: str = ""
    summary: str = ""
    permalink: str = ""
    canonical_key: str = ""
    tags: tuple[str, ...] = field(default_factory=tuple)
    payload: dict[str, Any] = field(default_factory=dict)
    assertion_kind: str = "stated"
    require_review: bool = False
    lock_for_user: bool = False
    evidences: tuple[MemoryEvidence, ...] = field(default_factory=tuple)
    relations: tuple[RelationSpec, ...] = field(default_factory=tuple)
    event_time: str = ""
    reason: str = ""
    replace_relations: bool = False
    # forget only: RawNotes in this set are left untouched by the note cascade (they get re-extracted)
    keep_note_ids: tuple[str, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        if not self.space_id.strip():
            raise ValidationError("space_id is required")
        if self.action not in CHANGE_ACTIONS:
            raise ValidationError(f"Unsupported action: {self.action!r}")
        if self.origin not in CHANGE_ORIGINS:
            raise ValidationError(f"Unsupported origin: {self.origin!r}")
        if self.memory_type not in MEMORY_TYPES:
            raise ValidationError(f"Unsupported memory_type: {self.memory_type!r}")
        if self.assertion_kind not in ASSERTION_KINDS:
            raise ValidationError(f"Unsupported assertion_kind: {self.assertion_kind!r}")
        if self.permalink:
            validate_safe_permalink(self.permalink)
        if self.action != "create" and not self.target_memory_id:
            raise ValidationError(f"Action {self.action!r} requires target_memory_id")


@dataclass(frozen=True)
class ChangeResult:
    """Outcome of applying a ChangeProposal."""

    proposal_id: str
    status: str  # "applied" | "pending_review" | "idempotent_replay" | "skipped_older_event" | "skipped_user_locked"
    memory: MemoryItem | None
    message: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "proposal_id": self.proposal_id,
            "status": self.status,
            "memory": self.memory.to_dict() if self.memory else None,
            "message": self.message,
        }
