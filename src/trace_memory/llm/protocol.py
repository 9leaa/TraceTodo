"""LLM and Embedding provider protocols and structured extraction contracts."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from trace_memory.domain.memories import MemoryItem
from trace_memory.domain.notes import RawNote
from trace_memory.domain.proposals import RelationSpec


@dataclass(frozen=True)
class ExtractedCandidate:
    """Structured candidate extracted from a `RawNote` before validation and adjudication."""

    memory_type: str  # "task" | "preference" | "semantic" | "episodic"
    title: str
    summary: str
    canonical_key: str
    payload: dict[str, Any]
    mutation_hint: str = "upsert"  # "upsert" | "complete_task" | "cancel_task" | "reopen_task"
    assertion_kind: str = "stated"
    tags: tuple[str, ...] = field(default_factory=tuple)
    relations: tuple[RelationSpec, ...] = field(default_factory=tuple)
    event_time: str = ""
    quote: str = ""


@dataclass(frozen=True)
class ExtractionBatch:
    candidates: tuple[ExtractedCandidate, ...]
    llm_calls: int = 0
    tokens_used: int = 0


@dataclass(frozen=True)
class DreamInsight:
    title: str
    summary: str
    canonical_key: str
    payload: dict[str, Any]
    source_memory_ids: tuple[str, ...]
    tags: tuple[str, ...] = field(default_factory=tuple)
    relations: tuple[RelationSpec, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class DreamBatch:
    insights: tuple[DreamInsight, ...]
    llm_calls: int = 1
    tokens_used: int = 0


@dataclass(frozen=True)
class AskSynthesis:
    answer: str
    cited_memory_ids: tuple[str, ...]
    cited_note_ids: tuple[str, ...]
    has_sufficient_evidence: bool
    llm_calls: int = 1
    tokens_used: int = 0


@dataclass(frozen=True)
class AskTurn:
    message: dict[str, Any]
    tokens_used: int


@runtime_checkable
class AskToolModel(Protocol):
    def build_ask_messages(
        self,
        question: str,
        query_mode: str,
        memories: list[MemoryItem],
        notes: list[RawNote],
        history_versions: list[dict[str, Any]],
        graph_relations: list[dict[str, Any]] | None = None,
    ) -> list[dict[str, Any]]: ...

    def ask_turn(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        *,
        allow_tools: bool,
    ) -> AskTurn: ...


@dataclass(frozen=True)
class SubtaskSuggestion:
    title: str
    estimated_minutes: int = 15


@dataclass(frozen=True)
class DecomposeBatch:
    subtasks: tuple[SubtaskSuggestion, ...]
    memory_context_used: tuple[str, ...] = field(default_factory=tuple)
    llm_calls: int = 1
    tokens_used: int = 0


class EmbeddingProvider(Protocol):
    dimension: int

    def embed(self, text: str) -> list[float]: ...


class LLMProvider(Protocol):
    provider_name: str

    def extract_candidates(
        self,
        note_text: str,
        default_event_time: str,
        existing_memories: list[MemoryItem] | None = None,
    ) -> ExtractionBatch: ...

    def synthesize_dream_cluster(
        self, topic: str, memories: list[MemoryItem]
    ) -> DreamBatch: ...

    def synthesize_answer(
        self,
        question: str,
        query_mode: str,  # "current" | "history"
        memories: list[MemoryItem],
        notes: list[RawNote],
        history_versions: list[dict[str, Any]],
        graph_relations: list[dict[str, Any]] | None = None,
    ) -> AskSynthesis: ...

    def decompose_task(
        self,
        task_title: str,
        task_summary: str = "",
        context_memories: list[MemoryItem] | None = None,
    ) -> DecomposeBatch: ...
