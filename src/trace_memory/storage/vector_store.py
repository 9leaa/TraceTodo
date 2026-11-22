"""Pluggable Vector Database (`QdrantVectorStore` + `SQLiteVectorStore`) and Vector Lifecycle.

Why Vector Lifecycle + Metadata Pre-filtering matters for Agent Memory:
Unlike static RAG corpora where chunks are write-once, agent memories evolve (`superseded`)
and can be revoked (`forgotten`). Without synchronous lifecycle cleanup and pre-filtering
(`space_id` + `status == 'active'`), dense vector similarity search resurrects stale facts
or forgotten private information.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import math
from pathlib import Path
from typing import Any, Protocol
import uuid

from trace_memory.domain.memories import MemoryItem
from trace_memory.domain.notes import RawNote, utc_now_iso
from trace_memory.storage.database import Database

NOTES_COLLECTION = "trace_memory_notes"
MEMORIES_COLLECTION = "trace_memory_memories"


@dataclass(frozen=True)
class VectorHit:
    doc_id: str
    score: float
    metadata: dict[str, Any]


def _vector_norm(vec: list[float]) -> float:
    return math.sqrt(sum(x * x for x in vec))


def cosine_similarity(vec_a: list[float], norm_a: float, vec_b: list[float], norm_b: float) -> float:
    if norm_a <= 1e-12 or norm_b <= 1e-12 or len(vec_a) != len(vec_b):
        return 0.0
    dot = sum(a * b for a, b in zip(vec_a, vec_b))
    return dot / (norm_a * norm_b)


class VectorStore(Protocol):
    """Pluggable dense vector database contract."""

    backend_name: str

    def upsert(
        self,
        collection: str,
        doc_id: str,
        space_id: str,
        vector: list[float],
        metadata: dict[str, Any],
    ) -> None: ...

    def delete(self, collection: str, doc_id: str) -> None: ...

    def search(
        self,
        collection: str,
        space_id: str,
        query_vector: list[float],
        top_k: int = 10,
        filters: dict[str, Any] | None = None,
    ) -> list[VectorHit]: ...

    def count(self, collection: str, space_id: str = "") -> int: ...

    def get_space_vectors(
        self,
        collection: str,
        space_id: str,
        doc_ids: set[str] | None = None,
    ) -> dict[str, tuple[list[float], float]]: ...


class SQLiteVectorStore:
    """Zero-dependency persistent Cosine Similarity vector store with metadata pre-filtering."""

    backend_name = "sqlite_cosine"

    def __init__(self, db: Database) -> None:
        self.db = db

    def upsert(
        self,
        collection: str,
        doc_id: str,
        space_id: str,
        vector: list[float],
        metadata: dict[str, Any],
    ) -> None:
        norm = _vector_norm(vector)
        meta = dict(metadata)
        meta["space_id"] = space_id
        meta["doc_id"] = doc_id
        with self.db.transaction() as conn:
            conn.execute(
                """
                INSERT INTO vector_records
                    (collection, doc_id, space_id, vector_json, norm, metadata_json, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(collection, doc_id) DO UPDATE SET
                    space_id = excluded.space_id,
                    vector_json = excluded.vector_json,
                    norm = excluded.norm,
                    metadata_json = excluded.metadata_json,
                    updated_at = excluded.updated_at
                """,
                (
                    collection,
                    doc_id,
                    space_id,
                    json.dumps(vector),
                    norm,
                    json.dumps(meta, ensure_ascii=False),
                    utc_now_iso(),
                ),
            )

    def delete(self, collection: str, doc_id: str) -> None:
        with self.db.transaction() as conn:
            conn.execute(
                "DELETE FROM vector_records WHERE collection = ? AND doc_id = ?",
                (collection, doc_id),
            )

    def search(
        self,
        collection: str,
        space_id: str,
        query_vector: list[float],
        top_k: int = 10,
        filters: dict[str, Any] | None = None,
    ) -> list[VectorHit]:
        q_norm = _vector_norm(query_vector)
        if q_norm <= 1e-12:
            return []

        rows = self.db.query_all(
            "SELECT doc_id, vector_json, norm, metadata_json FROM vector_records WHERE collection = ? AND space_id = ?",
            (collection, space_id),
        )
        scored: list[VectorHit] = []
        for row in rows:
            meta: dict[str, Any] = json.loads(row["metadata_json"])
            # Metadata Pre-filtering before similarity ranking
            if filters:
                matched = True
                for k, expected in filters.items():
                    if expected is None or expected == "":
                        continue
                    if meta.get(k) != expected:
                        matched = False
                        break
                if not matched:
                    continue
            vec = json.loads(row["vector_json"])
            sim = cosine_similarity(query_vector, q_norm, vec, float(row["norm"]))
            if sim > 0.0:
                scored.append(VectorHit(doc_id=row["doc_id"], score=round(sim, 6), metadata=meta))

        scored.sort(key=lambda h: h.score, reverse=True)
        return scored[:top_k]

    def count(self, collection: str, space_id: str = "") -> int:
        if space_id:
            row = self.db.query_one(
                "SELECT COUNT(*) AS cnt FROM vector_records WHERE collection = ? AND space_id = ?",
                (collection, space_id),
            )
        else:
            row = self.db.query_one(
                "SELECT COUNT(*) AS cnt FROM vector_records WHERE collection = ?",
                (collection,),
            )
        return int(row["cnt"]) if row else 0

    def get_space_vectors(
        self,
        collection: str,
        space_id: str,
        doc_ids: set[str] | None = None,
    ) -> dict[str, tuple[list[float], float]]:
        rows = self.db.query_all(
            "SELECT doc_id, vector_json, norm FROM vector_records WHERE collection = ? AND space_id = ?",
            (collection, space_id),
        )
        out: dict[str, tuple[list[float], float]] = {}
        for r in rows:
            did = str(r["doc_id"])
            if doc_ids is not None and did not in doc_ids:
                continue
            vec = json.loads(r["vector_json"])
            norm = float(r["norm"] or 0.0)
            if norm <= 1e-12:
                norm = _vector_norm(vec)
            if norm > 1e-12:
                out[did] = (vec, norm)
        return out


class QdrantVectorStore:
    """Production HNSW VectorStore backed by Qdrant (local embedded disk or remote server)."""

    backend_name = "qdrant_hnsw"

    def __init__(
        self,
        embedding_dim: int,
        path: Path | None = None,
        url: str = "",
        api_key: str = "",
    ) -> None:
        try:
            from qdrant_client import QdrantClient
            from qdrant_client.models import Distance, VectorParams
        except ImportError as exc:
            raise RuntimeError(
                "qdrant-client is not installed. Install with `pip install qdrant-client` "
                "or set TRACE_MEMORY_VECTOR_BACKEND=sqlite."
            ) from exc

        self.embedding_dim = embedding_dim
        if url:
            self.client = QdrantClient(url=url, api_key=api_key or None)
        else:
            target_path = Path(path or "./data/qdrant")
            target_path.mkdir(parents=True, exist_ok=True)
            self.client = QdrantClient(path=str(target_path))

        for col in (NOTES_COLLECTION, MEMORIES_COLLECTION):
            if not self.client.collection_exists(col):
                self.client.create_collection(
                    collection_name=col,
                    vectors_config=VectorParams(size=embedding_dim, distance=Distance.COSINE),
                )

    @staticmethod
    def _point_uuid(doc_id: str) -> str:
        return str(uuid.uuid5(uuid.NAMESPACE_OID, f"trace-memory:{doc_id}"))

    def upsert(
        self,
        collection: str,
        doc_id: str,
        space_id: str,
        vector: list[float],
        metadata: dict[str, Any],
    ) -> None:
        from qdrant_client.models import PointStruct

        payload = dict(metadata)
        payload["doc_id"] = doc_id
        payload["space_id"] = space_id
        self.client.upsert(
            collection_name=collection,
            points=[
                PointStruct(
                    id=self._point_uuid(doc_id),
                    vector=vector,
                    payload=payload,
                )
            ],
        )

    def delete(self, collection: str, doc_id: str) -> None:
        from qdrant_client.models import PointIdsList

        self.client.delete(
            collection_name=collection,
            points_selector=PointIdsList(points=[self._point_uuid(doc_id)]),
        )

    def search(
        self,
        collection: str,
        space_id: str,
        query_vector: list[float],
        top_k: int = 10,
        filters: dict[str, Any] | None = None,
    ) -> list[VectorHit]:
        from qdrant_client.models import FieldCondition, Filter, MatchValue

        must_conditions = [FieldCondition(key="space_id", match=MatchValue(value=space_id))]
        if filters:
            for k, v in filters.items():
                if v is not None and v != "":
                    must_conditions.append(FieldCondition(key=k, match=MatchValue(value=v)))

        q_filter = Filter(must=must_conditions)
        if hasattr(self.client, "query_points"):
            res = self.client.query_points(
                collection_name=collection,
                query=query_vector,
                query_filter=q_filter,
                limit=top_k,
            ).points
        else:
            res = self.client.search(
                collection_name=collection,
                query_vector=query_vector,
                query_filter=q_filter,
                limit=top_k,
            )

        hits: list[VectorHit] = []
        for pt in res:
            payload = dict(pt.payload or {})
            doc_id = str(payload.get("doc_id", pt.id))
            hits.append(VectorHit(doc_id=doc_id, score=round(float(pt.score), 6), metadata=payload))
        return hits

    def count(self, collection: str, space_id: str = "") -> int:
        from qdrant_client.models import FieldCondition, Filter, MatchValue

        q_filter = None
        if space_id:
            q_filter = Filter(
                must=[FieldCondition(key="space_id", match=MatchValue(value=space_id))]
            )
        return int(self.client.count(collection_name=collection, count_filter=q_filter).count)

    def get_space_vectors(
        self,
        collection: str,
        space_id: str,
        doc_ids: set[str] | None = None,
    ) -> dict[str, tuple[list[float], float]]:
        from qdrant_client.models import FieldCondition, Filter, MatchValue

        q_filter = Filter(
            must=[FieldCondition(key="space_id", match=MatchValue(value=space_id))]
        )
        out: dict[str, tuple[list[float], float]] = {}
        offset: Any = None
        while True:
            points, next_offset = self.client.scroll(
                collection_name=collection,
                scroll_filter=q_filter,
                limit=500,
                offset=offset,
                with_payload=True,
                with_vectors=True,
            )
            for pt in points:
                payload = dict(pt.payload or {})
                did = str(payload.get("doc_id", pt.id))
                if doc_ids is not None and did not in doc_ids:
                    continue
                raw_vec = pt.vector
                if isinstance(raw_vec, list) and raw_vec and isinstance(raw_vec[0], (int, float)):
                    vec = [float(x) for x in raw_vec]
                    norm = _vector_norm(vec)
                    if norm > 1e-12:
                        out[did] = (vec, norm)
            if next_offset is None or not points:
                break
            offset = next_offset
        return out

    def close(self) -> None:
        close_fn = getattr(self.client, "close", None)
        if callable(close_fn):
            close_fn()


class EmbeddingProviderProtocol(Protocol):
    def embed(self, text: str) -> list[float]: ...


class VectorLifecycleService:
    """Synchronizes dense embeddings with Note/Memory state transitions and revocations."""

    def __init__(self, vector_store: VectorStore, embedder: EmbeddingProviderProtocol) -> None:
        self.vector_store = vector_store
        self.embedder = embedder

    def sync_note(self, note: RawNote) -> None:
        visible = note.visible_content()
        if note.is_forgotten or not visible.replace("[REDACTED]", "").strip():
            # Trigger: note forgotten or redacted empty -> physically purge vector (C07)
            self.vector_store.delete(NOTES_COLLECTION, note.note_id)
            return
        vec = self.embedder.embed(visible)
        self.vector_store.upsert(
            collection=NOTES_COLLECTION,
            doc_id=note.note_id,
            space_id=note.space_id,
            vector=vec,
            metadata={
                "revision": note.revision,
                "is_forgotten": False,
                "event_time": note.event_time,
            },
        )

    def sync_memory(self, memory: MemoryItem) -> None:
        if memory.status != "active":
            # Trigger: memory became `superseded`, `pending_review`, or `forgotten`
            # Outcome: purge from active dense index so outdated/forgotten facts never recall
            self.vector_store.delete(MEMORIES_COLLECTION, memory.memory_id)
            return
        vec = self.embedder.embed(memory.searchable_text())
        self.vector_store.upsert(
            collection=MEMORIES_COLLECTION,
            doc_id=memory.memory_id,
            space_id=memory.space_id,
            vector=vec,
            metadata={
                "memory_type": memory.memory_type,
                "status": memory.status,
                "version": memory.version,
                "assertion_kind": memory.assertion_kind,
                "permalink": memory.permalink,
            },
        )
