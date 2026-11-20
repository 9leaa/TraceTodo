"""Unified Change Proposal contract, CAS preconditions, and domain exceptions."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from trace_memory.domain.memories import MemoryEvidence, MemoryItem


class TraceMemoryError(Exception):
    """Base domain error."""


class ValidationError(TraceMemoryError):
    """Raised when schema, field, or domain protocol validation fails."""


class VersionConflictError(TraceMemoryError):
    """Raised when `expected_version` does not match current version (CAS failure)."""

    def __init__(self, memory_id: str, expected: int, actual: int) -> None:
        super().__init__(
            f"Version conflict on {memory_id}: expected v{expected}, current is v{actual}"
        )
        self.memory_id = memory_id
        self.expected = expected
        self.actual = actual


class PermissionDeniedError(TraceMemoryError):
    """Raised on cross-space access or unauthorized source expansion (C09)."""


class UserLockedError(TraceMemoryError):
    """Raised when automatic consolidation tries to overwrite a user-locked card (C06)."""


class NotFoundError(TraceMemoryError):
    """Raised when target entity/note does not exist or is invisible in the active space."""


class BudgetExceededError(TraceMemoryError):
    """Raised when Dream consolidation exceeds LLM call or token budget."""


class UnauthorizedError(TraceMemoryError):
    """Raised when authentication is required or credentials/session token are invalid."""


class RateLimitError(TraceMemoryError):
    """Raised when per-user/IP rate limits or burst guards are exceeded."""

    def __init__(self, message: str, retry_after_seconds: int = 60) -> None:
        super().__init__(message)
        self.retry_after_seconds = max(1, int(retry_after_seconds))


class QuotaExceededError(TraceMemoryError):
    """Raised when daily per-user note, LLM call, or token quotas are exhausted."""


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

    def __post_init__(self) -> None:
        if not self.space_id.strip():
            raise ValidationError("space_id is required")
        if self.action not in CHANGE_ACTIONS:
            raise ValidationError(f"Unsupported action: {self.action!r}")
        if self.origin not in CHANGE_ORIGINS:
            raise ValidationError(f"Unsupported origin: {self.origin!r}")
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
