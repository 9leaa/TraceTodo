"""Dual-source (Memory + RawNote) Grounded Question-Answering Service."""

from __future__ import annotations

import inspect
import re
import time
from typing import Any

from trace_memory.domain.memories import MemoryItem
from trace_memory.domain.notes import RawNote
from trace_memory.domain.proposals import ValidationError
from trace_memory.llm.mock_provider import DeterministicLLMProvider
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
    "原本",
    "最早",
    "起初",
    "搬家前",
    "原定",
    "改期前",
    "演进",
    "history",
    "previously",
    "used to",
)

_ENUMERATION_CUES = (
    "有哪些",
    "有什么",
    "列出",
    "所有",
    "全部",
    "汇总",
    "盘点",
    "清单",
    "一共",
    "总共",
    "多少",
    "几项",
    "几个",
    "几条",
    "list all",
    "what are all",
    "how many",
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

    @staticmethod
    def is_enumeration_query(question: str) -> bool:
        q = question.lower()
        return any(cue in q for cue in _ENUMERATION_CUES)

    def ask(self, space_id: str, question: str, top_k: int | None = None) -> dict[str, Any]:
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
        is_enum = self.is_enumeration_query(q_clean)
        if top_k is not None:
            eff_top_k = max(1, min(25, int(top_k)))
        else:
            eff_top_k = 16 if is_enum else 10

        # 1. Hybrid search over active Memories (C12 gate + relative tail-noise filter beyond rank 5)
        base_mem_hits = [
            h
            for h in self.search_index.search_memories(
                space_id=space_id, query=q_clean, top_k=eff_top_k, min_vector_score=0.25
            )
            if h.bm25_rank is not None or h.vector_score >= 0.34
        ]
        mem_hits = []
        if base_mem_hits:
            top_rrf = base_mem_hits[0].rrf_score
            for idx, h in enumerate(base_mem_hits):
                if idx < 5:
                    mem_hits.append(h)
                elif (
                    (is_enum and (h.bm25_rank is not None or h.vector_score >= 0.42))
                    or h.rrf_score >= top_rrf * 0.55
                    or h.vector_score >= 0.48
                ):
                    mem_hits.append(h)

        # 2. 1-hop graph expansion for top-2 seed memories to pull in linked context & active relation edges
        direct_memories: list[MemoryItem] = [h.memory for h in mem_hits]
        candidate_memories: list[MemoryItem] = list(direct_memories)
        seen_mem_ids = {m.memory_id for m in candidate_memories}
        neighbor_memories: list[MemoryItem] = []
        mem_by_id: dict[str, MemoryItem] = {m.memory_id: m for m in candidate_memories}
        graph_relations: list[dict[str, Any]] = []
        seen_rel_keys: set[tuple[str, str, str, str]] = set()

        for seed_mem in direct_memories[:2]:
            graph_ctx = self.retrieval_service.build_graph_context(
                space_id=space_id,
                seed_identifier_or_query=seed_mem.memory_id,
                max_hops=1,
                max_nodes=5,
                max_chars=2000,
            )
            for node in graph_ctx.get("nodes", []):
                nid = str(node["memory"]["memory_id"])
                if nid not in mem_by_id:
                    extra = self.memory_repo.get(space_id, nid)
                    if extra is not None and extra.status == "active":
                        mem_by_id[nid] = extra
                if nid in mem_by_id and nid not in seen_mem_ids:
                    extra = mem_by_id[nid]
                    candidate_memories.append(extra)
                    neighbor_memories.append(extra)
                    seen_mem_ids.add(nid)
            for edge in graph_ctx.get("edges", []):
                from_id = str(edge.get("from_memory_id") or "")
                to_id = str(edge.get("to_memory_id") or "")
                to_txt = str(edge.get("to_target_text") or "")
                rtype = str(edge.get("relation_type") or "relates_to")
                if to_txt == "[FORGOTTEN]" or not from_id:
                    continue
                from_mem = mem_by_id.get(from_id) or self.memory_repo.get(space_id, from_id)
                if from_mem is None or from_mem.status != "active":
                    continue
                to_mem = (mem_by_id.get(to_id) or self.memory_repo.get(space_id, to_id)) if to_id else None
                if to_id and (to_mem is None or to_mem.status != "active"):
                    continue
                rel_key = (from_id, rtype, to_id, to_txt)
                if rel_key in seen_rel_keys:
                    continue
                seen_rel_keys.add(rel_key)
                graph_relations.append(
                    {
                        "from_memory_id": from_id,
                        "from_title": from_mem.title,
                        "relation_type": rtype,
                        "to_memory_id": to_id,
                        "to_title": to_mem.title if to_mem is not None else to_txt,
                        "context": str(edge.get("context") or ""),
                    }
                )

        # Build bounded LLM memory context with adaptive window & reserved slots for 1-hop neighbors
        llm_mem_cap = min(24, eff_top_k + 3)
        reserve_neighbors = min(3, len(neighbor_memories))
        direct_slot_cap = min(eff_top_k, max(1, llm_mem_cap - reserve_neighbors))
        direct_head = direct_memories[:direct_slot_cap]
        llm_memories: list[MemoryItem] = list(direct_head)
        llm_mem_ids = {m.memory_id for m in llm_memories}
        for nm in neighbor_memories:
            if len(llm_memories) >= llm_mem_cap:
                break
            if nm.memory_id not in llm_mem_ids:
                llm_memories.append(nm)
                llm_mem_ids.add(nm.memory_id)
        for cm in candidate_memories:
            if len(llm_memories) >= llm_mem_cap:
                break
            if cm.memory_id not in llm_mem_ids:
                llm_memories.append(cm)
                llm_mem_ids.add(cm.memory_id)

        # 3. Hybrid search over ground-truth RawNotes (scaled proportionally)
        note_top_k = min(10 if is_enum else 8, max(3, eff_top_k))
        note_hits = [
            nh
            for nh in self.search_index.search_notes(
                space_id=space_id, query=q_clean, top_k=note_top_k, min_vector_score=0.25
            )
            if nh.bm25_rank is not None or nh.vector_score >= 0.34
        ]
        candidate_notes = [nh.note for nh in note_hits]
        seen_note_ids = {n.note_id for n in candidate_notes}

        # Also pull direct supporting source notes of top memories if not already included
        for m in llm_memories[:6]:
            for ev in m.evidences:
                if ev.note_id not in seen_note_ids and len(candidate_notes) < note_top_k:
                    src_note = self.note_repo.get(space_id, ev.note_id)
                    if src_note is not None and not src_note.is_forgotten:
                        candidate_notes.append(src_note)
                        seen_note_ids.add(src_note.note_id)

        # 3b. Read-Your-Own-Writes: inject latest queued/processing RawNotes without evicting top search hits
        pending_notes: list[RawNote] = []
        pending_by_id: dict[str, RawNote] = {}
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
                pending_by_id[tagged.note_id] = tagged

        base_notes = [pending_by_id.get(n.note_id, n) for n in candidate_notes]
        q_tokens = {tok for tok in re.findall(r"[a-zA-Z0-9\u4e00-\u9fff]+", q_clean.lower()) if len(tok) >= 2}
        unseen_pending = [pn for pn in pending_notes if pn.note_id not in seen_note_ids]
        unseen_pending.sort(
            key=lambda pn: sum(1 for tok in q_tokens if tok in pn.visible_content().lower()),
            reverse=True,
        )
        candidate_notes = list(base_notes) + unseen_pending
        for pn in unseen_pending:
            seen_note_ids.add(pn.note_id)

        llm_note_cap = (
            min(12, max(6, note_top_k))
            if (top_k is None or eff_top_k >= 6)
            else max(3, eff_top_k + 1)
        )
        reserve_pending = min(2, len(unseen_pending))
        head_limit = max(2, llm_note_cap - reserve_pending)
        raw_llm_notes: list[RawNote] = list(base_notes[:head_limit])
        llm_note_ids = {n.note_id for n in raw_llm_notes}
        for pn in unseen_pending:
            if len(raw_llm_notes) >= llm_note_cap:
                break
            if pn.note_id not in llm_note_ids:
                raw_llm_notes.append(pn)
                llm_note_ids.add(pn.note_id)
        for bn in base_notes[head_limit:]:
            if len(raw_llm_notes) >= llm_note_cap:
                break
            if bn.note_id not in llm_note_ids:
                raw_llm_notes.append(bn)
                llm_note_ids.add(bn.note_id)

        # 4. Load version history only when answering historical evolution questions (capped)
        history_versions: list[dict[str, Any]] = []
        if query_mode == "history":
            for m in llm_memories[:3]:
                ver_list = self.memory_repo.get_history(space_id, m.memory_id)
                if len(ver_list) > 4:
                    selected_vers = [ver_list[0], *ver_list[-3:]]
                else:
                    selected_vers = ver_list
                for ver in selected_vers:
                    history_versions.append(ver.to_dict())
            history_versions = history_versions[:12]

        llm_notes: list[RawNote] = []
        for n in raw_llm_notes:
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

        def _invoke_synth(provider: Any) -> Any:
            sig = inspect.signature(provider.synthesize_answer)
            kwargs: dict[str, Any] = {
                "question": q_clean,
                "query_mode": query_mode,
                "memories": llm_memories,
                "notes": llm_notes,
                "history_versions": history_versions,
            }
            if "graph_relations" in sig.parameters:
                kwargs["graph_relations"] = graph_relations
            return provider.synthesize_answer(**kwargs)

        try:
            synthesis = _invoke_synth(self.llm)
        except Exception:
            synthesis = _invoke_synth(DeterministicLLMProvider())

        # Strict Citation Hallucination Guard: only allow IDs actually sent to synthesis (and clear when insufficient)
        if not synthesis.has_sufficient_evidence:
            valid_mem_ids: list[str] = []
            valid_note_ids: list[str] = []
        else:
            valid_mem_ids = list(
                dict.fromkeys(mid for mid in synthesis.cited_memory_ids if mid in llm_mem_ids)
            )
            valid_note_ids = list(
                dict.fromkeys(nid for nid in synthesis.cited_note_ids if nid in llm_note_ids)
            )

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
                "enumeration_intent": is_enum,
                "effective_top_k": eff_top_k,
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
