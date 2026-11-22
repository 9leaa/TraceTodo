"""ReMe-inspired Sleep-Time Consolidation (`DreamService`) with strict Budget Circuit Breaker."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
import hashlib
import time
from typing import Any

from trace_memory.domain.memories import MemoryEvidence, MemoryItem
from trace_memory.domain.proposals import ChangeProposal, ChangeResult
from trace_memory.llm.protocol import LLMProvider
from trace_memory.services.change_service import ChangeService
from trace_memory.storage.repositories import MemoryRepository, TraceRepository


@dataclass
class BudgetGuard:
    """Enforces strict upper bounds on LLM calls and token consumption during Dream runs."""

    max_llm_calls: int
    max_tokens: int
    used_llm_calls: int = 0
    used_tokens: int = 0
    halted_reason: str = ""

    def can_proceed(self) -> bool:
        if self.used_llm_calls >= self.max_llm_calls:
            self.halted_reason = f"Reached max_llm_calls limit ({self.max_llm_calls})"
            return False
        if self.used_tokens >= self.max_tokens:
            self.halted_reason = f"Reached max_tokens limit ({self.used_tokens}/{self.max_tokens})"
            return False
        return True

    def record(self, calls: int, tokens: int) -> None:
        self.used_llm_calls += int(calls)
        self.used_tokens += int(tokens)
        if self.used_tokens >= self.max_tokens and not self.halted_reason:
            self.halted_reason = f"Reached max_tokens limit ({self.used_tokens}/{self.max_tokens})"


class DreamService:
    """Synthesizes cross-record `inferred` insights from `stated` facts/episodes within budget."""

    def __init__(
        self,
        memory_repo: MemoryRepository,
        trace_repo: TraceRepository,
        change_service: ChangeService,
        llm: LLMProvider,
        default_max_calls: int = 5,
        default_max_tokens: int = 8000,
        quota_service: Any = None,
    ) -> None:
        self.memory_repo = memory_repo
        self.trace_repo = trace_repo
        self.change_service = change_service
        self.llm = llm
        self.default_max_calls = default_max_calls
        self.default_max_tokens = default_max_tokens
        self.quota_service = quota_service

    @staticmethod
    def _cluster_topic(mem: MemoryItem) -> str:
        for t in mem.tags:
            if t not in ("fact", "episode", "preference", "dream_insight", "task"):
                return t
        if mem.memory_type == "semantic":
            pred = str(mem.payload.get("predicate", ""))
            if pred and pred != "statement":
                return pred
        if mem.memory_type == "preference":
            return "偏好习惯"
        if mem.memory_type == "episodic":
            return "近期经历"
        return "日常知识"

    @staticmethod
    def _evidence_signature(memories: list[MemoryItem]) -> str:
        parts = sorted(f"{m.memory_id}:v{m.version}" for m in memories)
        return hashlib.sha1("|".join(parts).encode("utf-8")).hexdigest()[:16]

    def run_dream(
        self,
        space_id: str,
        max_llm_calls: int | None = None,
        max_tokens: int | None = None,
        require_review: bool = False,
    ) -> dict[str, Any]:
        """Group eligible stated memories by topic and synthesize `inferred` pattern cards."""
        t0 = time.perf_counter()
        guard = BudgetGuard(
            max_llm_calls=self.default_max_calls if max_llm_calls is None else int(max_llm_calls),
            max_tokens=self.default_max_tokens if max_tokens is None else int(max_tokens),
        )

        # Only consolidate active `stated` facts, episodes, and preferences (never re-dream inferred dreams!)
        active_items = [
            m
            for m in self.memory_repo.list_memories(space_id=space_id, status="active", limit=200)
            if m.assertion_kind == "stated" and m.memory_type in ("semantic", "episodic", "preference")
        ]

        raw_clusters: dict[str, list[MemoryItem]] = defaultdict(list)
        for m in active_items:
            raw_clusters[self._cluster_topic(m)].append(m)

        # Merge singleton tag topics into broader domain buckets so related preferences/episodes consolidate
        clusters: dict[str, list[MemoryItem]] = defaultdict(list)
        type_bucket_names = {
            "preference": "偏好习惯",
            "episodic": "近期经历",
            "semantic": "日常知识",
        }
        for topic, items in raw_clusters.items():
            if len(items) >= 2:
                clusters[topic].extend(items)
            else:
                m = items[0]
                clusters[type_bucket_names.get(m.memory_type, "日常知识")].append(m)

        created_or_updated: list[ChangeResult] = []
        skipped_unchanged = 0
        skipped_no_evidence = 0

        try:
            for topic, group in sorted(clusters.items()):
                if len(group) < 2:
                    continue
                if not guard.can_proceed():
                    break

                # Collect ground-truth evidences across source memories (no-evidence -> never commit!)
                combined_evidences: dict[tuple[str, int], MemoryEvidence] = {}
                for m in group[:6]:
                    for ev in m.evidences:
                        combined_evidences[(ev.note_id, ev.note_revision)] = ev
                if not combined_evidences:
                    skipped_no_evidence += 1
                    continue

                sig = self._evidence_signature(group[:6])
                idemp_key = f"dream:{topic}:{sig}"
                if self.trace_repo.get_idempotent_result(space_id, idemp_key) is not None:
                    skipped_unchanged += 1
                    continue

                try:
                    batch = self.llm.synthesize_dream_cluster(topic, group[:6])
                except Exception:
                    guard.record(1, 0)
                    if not guard.can_proceed():
                        break
                    continue
                guard.record(batch.llm_calls, batch.tokens_used)

                for idx, insight in enumerate(batch.insights):
                    item_idemp = f"{idemp_key}:{idx}"
                    existing = self.memory_repo.find_by_canonical_key(
                        space_id,
                        insight.canonical_key,
                        statuses=("active", "pending_review"),
                    )
                    if existing is None:
                        prop = ChangeProposal(
                            space_id=space_id,
                            action="create",
                            memory_type="semantic",
                            idempotency_key=item_idemp,
                            actor_id="dream_engine",
                            origin="dream_consolidator",
                            title=insight.title,
                            summary=insight.summary,
                            canonical_key=insight.canonical_key,
                            tags=insight.tags,
                            payload=insight.payload,
                            assertion_kind="inferred",  # Strictly tagged inferred!
                            require_review=require_review,
                            evidences=tuple(combined_evidences.values()),
                            relations=insight.relations,
                            reason=f"Dream consolidated {len(group[:6])} stated memories under '{topic}'",
                        )
                    else:
                        prop = ChangeProposal(
                            space_id=space_id,
                            action="update",
                            memory_type="semantic",
                            target_memory_id=existing.memory_id,
                            expected_version=existing.version,
                            idempotency_key=item_idemp,
                            actor_id="dream_engine",
                            origin="dream_consolidator",
                            title=insight.title,
                            summary=insight.summary,
                            canonical_key=insight.canonical_key,
                            tags=insight.tags,
                            payload=insight.payload,
                            assertion_kind="inferred",
                            require_review=require_review,
                            evidences=tuple(combined_evidences.values()),
                            relations=insight.relations,
                            reason=f"Dream updated pattern under '{topic}'",
                        )
                    try:
                        res = self.change_service.apply(prop)
                        created_or_updated.append(res)
                    except Exception:
                        continue

                self.trace_repo.save_idempotent_result(
                    space_id, idemp_key, topic, {"topic": topic, "sig": sig}
                )
        finally:
            duration_ms = (time.perf_counter() - t0) * 1000.0
            if self.quota_service is not None and (guard.used_llm_calls or guard.used_tokens):
                self.quota_service.record_usage(
                    f"space:{space_id}",
                    llm_calls=guard.used_llm_calls,
                    tokens_used=guard.used_tokens,
                )
            summary = {
                "space_id": space_id,
                "clusters_evaluated": sum(1 for g in clusters.values() if len(g) >= 2),
                "insights_processed": len(created_or_updated),
                "results": [r.to_dict() for r in created_or_updated],
                "skipped_unchanged": skipped_unchanged,
                "skipped_no_evidence": skipped_no_evidence,
                "budget": {
                    "max_llm_calls": guard.max_llm_calls,
                    "used_llm_calls": guard.used_llm_calls,
                    "max_tokens": guard.max_tokens,
                    "used_tokens": guard.used_tokens,
                    "budget_halted": bool(guard.halted_reason),
                    "halted_reason": guard.halted_reason,
                },
            }
            trace_id = self.trace_repo.record_trace(
                space_id=space_id,
                operation="dream_consolidate",
                input_summary=f"Dream over {len(active_items)} stated memories",
                details=summary,
                llm_calls=guard.used_llm_calls,
                tokens_used=guard.used_tokens,
                duration_ms=duration_ms,
            )
            summary["trace_id"] = trace_id
        return summary
