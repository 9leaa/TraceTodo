"""Hybrid retrieval and budgeted multi-hop knowledge graph expansion."""

from __future__ import annotations

from collections import deque
import time
from typing import Any

from trace_memory.domain.memories import MemoryItem
from trace_memory.domain.proposals import NotFoundError, PermissionDeniedError
from trace_memory.storage.hybrid_search import HybridSearchIndex, RankedMemoryHit, RankedNoteHit
from trace_memory.storage.repositories import (
    MemoryRepository,
    NoteRepository,
    RelationRepository,
    TraceRepository,
)


class RetrievalService:
    """Executes RRF hybrid search and budgeted graph traversal with strict ACL & forget gates."""

    def __init__(
        self,
        note_repo: NoteRepository,
        memory_repo: MemoryRepository,
        relation_repo: RelationRepository,
        trace_repo: TraceRepository,
        search_index: HybridSearchIndex,
        default_max_hops: int = 2,
        default_max_nodes: int = 10,
        default_max_chars: int = 4000,
    ) -> None:
        self.note_repo = note_repo
        self.memory_repo = memory_repo
        self.relation_repo = relation_repo
        self.trace_repo = trace_repo
        self.search_index = search_index
        self.default_max_hops = default_max_hops
        self.default_max_nodes = default_max_nodes
        self.default_max_chars = default_max_chars

    def search(
        self,
        space_id: str,
        query: str,
        memory_type: str = "",
        top_k: int = 10,
        include_notes: bool = True,
    ) -> dict[str, Any]:
        t0 = time.perf_counter()
        mem_hits: list[RankedMemoryHit] = self.search_index.search_memories(
            space_id=space_id, query=query, memory_type=memory_type, top_k=top_k
        )
        note_hits: list[RankedNoteHit] = (
            self.search_index.search_notes(space_id=space_id, query=query, top_k=min(top_k, 6))
            if include_notes and query.strip()
            else []
        )
        duration_ms = (time.perf_counter() - t0) * 1000.0
        trace_id = self.trace_repo.record_trace(
            space_id=space_id,
            operation="hybrid_search",
            input_summary=query or f"list:{memory_type or 'all'}",
            details={
                "memory_hits": [
                    {
                        "memory_id": h.memory.memory_id,
                        "rrf": round(h.rrf_score, 5),
                        "bm25_rank": h.bm25_rank,
                        "vector_rank": h.vector_rank,
                        "vector_score": h.vector_score,
                    }
                    for h in mem_hits
                ],
                "note_hits": [h.note.note_id for h in note_hits],
            },
            duration_ms=duration_ms,
        )
        return {
            "query": query,
            "space_id": space_id,
            "memories": [h.to_dict() for h in mem_hits],
            "notes": [n.to_dict() for n in note_hits],
            "trace_id": trace_id,
        }

    def read_memory_detail(self, space_id: str, identifier: str) -> dict[str, Any]:
        """Read a single active/historical memory + safe source notes + graph relations."""
        # C09: Check cross-space ID probing without leaking target content
        any_mem = self.memory_repo.get_any_space(identifier.strip())
        if any_mem is not None and any_mem.space_id != space_id:
            raise PermissionDeniedError("Access denied to memory in another space")

        mem = self.memory_repo.get(space_id, identifier)
        if mem is None:
            raise NotFoundError(f"Memory {identifier!r} not found")

        sources: list[dict[str, Any]] = []
        for ev in mem.evidences:
            note = self.note_repo.get(space_id, ev.note_id)
            if note is not None and not note.is_forgotten:
                sources.append(
                    {
                        "note_id": note.note_id,
                        "cited_revision": ev.note_revision,
                        "current_revision": note.revision,
                        "visible_content": note.visible_content(),
                        "event_time": note.event_time,
                    }
                )

        relations = [
            r.to_dict() for r in self.relation_repo.get_neighbors(space_id, mem.memory_id)
        ]
        history = [
            v.to_dict() for v in self.memory_repo.get_history(space_id, mem.memory_id)
        ]
        return {
            "memory": mem.to_dict(),
            "sources": sources,
            "relations": relations,
            "history": history,
        }

    def build_graph_context(
        self,
        space_id: str,
        seed_identifier_or_query: str,
        max_hops: int | None = None,
        max_nodes: int | None = None,
        max_chars: int | None = None,
    ) -> dict[str, Any]:
        """Traverse outgoing and incoming `[[wikilink]]` edges within strict hop/node/char budgets.

        Security & Invariant guarantees:
        - Cycle-safe via `visited` set (handles `A -> B -> A` rings cleanly).
        - Every hop re-verifies `space_id` ownership and `status == 'active'` so neither
          cross-space edges nor forgotten/superseded nodes can resurrect through graph links.
        """
        hops_limit = self.default_max_hops if max_hops is None else max(0, int(max_hops))
        nodes_limit = self.default_max_nodes if max_nodes is None else max(1, int(max_nodes))
        chars_limit = self.default_max_chars if max_chars is None else max(100, int(max_chars))

        seeds: list[MemoryItem] = []
        direct = self.memory_repo.get(space_id, seed_identifier_or_query)
        if direct is not None and direct.status == "active":
            seeds.append(direct)
        else:
            hits = self.search_index.search_memories(
                space_id=space_id, query=seed_identifier_or_query, top_k=2
            )
            seeds.extend(h.memory for h in hits)

        visited: set[str] = set()
        queue: deque[tuple[MemoryItem, int]] = deque()
        nodes_out: list[dict[str, Any]] = []
        edges_out: list[dict[str, Any]] = []
        seen_edges: set[str] = set()
        used_chars = 0
        budget_truncated = False

        for s in seeds:
            if s.memory_id not in visited:
                visited.add(s.memory_id)
                queue.append((s, 0))

        while queue:
            current, depth = queue.popleft()
            if len(nodes_out) >= nodes_limit:
                budget_truncated = True
                break

            node_text_len = len(current.title) + len(current.summary)
            if nodes_out and (used_chars + node_text_len > chars_limit):
                budget_truncated = True
                break

            used_chars += node_text_len
            nodes_out.append(
                {
                    "memory": current.to_dict(),
                    "hop": depth,
                }
            )

            if depth >= hops_limit:
                continue

            for rel in self.relation_repo.get_neighbors(space_id, current.memory_id):
                if rel.space_id != space_id:
                    continue
                if rel.relation_id not in seen_edges:
                    seen_edges.add(rel.relation_id)
                    edges_out.append(rel.to_dict())

                neighbor_id = (
                    rel.to_memory_id
                    if rel.from_memory_id == current.memory_id
                    else rel.from_memory_id
                )
                if not neighbor_id or neighbor_id in visited:
                    continue

                # Re-validate neighbor status & space ownership before expanding!
                neighbor = self.memory_repo.get(space_id, neighbor_id)
                if neighbor is None or neighbor.space_id != space_id or neighbor.status != "active":
                    continue

                visited.add(neighbor.memory_id)
                queue.append((neighbor, depth + 1))

        return {
            "seed": seed_identifier_or_query,
            "nodes": nodes_out,
            "edges": edges_out,
            "budget": {
                "max_hops": hops_limit,
                "max_nodes": nodes_limit,
                "max_chars": chars_limit,
                "used_nodes": len(nodes_out),
                "used_chars": used_chars,
                "budget_truncated": budget_truncated,
            },
        }
