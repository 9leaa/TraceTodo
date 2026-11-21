"""Hybrid Sparse (SQLite FTS5 BM25) + Dense (VectorStore Cosine) Retrieval with RRF Fusion."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from trace_memory.domain.memories import MemoryItem
from trace_memory.domain.notes import RawNote
from trace_memory.storage.repositories import MemoryRepository, NoteRepository
from trace_memory.storage.vector_store import (
    MEMORIES_COLLECTION,
    NOTES_COLLECTION,
    EmbeddingProviderProtocol,
    VectorStore,
)


@dataclass(frozen=True)
class RankedMemoryHit:
    memory: MemoryItem
    rrf_score: float
    bm25_rank: int | None
    bm25_score: float
    vector_rank: int | None
    vector_score: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "memory": self.memory.to_dict(),
            "rrf_score": round(self.rrf_score, 6),
            "bm25_rank": self.bm25_rank,
            "bm25_score": round(self.bm25_score, 4),
            "vector_rank": self.vector_rank,
            "vector_score": round(self.vector_score, 4),
        }


@dataclass(frozen=True)
class RankedNoteHit:
    note: RawNote
    rrf_score: float
    bm25_rank: int | None
    bm25_score: float
    vector_rank: int | None
    vector_score: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "note": self.note.to_dict(),
            "rrf_score": round(self.rrf_score, 6),
            "bm25_rank": self.bm25_rank,
            "bm25_score": round(self.bm25_score, 4),
            "vector_rank": self.vector_rank,
            "vector_score": round(self.vector_score, 4),
        }


class HybridSearchIndex:
    """Combines BM25 keyword precision with Dense Vector semantic recall via Reciprocal Rank Fusion."""

    def __init__(
        self,
        note_repo: NoteRepository,
        memory_repo: MemoryRepository,
        vector_store: VectorStore,
        embedder: EmbeddingProviderProtocol,
        rrf_k: int = 60,
        vector_only_min_score: float = 0.0,
    ) -> None:
        self.note_repo = note_repo
        self.memory_repo = memory_repo
        self.vector_store = vector_store
        self.embedder = embedder
        self.rrf_k = rrf_k
        # Pure semantic hits (no BM25 keyword match) must clear this floor, otherwise
        # gibberish queries surface every memory with a mediocre cosine score.
        self.vector_only_min_score = max(0.0, float(vector_only_min_score))

    def search_memories(
        self,
        space_id: str,
        query: str,
        memory_type: str = "",
        top_k: int = 10,
        min_vector_score: float = 0.25,
    ) -> list[RankedMemoryHit]:
        if not query.strip():
            items = self.memory_repo.list_memories(
                space_id=space_id, memory_type=memory_type, status="active", limit=top_k
            )
            return [
                RankedMemoryHit(
                    memory=m,
                    rrf_score=1.0 / (self.rrf_k + idx + 1),
                    bm25_rank=None,
                    bm25_score=0.0,
                    vector_rank=None,
                    vector_score=0.0,
                )
                for idx, m in enumerate(items)
            ]

        pool_size = max(top_k * 3, 20)
        bm25_hits = self.memory_repo.search_bm25(
            space_id=space_id, query=query, memory_type=memory_type, limit=pool_size
        )
        query_vec = self.embedder.embed(query)
        vec_filters: dict[str, Any] = {"status": "active"}
        if memory_type:
            vec_filters["memory_type"] = memory_type
        vec_hits = [
            h
            for h in self.vector_store.search(
                collection=MEMORIES_COLLECTION,
                space_id=space_id,
                query_vector=query_vec,
                top_k=pool_size,
                filters=vec_filters,
            )
            if h.score >= min_vector_score
        ]

        items_by_id: dict[str, MemoryItem] = {}
        bm25_meta: dict[str, tuple[int, float]] = {}
        vec_meta: dict[str, tuple[int, float]] = {}

        for idx, (mem, score) in enumerate(bm25_hits, start=1):
            items_by_id[mem.memory_id] = mem
            bm25_meta[mem.memory_id] = (idx, score)

        for idx, vhit in enumerate(vec_hits, start=1):
            vec_meta[vhit.doc_id] = (idx, vhit.score)
            if vhit.doc_id not in items_by_id:
                mem_obj = self.memory_repo.get(space_id, vhit.doc_id)
                # Double-check relational status so stale projections never leak
                if mem_obj is not None and mem_obj.status == "active":
                    if not memory_type or mem_obj.memory_type == memory_type:
                        items_by_id[vhit.doc_id] = mem_obj

        fused: list[RankedMemoryHit] = []
        for mem_id, mem in items_by_id.items():
            b_rank, b_score = bm25_meta.get(mem_id, (None, 0.0))
            v_rank, v_score = vec_meta.get(mem_id, (None, 0.0))
            if b_rank is None and v_score < self.vector_only_min_score:
                continue
            rrf = 0.0
            if b_rank is not None:
                rrf += 1.15 / (self.rrf_k + b_rank)
            if v_rank is not None:
                rrf += 1.0 / (self.rrf_k + v_rank) + 0.02 * v_score
            fused.append(
                RankedMemoryHit(
                    memory=mem,
                    rrf_score=rrf,
                    bm25_rank=b_rank,
                    bm25_score=b_score,
                    vector_rank=v_rank,
                    vector_score=v_score,
                )
            )

        fused.sort(key=lambda x: (x.rrf_score, x.vector_score, x.bm25_score), reverse=True)
        return fused[:top_k]

    def search_notes(
        self,
        space_id: str,
        query: str,
        top_k: int = 8,
        min_vector_score: float = 0.25,
    ) -> list[RankedNoteHit]:
        if not query.strip():
            return []

        pool_size = max(top_k * 3, 16)
        bm25_hits = self.note_repo.search_bm25(space_id=space_id, query=query, limit=pool_size)
        query_vec = self.embedder.embed(query)
        vec_hits = [
            h
            for h in self.vector_store.search(
                collection=NOTES_COLLECTION,
                space_id=space_id,
                query_vector=query_vec,
                top_k=pool_size,
                filters={"is_forgotten": False},
            )
            if h.score >= min_vector_score
        ]

        notes_by_id: dict[str, RawNote] = {}
        bm25_meta: dict[str, tuple[int, float]] = {}
        vec_meta: dict[str, tuple[int, float]] = {}

        for idx, (note, score) in enumerate(bm25_hits, start=1):
            notes_by_id[note.note_id] = note
            bm25_meta[note.note_id] = (idx, score)

        for idx, vhit in enumerate(vec_hits, start=1):
            vec_meta[vhit.doc_id] = (idx, vhit.score)
            if vhit.doc_id not in notes_by_id:
                note_obj = self.note_repo.get(space_id, vhit.doc_id)
                if note_obj is not None and not note_obj.is_forgotten:
                    notes_by_id[vhit.doc_id] = note_obj

        fused: list[RankedNoteHit] = []
        for note_id, note in notes_by_id.items():
            b_rank, b_score = bm25_meta.get(note_id, (None, 0.0))
            v_rank, v_score = vec_meta.get(note_id, (None, 0.0))
            if b_rank is None and v_score < self.vector_only_min_score:
                continue
            rrf = 0.0
            if b_rank is not None:
                rrf += 1.15 / (self.rrf_k + b_rank)
            if v_rank is not None:
                rrf += 1.0 / (self.rrf_k + v_rank) + 0.02 * v_score
            fused.append(
                RankedNoteHit(
                    note=note,
                    rrf_score=rrf,
                    bm25_rank=b_rank,
                    bm25_score=b_score,
                    vector_rank=v_rank,
                    vector_score=v_score,
                )
            )

        fused.sort(key=lambda x: (x.rrf_score, x.vector_score, x.bm25_score), reverse=True)
        return fused[:top_k]
