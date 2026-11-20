"""Dual-source (Memory + RawNote) Grounded Question-Answering Service."""

from __future__ import annotations

import time
from typing import Any

from trace_memory.domain.memories import MemoryItem
from trace_memory.domain.notes import RawNote
from trace_memory.domain.proposals import ValidationError
from trace_memory.llm.protocol import LLMProvider
from trace_memory.services.guard_service import InputGuardService, RateLimitAndQuotaService
from trace_memory.services.ingest_queue import IngestJobRepository
from trace_memory.services.retrieval_service import RetrievalService
from trace_memory.storage.hybrid_search import HybridSearchIndex
from trace_memory.storage.repositories import MemoryRepository, NoteRepository, TraceRepository


_HISTORY_CUES = (
    "历史",
    "以前",
    "之前",
    "曾经",
    "过去",
    "变迁",
    "变化",
    "改过",
    "最早",
    "演进",
    "history",
    "previously",
    "used to",
)


class AskService:
    """ strictly read-only evidence-grounded QA combining structured Memory + original RawNotes.

    Security invariant (from FUSION_IMPLEMENTATION_PLAN §6.1):
    `AskService.ask()` NEVER mutates task or memory states even if the query looks like an
    imperative command (e.g., "完成周报任务").
    """

    def __init__(
        self,
        note_repo: NoteRepository,
        memory_repo: MemoryRepository,
        trace_repo: TraceRepository,
        search_index: HybridSearchIndex,
        retrieval_service: RetrievalService,
        llm: LLMProvider,
        input_guard: InputGuardService | None = None,
        quota_service: RateLimitAndQuotaService | None = None,
        job_repo: IngestJobRepository | None = None,
        media_service: Any = None,
    ) -> None:
        self.note_repo = note_repo
        self.memory_repo = memory_repo
        self.trace_repo = trace_repo
        self.search_index = search_index
        self.retrieval_service = retrieval_service
        self.llm = llm
        self.input_guard = input_guard
        self.quota_service = quota_service
        self.job_repo = job_repo
        self.media_service = media_service

    @staticmethod
    def detect_query_mode(question: str) -> str:
        q = question.lower()
        return "history" if any(cue in q for cue in _HISTORY_CUES) else "current"

    def ask(self, space_id: str, question: str, top_k: int = 5) -> dict[str, Any]:
        t0 = time.perf_counter()
        q_clean = question.strip()
        if self.input_guard is not None:
            guard_res = self.input_guard.inspect_ask(q_clean)
            if not guard_res.allowed:
                if guard_res.category in ("empty", "too_long"):
                    raise ValidationError(guard_res.reason)
                if self.quota_service is not None:
                    self.quota_service.record_usage(f"space:{space_id}", blocked_noise=1)
                duration_ms = (time.perf_counter() - t0) * 1000.0
                trace_id = self.trace_repo.record_trace(
                    space_id=space_id,
                    operation="ask_filtered_noise",
                    input_summary=q_clean[:60],
                    details={"category": guard_res.category, "reason": guard_res.reason},
                    llm_calls=0,
                    tokens_used=0,
                    duration_ms=duration_ms,
                )
                return {
                    "question": q_clean,
                    "query_mode": "current",
                    "answer": f"🛡️ {guard_res.reason}。你可以这样问：例如“我之前在哪里居住？”或“有哪些未完成的高优待办？”。",
                    "has_sufficient_evidence": False,
                    "filtered": True,
                    "citations": {"memories": [], "notes": []},
                    "trace_id": trace_id,
                }
        query_mode = self.detect_query_mode(q_clean)

        # 1. Hybrid search over active Memories (high threshold so unrelated noise doesn't trigger false answers C12)
        mem_hits = [
            h
            for h in self.search_index.search_memories(
                space_id=space_id, query=q_clean, top_k=top_k, min_vector_score=0.25
            )
            if h.bm25_rank is not None or h.vector_score >= 0.34
        ]
        candidate_memories: list[MemoryItem] = [h.memory for h in mem_hits]
        seen_mem_ids = {m.memory_id for m in candidate_memories}

        # 2. 1-hop graph expansion for top seed memory to pull in linked context
        if candidate_memories:
            graph_ctx = self.retrieval_service.build_graph_context(
                space_id=space_id,
                seed_identifier_or_query=candidate_memories[0].memory_id,
                max_hops=1,
                max_nodes=4,
                max_chars=1500,
            )
            for node in graph_ctx["nodes"]:
                nid = node["memory"]["memory_id"]
                if nid not in seen_mem_ids:
                    extra = self.memory_repo.get(space_id, nid)
                    if extra is not None and extra.status == "active":
                        candidate_memories.append(extra)
                        seen_mem_ids.add(nid)

        # 3. Hybrid search over ground-truth RawNotes (capped at 5 notes)
        note_hits = [
            nh
            for nh in self.search_index.search_notes(
                space_id=space_id, query=q_clean, top_k=min(top_k, 5), min_vector_score=0.25
            )
            if nh.bm25_rank is not None or nh.vector_score >= 0.34
        ]
        candidate_notes = [nh.note for nh in note_hits]
        seen_note_ids = {n.note_id for n in candidate_notes}

        # Also pull direct supporting source notes of top memories if not already included
        for m in candidate_memories[:3]:
            for ev in m.evidences:
                if ev.note_id not in seen_note_ids and len(candidate_notes) < 5:
                    src_note = self.note_repo.get(space_id, ev.note_id)
                    if src_note is not None and not src_note.is_forgotten:
                        candidate_notes.append(src_note)
                        seen_note_ids.add(src_note.note_id)

        # 3b. Read-Your-Own-Writes: inject latest queued/processing RawNotes so immediate Q&A sees just-submitted inputs
        pending_notes: list[RawNote] = []
        if self.job_repo is not None:
            for pid in self.job_repo.list_pending_note_ids(space_id, limit=5):
                pnote = self.note_repo.get(space_id, pid)
                if pnote is None or pnote.is_forgotten:
                    continue
                tagged = RawNote(
                    note_id=pnote.note_id,
                    space_id=pnote.space_id,
                    content=f"[刚录入·整理中] {pnote.visible_content()}",
                    source_type=pnote.source_type,
                    actor_id=pnote.actor_id,
                    revision=pnote.revision,
                    is_forgotten=False,
                    forgotten_spans=(),
                    event_time=pnote.event_time,
                    created_at=pnote.created_at,
                    updated_at=pnote.updated_at,
                )
                pending_notes.append(tagged)
                if pnote.note_id in seen_note_ids:
                    candidate_notes = [
                        tagged if n.note_id == pnote.note_id else n for n in candidate_notes
                    ]
                else:
                    candidate_notes.insert(0, tagged)
                    seen_note_ids.add(pnote.note_id)

        # 4. Load version history only when answering historical evolution questions
        history_versions: list[dict[str, Any]] = []
        if query_mode == "history":
            for m in candidate_memories[:3]:
                for ver in self.memory_repo.get_history(space_id, m.memory_id):
                    history_versions.append(ver.to_dict())

        notes_limit = max(5, min(8, 5 + len(pending_notes)))
        sliced_notes = candidate_notes[:notes_limit]
        llm_notes: list[RawNote] = []
        for n in sliced_notes:
            if self.media_service is not None:
                vtxt = self.media_service.get_visual_text_for_note(space_id, n.note_id)
                if vtxt:
                    llm_notes.append(
                        RawNote(
                            note_id=n.note_id,
                            space_id=n.space_id,
                            content=f"{n.visible_content()}\n[附图视觉内容: {vtxt}]",
                            source_type=n.source_type,
                            actor_id=n.actor_id,
                            revision=n.revision,
                            event_time=n.event_time,
                            created_at=n.created_at,
                            updated_at=n.updated_at,
                        )
                    )
                    continue
            llm_notes.append(n)

        synthesis = self.llm.synthesize_answer(
            question=q_clean,
            query_mode=query_mode,
            memories=candidate_memories[:5],
            notes=llm_notes,
            history_versions=history_versions,
        )

        # Citation Hallucination Guard: keep only IDs actually present in retrieved candidates!
        valid_mem_ids = [mid for mid in synthesis.cited_memory_ids if mid in seen_mem_ids]
        valid_note_ids = [nid for nid in synthesis.cited_note_ids if nid in seen_note_ids]

        cited_media: list[dict[str, Any]] = []
        if self.media_service is not None and synthesis.has_sufficient_evidence:
            evidence_note_ids: list[str] = list(valid_note_ids)
            for m in candidate_memories:
                if m.memory_id in valid_mem_ids:
                    for ev in m.evidences:
                        if ev.note_id not in evidence_note_ids:
                            evidence_note_ids.append(ev.note_id)
            if evidence_note_ids:
                by_note = self.media_service.list_for_notes(space_id, evidence_note_ids)
                for nid in evidence_note_ids:
                    cited_media.extend(by_note.get(nid, []))

        duration_ms = (time.perf_counter() - t0) * 1000.0
        if self.quota_service is not None:
            self.quota_service.record_usage(
                f"space:{space_id}",
                llm_calls=synthesis.llm_calls,
                embed_calls=2,
                tokens_used=synthesis.tokens_used,
            )
        trace_id = self.trace_repo.record_trace(
            space_id=space_id,
            operation="ask",
            input_summary=q_clean,
            details={
                "query_mode": query_mode,
                "has_sufficient_evidence": synthesis.has_sufficient_evidence,
                "cited_memory_ids": valid_mem_ids,
                "cited_note_ids": valid_note_ids,
                "retrieved_memory_count": len(candidate_memories),
                "retrieved_note_count": len(candidate_notes),
            },
            llm_calls=synthesis.llm_calls,
            tokens_used=synthesis.tokens_used,
            duration_ms=duration_ms,
        )

        return {
            "question": q_clean,
            "query_mode": query_mode,
            "answer": synthesis.answer,
            "has_sufficient_evidence": synthesis.has_sufficient_evidence,
            "pending_ingest_count": len(pending_notes),
            "citations": {
                "memories": [
                    m.to_dict() for m in candidate_memories if m.memory_id in valid_mem_ids
                ],
                "notes": [
                    n.to_dict() for n in candidate_notes if n.note_id in valid_note_ids
                ],
                "media": cited_media,
            },
            "trace_id": trace_id,
        }
