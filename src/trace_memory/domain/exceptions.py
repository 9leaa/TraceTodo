"""Domain exception hierarchy for TraceTodo / TraceMemory.

Separated into its own module so `memories.py`, `notes.py`, and `proposals.py`
can raise `ValidationError` uniformly without circular imports.
"""

from __future__ import annotations


class TraceMemoryError(Exception):
    """Base domain error."""


class StructuredOutputError(TraceMemoryError):
    """The provider returned a response that does not satisfy the JSON contract."""

    def __init__(self, reason: str, message: str, tokens_used: int) -> None:
        super().__init__(message)
        self.reason = reason
        self.tokens_used = tokens_used


class ValidationError(TraceMemoryError, ValueError):
    """Raised when schema, field, or domain protocol validation fails.

    Subclasses both `TraceMemoryError` and built-in `ValueError` so HTTP/MCP
    dispatchers map it to structured 4xx errors while legacy callers catching
    `ValueError` remain compatible.
    """


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
