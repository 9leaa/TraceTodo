"""Hybrid retrieval and budgeted multi-hop knowledge graph expansion."""

from __future__ import annotations

from collections import defaultdict, deque
import hashlib
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
from trace_memory.storage.vector_store import MEMORIES_COLLECTION, cosine_similarity


_SELF_SUBJECT_ALIASES = frozenset({"", "self", "user", "me", "我", "本人", "自己"})


def extract_memory_subject(mem: MemoryItem) -> str:
    """Normalize the primary subject/owner entity of a memory card (`self` vs named person)."""
    p = mem.payload or {}
    raw = str(p.get("subject_entity") or "").strip()
    if not raw:
        if mem.assertion_kind == "inferred":
            return "self"
        if mem.memory_type == "task":
            raw = str(p.get("assignee") or "").strip()
        else:
            raw = str(p.get("subject") or "").strip()
    if raw.lower().startswith("person:"):
        raw = raw[7:].strip()
    elif raw.lower().startswith("relation:"):
        raw = raw[9:].strip()
    if raw.lower() in _SELF_SUBJECT_ALIASES:
        return "self"
    return raw


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
        any_seed = self.memory_repo.get_any_space(seed_identifier_or_query.strip())
        if any_seed is not None and any_seed.space_id != space_id:
            raise PermissionDeniedError("Cross-space graph access denied")
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
                if rel.space_id != space_id or rel.to_target_text == "[FORGOTTEN]":
                    continue

                neighbor_id = (
                    rel.to_memory_id
                    if rel.from_memory_id == current.memory_id
                    else rel.from_memory_id
                )
                neighbor: MemoryItem | None = None
                if neighbor_id:
                    neighbor = self.memory_repo.get(space_id, neighbor_id)
                    if neighbor is None or neighbor.space_id != space_id or neighbor.status != "active":
                        continue
                elif rel.to_memory_id == "" and rel.from_memory_id == current.memory_id:
                    # Also hide unresolved edges whose target was previously forgotten
                    tgt_check = self.memory_repo.get(space_id, rel.to_target_text, include_forgotten=True)
                    if tgt_check is not None and tgt_check.status != "active":
                        continue

                if rel.relation_id not in seen_edges:
                    seen_edges.add(rel.relation_id)
                    edges_out.append(rel.to_dict())

                if neighbor is not None and neighbor.memory_id not in visited:
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

    def build_graph_canvas(
        self,
        space_id: str,
        seed: str = "",
        memory_type: str = "",
        subject: str = "",
        max_hops: int = 2,
        max_nodes: int = 120,
        include_semantic: bool = True,
        semantic_threshold: float = 0.52,
        max_semantic_per_node: int = 3,
        include_unresolved: bool = True,
        include_co_evidence: bool = True,
    ) -> dict[str, Any]:
        """Build an Obsidian-style interactive graph payload (explicit wikilinks, Dream provenance,
        co-evidence links, unresolved ghost forward-refs, and degree-capped semantic dashed edges).

        Security & Privacy Invariants:
        - Strictly enforces `space_id` isolation (C09) and `status == 'active'` + non-forgotten checks (C07).
        - Zero external LLM/Embedding API calls: reuses already-persisted space vectors in memory.
        """
        cleaned_seed = seed.strip()
        if cleaned_seed:
            any_mem = self.memory_repo.get_any_space(cleaned_seed)
            if any_mem is not None and any_mem.space_id != space_id:
                raise PermissionDeniedError("Access denied to memory in another space")

        hops_limit = max(1, min(4, int(max_hops)))
        nodes_limit = max(1, min(250, int(max_nodes)))
        sem_thresh = max(0.25, min(0.98, float(semantic_threshold)))
        sem_cap = max(1, min(8, int(max_semantic_per_node)))

        pool_limit = max(nodes_limit * 2, 200)
        active_items = [
            m
            for m in self.memory_repo.list_memories(
                space_id=space_id, status="active", limit=pool_limit
            )
            if m.space_id == space_id and m.status == "active"
        ]
        active_by_id: dict[str, MemoryItem] = {m.memory_id: m for m in active_items}

        # Collect available subjects across the whole active space for UI filter dropdowns
        subject_by_id: dict[str, str] = {}
        all_subjects_set: set[str] = set()
        for m in active_items:
            subj = extract_memory_subject(m)
            subject_by_id[m.memory_id] = subj
            all_subjects_set.add(subj)
        sorted_subjects = sorted(all_subjects_set, key=lambda s: (0 if s == "self" else 1, s))

        type_tokens = {
            t.strip().lower() for t in memory_type.split(",") if t.strip()
        }
        subj_filter = subject.strip().lower()
        if subj_filter in ("user", "me", "我", "本人", "自己"):
            subj_filter = "self"

        def _matches_filter(mem: MemoryItem) -> bool:
            if type_tokens:
                matched_type = mem.memory_type.lower() in type_tokens or (
                    "dream" in type_tokens and mem.assertion_kind == "inferred"
                )
                if not matched_type:
                    return False
            if subj_filter:
                if subject_by_id.get(mem.memory_id, "self").lower() != subj_filter:
                    return False
            return True

        # Build candidate edges across active space memories
        candidate_edges: list[dict[str, Any]] = []
        explicit_pair_keys: set[tuple[str, str]] = set()
        ghost_nodes_by_id: dict[str, dict[str, Any]] = {}

        for rel in self.relation_repo.list_all(space_id):
            if rel.space_id != space_id or rel.from_memory_id not in active_by_id:
                continue
            if rel.to_target_text == "[FORGOTTEN]":
                continue

            if rel.to_memory_id and rel.to_memory_id in active_by_id:
                if rel.to_memory_id == rel.from_memory_id:
                    continue
                is_derived = rel.relation_type == "derived_from"
                edge_kind = "derived_from" if is_derived else "wikilink"
                pair = tuple(sorted((rel.from_memory_id, rel.to_memory_id)))
                explicit_pair_keys.add(pair)  # type: ignore[arg-type]
                candidate_edges.append(
                    {
                        "id": rel.relation_id,
                        "source": rel.from_memory_id,
                        "target": rel.to_memory_id,
                        "edge_kind": edge_kind,
                        "relation_type": rel.relation_type,
                        "label": "Dream 归纳" if is_derived else rel.relation_type,
                        "weight": 0.92 if is_derived else 1.0,
                        "directed": True,
                        "dashed": False,
                        "resolved": True,
                        "context": rel.context,
                    }
                )
            elif not rel.to_memory_id and include_unresolved and rel.to_target_text.strip():
                target_label = rel.to_target_text.strip()
                # C07 guard: never render a ghost node if the target existed before and was forgotten/superseded
                inactive_existing = self.memory_repo.get(
                    space_id, target_label, include_forgotten=True
                )
                if inactive_existing is not None:
                    continue
                ghost_hash = hashlib.sha1(target_label.lower().encode("utf-8")).hexdigest()[:10]
                ghost_id = f"ghost_{ghost_hash}"
                if ghost_id not in ghost_nodes_by_id:
                    ghost_nodes_by_id[ghost_id] = {
                        "id": ghost_id,
                        "memory_id": "",
                        "is_ghost": True,
                        "title": target_label,
                        "summary": "尚未创建的双链前向引用节点（点击可一键补建并自动连线）",
                        "uri": f"[[{target_label}]]",
                        "permalink": target_label,
                        "memory_type": "ghost",
                        "assertion_kind": "unresolved",
                        "user_locked": False,
                        "version": 0,
                        "subject": "",
                        "tags": [],
                        "hop": 0,
                        "degree": 0,
                        "explicit_degree": 0,
                        "evidence_count": 0,
                        "quadrant": "",
                        "task_status": "",
                        "due_at": "",
                        "updated_at": "",
                    }
                candidate_edges.append(
                    {
                        "id": rel.relation_id,
                        "source": rel.from_memory_id,
                        "target": ghost_id,
                        "edge_kind": "ghost_ref",
                        "relation_type": rel.relation_type,
                        "label": rel.relation_type,
                        "weight": 0.65,
                        "directed": True,
                        "dashed": True,
                        "resolved": False,
                        "context": rel.context,
                    }
                )

        # Shared RawNote co-evidence edges (for stated memories extracted from the same note)
        if include_co_evidence:
            note_to_mem_ids: dict[str, list[str]] = defaultdict(list)
            for m in active_items:
                if m.assertion_kind == "inferred":
                    continue
                for ev in m.evidences:
                    note_obj = self.note_repo.get(space_id, ev.note_id)
                    if note_obj is not None and not note_obj.is_forgotten:
                        if m.memory_id not in note_to_mem_ids[ev.note_id]:
                            note_to_mem_ids[ev.note_id].append(m.memory_id)

            for note_id, mids in note_to_mem_ids.items():
                if len(mids) < 2 or len(mids) > 8:
                    continue
                for i in range(len(mids)):
                    for j in range(i + 1, len(mids)):
                        u, v = mids[i], mids[j]
                        pair = (u, v) if u < v else (v, u)
                        if pair in explicit_pair_keys:
                            continue
                        explicit_pair_keys.add(pair)
                        candidate_edges.append(
                            {
                                "id": f"coev_{note_id}_{u}_{v}",
                                "source": u,
                                "target": v,
                                "edge_kind": "co_evidence",
                                "relation_type": "co_evidence",
                                "label": "同源记录",
                                "weight": 0.78,
                                "directed": False,
                                "dashed": False,
                                "resolved": True,
                                "context": f"同源于原话 {note_id}",
                            }
                        )

        # Degree-capped high-similarity semantic dashed edges
        if include_semantic and len(active_items) >= 2:
            vec_fetcher = getattr(self.search_index.vector_store, "get_space_vectors", None)
            if callable(vec_fetcher):
                vecs = vec_fetcher(
                    MEMORIES_COLLECTION, space_id, set(active_by_id.keys())
                )
                vec_ids = sorted(vecs.keys())
                sem_candidates: list[tuple[float, str, str]] = []
                for i in range(len(vec_ids)):
                    uid = vec_ids[i]
                    u_vec, u_norm = vecs[uid]
                    for j in range(i + 1, len(vec_ids)):
                        vid = vec_ids[j]
                        if (uid, vid) in explicit_pair_keys:
                            continue
                        v_vec, v_norm = vecs[vid]
                        sim = cosine_similarity(u_vec, u_norm, v_vec, v_norm)
                        if sim >= sem_thresh:
                            sem_candidates.append((sim, uid, vid))

                sem_candidates.sort(key=lambda item: item[0], reverse=True)
                sem_deg: dict[str, int] = defaultdict(int)
                for sim, uid, vid in sem_candidates:
                    if sem_deg[uid] >= sem_cap or sem_deg[vid] >= sem_cap:
                        continue
                    sem_deg[uid] += 1
                    sem_deg[vid] += 1
                    candidate_edges.append(
                        {
                            "id": f"sem_{uid}_{vid}",
                            "source": uid,
                            "target": vid,
                            "edge_kind": "semantic",
                            "relation_type": "semantic_similar",
                            "label": f"语义相似 {sim:.2f}",
                            "weight": round(sim, 4),
                            "directed": False,
                            "dashed": True,
                            "resolved": True,
                            "context": f"向量余弦相似度 {sim:.3f}",
                        }
                    )

        # Select visible nodes (Local BFS around seed vs Global filtered view)
        hop_by_node_id: dict[str, int] = {}
        mode = "local" if cleaned_seed else "global"

        if cleaned_seed:
            seed_items: list[MemoryItem] = []
            direct = self.memory_repo.get(space_id, cleaned_seed)
            if direct is not None and direct.space_id == space_id and direct.status == "active":
                seed_items.append(direct)
            else:
                hits = self.search_index.search_memories(
                    space_id=space_id, query=cleaned_seed, top_k=3
                )
                seed_items.extend(
                    h.memory for h in hits if h.memory.memory_id in active_by_id
                )

            adj: dict[str, list[str]] = defaultdict(list)
            for e in candidate_edges:
                s_id, t_id = e["source"], e["target"]
                adj[s_id].append(t_id)
                adj[t_id].append(s_id)

            bfs_q: deque[tuple[str, int]] = deque()
            for sm in seed_items:
                if sm.memory_id not in hop_by_node_id and len(hop_by_node_id) < nodes_limit:
                    hop_by_node_id[sm.memory_id] = 0
                    bfs_q.append((sm.memory_id, 0))

            while bfs_q and len(hop_by_node_id) < nodes_limit:
                curr_id, depth = bfs_q.popleft()
                if depth >= hops_limit:
                    continue
                for nb_id in adj.get(curr_id, []):
                    if nb_id in hop_by_node_id:
                        continue
                    if nb_id in active_by_id:
                        if not _matches_filter(active_by_id[nb_id]):
                            continue
                    elif nb_id in ghost_nodes_by_id:
                        if not include_unresolved or type_tokens or subj_filter:
                            continue
                    else:
                        continue
                    hop_by_node_id[nb_id] = depth + 1
                    if len(hop_by_node_id) >= nodes_limit:
                        break
                    bfs_q.append((nb_id, depth + 1))
        else:
            for m in active_items:
                if not _matches_filter(m):
                    continue
                hop_by_node_id[m.memory_id] = 0
                if len(hop_by_node_id) >= nodes_limit:
                    break

            if include_unresolved and not type_tokens and not subj_filter:
                for e in candidate_edges:
                    if len(hop_by_node_id) >= nodes_limit:
                        break
                    if (
                        e["edge_kind"] == "ghost_ref"
                        and e["source"] in hop_by_node_id
                        and e["target"] in ghost_nodes_by_id
                    ):
                        hop_by_node_id[e["target"]] = 1

        visible_ids = set(hop_by_node_id.keys())
        visible_edges = [
            e
            for e in candidate_edges
            if e["source"] in visible_ids and e["target"] in visible_ids
        ]

        deg_all: dict[str, int] = defaultdict(int)
        deg_explicit: dict[str, int] = defaultdict(int)
        for e in visible_edges:
            u, v = e["source"], e["target"]
            deg_all[u] += 1
            deg_all[v] += 1
            if e["edge_kind"] in ("wikilink", "derived_from", "ghost_ref"):
                deg_explicit[u] += 1
                deg_explicit[v] += 1

        nodes_out: list[dict[str, Any]] = []
        real_count = 0
        ghost_count = 0
        for nid, hop_val in hop_by_node_id.items():
            if nid in active_by_id:
                m = active_by_id[nid]
                p = m.payload or {}
                real_count += 1
                nodes_out.append(
                    {
                        "id": m.memory_id,
                        "memory_id": m.memory_id,
                        "is_ghost": False,
                        "title": m.title,
                        "summary": m.summary,
                        "uri": f"memory://{m.permalink}",
                        "permalink": m.permalink,
                        "memory_type": m.memory_type,
                        "assertion_kind": m.assertion_kind,
                        "user_locked": m.user_locked,
                        "version": m.version,
                        "subject": subject_by_id.get(m.memory_id, "self"),
                        "tags": list(m.tags),
                        "hop": hop_val,
                        "degree": deg_all[nid],
                        "explicit_degree": deg_explicit[nid],
                        "evidence_count": len(m.evidences),
                        "quadrant": str(p.get("quadrant") or ""),
                        "task_status": str(p.get("task_status") or ""),
                        "due_at": str(p.get("due_at") or ""),
                        "updated_at": m.updated_at,
                    }
                )
            elif nid in ghost_nodes_by_id:
                g = dict(ghost_nodes_by_id[nid])
                g["hop"] = hop_val
                g["degree"] = deg_all[nid]
                g["explicit_degree"] = deg_explicit[nid]
                ghost_count += 1
                nodes_out.append(g)

        # Sort hub nodes first for clean list fallback & deterministic initial spiral layout
        nodes_out.sort(
            key=lambda n: (n["hop"], 1 if n["is_ghost"] else 0, -n["degree"], n["id"])
        )

        kind_counts: dict[str, int] = defaultdict(int)
        for e in visible_edges:
            kind_counts[e["edge_kind"]] += 1

        isolated_count = sum(1 for n in nodes_out if n["degree"] == 0)

        return {
            "space_id": space_id,
            "mode": mode,
            "seed": cleaned_seed,
            "nodes": nodes_out,
            "edges": visible_edges,
            "stats": {
                "total_nodes": len(nodes_out),
                "real_nodes": real_count,
                "ghost_nodes": ghost_count,
                "total_edges": len(visible_edges),
                "wikilink_edges": kind_counts["wikilink"],
                "derived_edges": kind_counts["derived_from"],
                "co_evidence_edges": kind_counts["co_evidence"],
                "semantic_edges": kind_counts["semantic"],
                "ghost_edges": kind_counts["ghost_ref"],
                "isolated_nodes": isolated_count,
                "subjects": sorted_subjects,
            },
        }
