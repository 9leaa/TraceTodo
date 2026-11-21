"""Relational, FTS5, Relation, Idempotency, and Trace repositories."""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from trace_memory.domain.memories import MemoryEvidence, MemoryItem, MemoryVersion
from trace_memory.domain.notes import RawNote, new_id, utc_now_iso
from trace_memory.domain.proposals import VersionConflictError
from trace_memory.domain.relations import MemoryRelation, parse_memory_uri
from trace_memory.storage.database import Database, build_fts_match_query, tokenize_for_fts


def _row_to_note(row: sqlite3.Row) -> RawNote:
    keys = row.keys()
    tags_raw = json.loads(row["tags_json"] or "[]") if "tags_json" in keys else []
    return RawNote(
        note_id=row["note_id"],
        space_id=row["space_id"],
        content=row["content"],
        source_type=row["source_type"],
        actor_id=row["actor_id"],
        revision=int(row["revision"]),
        is_forgotten=bool(row["is_forgotten"]),
        forgotten_spans=tuple(json.loads(row["forgotten_spans_json"])),
        event_time=row["event_time"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        note_kind=str(row["note_kind"] or "note") if "note_kind" in keys else "note",
        title=str(row["title"] or "") if "title" in keys else "",
        pinned=bool(row["pinned"]) if "pinned" in keys else False,
        tags=tuple(str(t) for t in tags_raw if str(t).strip()),
        mood=str(row["mood"] or "") if "mood" in keys else "",
        journal_date=str(row["journal_date"] or "") if "journal_date" in keys else "",
        extract_memory=bool(row["extract_memory"]) if "extract_memory" in keys else True,
    )


def _row_to_memory(row: sqlite3.Row) -> MemoryItem:
    evidences_raw = json.loads(row["evidences_json"])
    evidences = tuple(
        MemoryEvidence(
            note_id=e["note_id"],
            note_revision=int(e["note_revision"]),
            quote=e.get("quote", ""),
        )
        for e in evidences_raw
    )
    return MemoryItem(
        memory_id=row["memory_id"],
        space_id=row["space_id"],
        permalink=row["permalink"],
        memory_type=row["memory_type"],
        title=row["title"],
        summary=row["summary"],
        payload=json.loads(row["payload_json"]),
        canonical_key=row["canonical_key"],
        tags=tuple(json.loads(row["tags_json"])),
        assertion_kind=row["assertion_kind"],
        status=row["status"],
        version=int(row["version"]),
        user_locked=bool(row["user_locked"]),
        rejected_inferences=tuple(json.loads(row["rejected_inferences_json"])),
        evidences=evidences,
        event_time=row["event_time"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


class NoteRepository:
    """Persistence and FTS5 indexing for ground-truth `RawNote` records."""

    def __init__(self, db: Database) -> None:
        self.db = db

    def save(self, note: RawNote) -> RawNote:
        with self.db.transaction() as conn:
            conn.execute(
                """
                INSERT INTO raw_notes (
                    note_id, space_id, content, source_type, actor_id,
                    revision, is_forgotten, forgotten_spans_json,
                    event_time, created_at, updated_at,
                    note_kind, title, pinned, tags_json, mood, journal_date, extract_memory
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(note_id) DO UPDATE SET
                    content = excluded.content,
                    revision = excluded.revision,
                    is_forgotten = excluded.is_forgotten,
                    forgotten_spans_json = excluded.forgotten_spans_json,
                    event_time = excluded.event_time,
                    updated_at = excluded.updated_at,
                    note_kind = excluded.note_kind,
                    title = excluded.title,
                    pinned = excluded.pinned,
                    tags_json = excluded.tags_json,
                    mood = excluded.mood,
                    journal_date = excluded.journal_date,
                    extract_memory = excluded.extract_memory
                """,
                (
                    note.note_id,
                    note.space_id,
                    note.content,
                    note.source_type,
                    note.actor_id,
                    note.revision,
                    int(note.is_forgotten),
                    json.dumps(list(note.forgotten_spans), ensure_ascii=False),
                    note.event_time,
                    note.created_at,
                    note.updated_at,
                    note.note_kind,
                    note.title,
                    int(note.pinned),
                    json.dumps(list(note.tags), ensure_ascii=False),
                    note.mood,
                    note.journal_date,
                    int(note.extract_memory),
                ),
            )
            self._refresh_fts(conn, note)
        return note

    def _refresh_fts(self, conn: sqlite3.Connection, note: RawNote) -> None:
        conn.execute("DELETE FROM notes_fts WHERE note_id = ?", (note.note_id,))
        visible = note.visible_content()
        combined = " ".join(part for part in (note.title, " ".join(note.tags), visible) if part).strip()
        if not note.is_forgotten and combined:
            conn.execute(
                "INSERT INTO notes_fts (note_id, space_id, segmented_text) VALUES (?, ?, ?)",
                (note.note_id, note.space_id, tokenize_for_fts(combined)),
            )

    def get(self, space_id: str, note_id: str, include_forgotten: bool = False) -> RawNote | None:
        row = self.db.query_one(
            "SELECT * FROM raw_notes WHERE note_id = ? AND space_id = ?",
            (note_id, space_id),
        )
        if row is None:
            return None
        note = _row_to_note(row)
        if note.is_forgotten and not include_forgotten:
            return None
        return note

    def get_any_space(self, note_id: str) -> RawNote | None:
        row = self.db.query_one("SELECT * FROM raw_notes WHERE note_id = ?", (note_id,))
        return _row_to_note(row) if row else None

    def list_notes(
        self, space_id: str, limit: int = 50, include_forgotten: bool = False
    ) -> list[RawNote]:
        if include_forgotten:
            rows = self.db.query_all(
                "SELECT * FROM raw_notes WHERE space_id = ? ORDER BY created_at DESC LIMIT ?",
                (space_id, limit),
            )
        else:
            rows = self.db.query_all(
                "SELECT * FROM raw_notes WHERE space_id = ? AND is_forgotten = 0 ORDER BY created_at DESC LIMIT ?",
                (space_id, limit),
            )
        return [_row_to_note(r) for r in rows]

    def search_bm25(self, space_id: str, query: str, limit: int = 10) -> list[tuple[RawNote, float]]:
        match_expr = build_fts_match_query(query)
        if not match_expr:
            return []
        rows = self.db.query_all(
            """
            SELECT n.*, bm25(notes_fts) AS raw_bm25
            FROM notes_fts
            JOIN raw_notes n ON n.note_id = notes_fts.note_id
            WHERE notes_fts.segmented_text MATCH ?
              AND n.space_id = ?
              AND n.is_forgotten = 0
            ORDER BY raw_bm25 ASC
            LIMIT ?
            """,
            (match_expr, space_id, limit),
        )
        # SQLite bm25() returns smaller (more negative) values for better matches
        return [(_row_to_note(r), float(-r["raw_bm25"])) for r in rows]


class MemoryRepository:
    """Persistence, CAS version enforcement, audit history, and FTS5 indexing for `MemoryItem`."""

    def __init__(self, db: Database) -> None:
        self.db = db

    def ensure_unique_permalink(
        self, space_id: str, desired_permalink: str, exclude_memory_id: str = ""
    ) -> str:
        base = desired_permalink.strip("/")
        candidate = base
        suffix = 2
        while True:
            row = self.db.query_one(
                "SELECT memory_id FROM memories WHERE space_id = ? AND permalink = ?",
                (space_id, candidate),
            )
            if row is None or row["memory_id"] == exclude_memory_id:
                return candidate
            candidate = f"{base}-{suffix}"
            suffix += 1

    def create(
        self,
        item: MemoryItem,
        change_reason: str,
        actor_id: str,
        origin: str,
    ) -> MemoryItem:
        unique_permalink = self.ensure_unique_permalink(item.space_id, item.permalink)
        if unique_permalink != item.permalink:
            item = MemoryItem(
                memory_id=item.memory_id,
                space_id=item.space_id,
                permalink=unique_permalink,
                memory_type=item.memory_type,
                title=item.title,
                summary=item.summary,
                payload=item.payload,
                canonical_key=item.canonical_key,
                tags=item.tags,
                assertion_kind=item.assertion_kind,
                status=item.status,
                version=1,
                user_locked=item.user_locked,
                rejected_inferences=item.rejected_inferences,
                evidences=item.evidences,
                event_time=item.event_time,
                created_at=item.created_at,
                updated_at=item.updated_at,
            )

        evidences_json = json.dumps([e.to_dict() for e in item.evidences], ensure_ascii=False)
        payload_json = json.dumps(item.payload, ensure_ascii=False)

        with self.db.transaction() as conn:
            conn.execute(
                """
                INSERT INTO memories (
                    memory_id, space_id, permalink, memory_type, title, summary,
                    payload_json, canonical_key, tags_json, assertion_kind, status,
                    version, user_locked, rejected_inferences_json, evidences_json,
                    event_time, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    item.memory_id,
                    item.space_id,
                    item.permalink,
                    item.memory_type,
                    item.title,
                    item.summary,
                    payload_json,
                    item.canonical_key,
                    json.dumps(list(item.tags), ensure_ascii=False),
                    item.assertion_kind,
                    item.status,
                    item.version,
                    int(item.user_locked),
                    json.dumps(list(item.rejected_inferences), ensure_ascii=False),
                    evidences_json,
                    item.event_time,
                    item.created_at,
                    item.updated_at,
                ),
            )
            self._write_version_and_links(
                conn, item, payload_json, evidences_json, change_reason, actor_id, origin
            )
            self._refresh_fts(conn, item)
        return item

    def update_cas(
        self,
        item: MemoryItem,
        expected_version: int,
        change_reason: str,
        actor_id: str,
        origin: str,
    ) -> MemoryItem:
        """Atomically update `item` only when database `version == expected_version`."""
        next_version = expected_version + 1
        updated_item = MemoryItem(
            memory_id=item.memory_id,
            space_id=item.space_id,
            permalink=self.ensure_unique_permalink(
                item.space_id, item.permalink, exclude_memory_id=item.memory_id
            ),
            memory_type=item.memory_type,
            title=item.title,
            summary=item.summary,
            payload=item.payload,
            canonical_key=item.canonical_key,
            tags=item.tags,
            assertion_kind=item.assertion_kind,
            status=item.status,
            version=next_version,
            user_locked=item.user_locked,
            rejected_inferences=item.rejected_inferences,
            evidences=item.evidences,
            event_time=item.event_time,
            created_at=item.created_at,
            updated_at=utc_now_iso(),
        )
        evidences_json = json.dumps(
            [e.to_dict() for e in updated_item.evidences], ensure_ascii=False
        )
        payload_json = json.dumps(updated_item.payload, ensure_ascii=False)

        with self.db.transaction() as conn:
            cursor = conn.execute(
                """
                UPDATE memories SET
                    permalink = ?,
                    title = ?,
                    summary = ?,
                    payload_json = ?,
                    canonical_key = ?,
                    tags_json = ?,
                    assertion_kind = ?,
                    status = ?,
                    version = ?,
                    user_locked = ?,
                    rejected_inferences_json = ?,
                    evidences_json = ?,
                    event_time = ?,
                    updated_at = ?
                WHERE memory_id = ? AND space_id = ? AND version = ?
                """,
                (
                    updated_item.permalink,
                    updated_item.title,
                    updated_item.summary,
                    payload_json,
                    updated_item.canonical_key,
                    json.dumps(list(updated_item.tags), ensure_ascii=False),
                    updated_item.assertion_kind,
                    updated_item.status,
                    updated_item.version,
                    int(updated_item.user_locked),
                    json.dumps(list(updated_item.rejected_inferences), ensure_ascii=False),
                    evidences_json,
                    updated_item.event_time,
                    updated_item.updated_at,
                    updated_item.memory_id,
                    updated_item.space_id,
                    expected_version,
                ),
            )
            if cursor.rowcount != 1:
                actual_row = conn.execute(
                    "SELECT version FROM memories WHERE memory_id = ? AND space_id = ?",
                    (updated_item.memory_id, updated_item.space_id),
                ).fetchone()
                actual_ver = int(actual_row["version"]) if actual_row else -1
                raise VersionConflictError(updated_item.memory_id, expected_version, actual_ver)

            self._write_version_and_links(
                conn, updated_item, payload_json, evidences_json, change_reason, actor_id, origin
            )
            self._refresh_fts(conn, updated_item)
        return updated_item

    def _write_version_and_links(
        self,
        conn: sqlite3.Connection,
        item: MemoryItem,
        payload_json: str,
        evidences_json: str,
        change_reason: str,
        actor_id: str,
        origin: str,
    ) -> None:
        conn.execute(
            """
            INSERT INTO memory_versions (
                memory_id, space_id, version, title, summary, payload_json,
                assertion_kind, status, change_reason, actor_id, origin,
                evidences_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                item.memory_id,
                item.space_id,
                item.version,
                item.title,
                item.summary,
                payload_json,
                item.assertion_kind,
                item.status,
                change_reason,
                actor_id,
                origin,
                evidences_json,
                item.updated_at,
            ),
        )
        conn.execute(
            "DELETE FROM memory_evidence_links WHERE memory_id = ?", (item.memory_id,)
        )
        for ev in item.evidences:
            conn.execute(
                """
                INSERT OR REPLACE INTO memory_evidence_links
                    (memory_id, space_id, note_id, note_revision, quote)
                VALUES (?, ?, ?, ?, ?)
                """,
                (item.memory_id, item.space_id, ev.note_id, ev.note_revision, ev.quote),
            )

    def _refresh_fts(self, conn: sqlite3.Connection, item: MemoryItem) -> None:
        conn.execute("DELETE FROM memories_fts WHERE memory_id = ?", (item.memory_id,))
        if item.status == "active":
            conn.execute(
                """
                INSERT INTO memories_fts
                    (memory_id, space_id, memory_type, segmented_title, segmented_body)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    item.memory_id,
                    item.space_id,
                    item.memory_type,
                    tokenize_for_fts(item.title),
                    tokenize_for_fts(item.searchable_text()),
                ),
            )

    def get(
        self, space_id: str, identifier: str, include_forgotten: bool = False
    ) -> MemoryItem | None:
        """Resolve a MemoryItem by `memory_id`, `memory://permalink`, `permalink`, or exact `title`."""
        cleaned = parse_memory_uri(identifier)
        row = self.db.query_one(
            """
            SELECT * FROM memories
            WHERE space_id = ? AND (memory_id = ? OR permalink = ? OR title = ?)
            ORDER BY CASE
                WHEN memory_id = ? THEN 0
                WHEN permalink = ? THEN 1
                ELSE 2
            END, version DESC
            LIMIT 1
            """,
            (space_id, cleaned, cleaned, cleaned, cleaned, cleaned),
        )
        if row is None:
            return None
        item = _row_to_memory(row)
        if item.status == "forgotten" and not include_forgotten:
            return None
        return item

    def get_any_space(self, memory_id: str) -> MemoryItem | None:
        row = self.db.query_one(
            "SELECT * FROM memories WHERE memory_id = ?", (memory_id,)
        )
        return _row_to_memory(row) if row else None

    def find_by_canonical_key(
        self, space_id: str, canonical_key: str, statuses: tuple[str, ...] = ("active", "pending_review")
    ) -> MemoryItem | None:
        if not canonical_key:
            return None
        placeholders = ",".join("?" for _ in statuses)
        row = self.db.query_one(
            f"""
            SELECT * FROM memories
            WHERE space_id = ? AND canonical_key = ? AND status IN ({placeholders})
            ORDER BY updated_at DESC LIMIT 1
            """,
            (space_id, canonical_key, *statuses),
        )
        return _row_to_memory(row) if row else None

    def find_dependent_memories(self, space_id: str, note_id: str) -> list[MemoryItem]:
        rows = self.db.query_all(
            """
            SELECT DISTINCT m.*
            FROM memory_evidence_links l
            JOIN memories m ON m.memory_id = l.memory_id
            WHERE l.space_id = ? AND l.note_id = ? AND m.status != 'forgotten'
            """,
            (space_id, note_id),
        )
        return [_row_to_memory(r) for r in rows]

    def list_memories(
        self,
        space_id: str,
        memory_type: str = "",
        status: str = "active",
        limit: int = 200,
    ) -> list[MemoryItem]:
        clauses = ["space_id = ?"]
        params: list[Any] = [space_id]
        if memory_type:
            clauses.append("memory_type = ?")
            params.append(memory_type)
        if status and status != "all":
            clauses.append("status = ?")
            params.append(status)
        else:
            clauses.append("status != 'forgotten'")
        params.append(limit)
        sql = f"SELECT * FROM memories WHERE {' AND '.join(clauses)} ORDER BY updated_at DESC LIMIT ?"
        return [_row_to_memory(r) for r in self.db.query_all(sql, tuple(params))]

    def page_active_memories(
        self, space_id: str, memory_type: str, limit: int, offset: int
    ) -> tuple[list[MemoryItem], int]:
        type_clause = "AND memory_type = ?" if memory_type else ""
        filters = (space_id, memory_type) if memory_type else (space_id,)
        rows = self.db.query_all(
            f"""
            WITH matching AS (
                SELECT memory_id, updated_at FROM memories
                WHERE space_id = ? AND status = 'active' {type_clause}
            ),
            page AS (
                SELECT memory_id, updated_at FROM matching
                ORDER BY updated_at DESC, memory_id DESC LIMIT ? OFFSET ?
            )
            SELECT memories.*, totals.total
            FROM (SELECT COUNT(*) AS total FROM matching) AS totals
            LEFT JOIN page ON TRUE
            LEFT JOIN memories ON memories.memory_id = page.memory_id
            ORDER BY page.updated_at DESC, page.memory_id DESC
            """,
            (*filters, limit, offset),
        )
        return [_row_to_memory(row) for row in rows if row["memory_id"] is not None], rows[0]["total"]

    def scrub_forgotten_versions(self, space_id: str, memory_id: str) -> None:
        """Erase plaintext content from all historical versions of a forgotten memory while preserving audit rows."""
        with self.db.transaction() as conn:
            conn.execute(
                """
                UPDATE memory_versions
                SET title = '[FORGOTTEN]',
                    summary = '[FORGOTTEN]',
                    payload_json = '{}',
                    evidences_json = '[]'
                WHERE space_id = ? AND memory_id = ?
                """,
                (space_id, memory_id),
            )

    def get_history(self, space_id: str, memory_id: str) -> list[MemoryVersion]:
        rows = self.db.query_all(
            """
            SELECT * FROM memory_versions
            WHERE space_id = ? AND memory_id = ?
            ORDER BY version ASC
            """,
            (space_id, memory_id),
        )
        history: list[MemoryVersion] = []
        for r in rows:
            evs = tuple(
                MemoryEvidence(
                    note_id=e["note_id"],
                    note_revision=int(e["note_revision"]),
                    quote=e.get("quote", ""),
                )
                for e in json.loads(r["evidences_json"])
            )
            history.append(
                MemoryVersion(
                    memory_id=r["memory_id"],
                    version=int(r["version"]),
                    title=r["title"],
                    summary=r["summary"],
                    payload=json.loads(r["payload_json"]),
                    assertion_kind=r["assertion_kind"],
                    status=r["status"],
                    change_reason=r["change_reason"],
                    actor_id=r["actor_id"],
                    origin=r["origin"],
                    evidences=evs,
                    created_at=r["created_at"],
                )
            )
        return history

    def search_bm25(
        self, space_id: str, query: str, memory_type: str = "", limit: int = 15
    ) -> list[tuple[MemoryItem, float]]:
        match_expr = build_fts_match_query(query)
        if not match_expr:
            return []
        params: list[Any] = [match_expr, space_id]
        type_clause = ""
        if memory_type:
            type_clause = "AND m.memory_type = ?"
            params.append(memory_type)
        params.append(limit)
        rows = self.db.query_all(
            f"""
            SELECT m.*, bm25(memories_fts, 0.0, 0.0, 0.0, 2.5, 1.0) AS raw_bm25
            FROM memories_fts
            JOIN memories m ON m.memory_id = memories_fts.memory_id
            WHERE memories_fts MATCH ?
              AND m.space_id = ?
              AND m.status = 'active'
              {type_clause}
            ORDER BY raw_bm25 ASC
            LIMIT ?
            """,
            tuple(params),
        )
        return [(_row_to_memory(r), float(-r["raw_bm25"])) for r in rows]


class RelationRepository:
    """Graph edges with Basic Memory forward-reference resolution and unresolution."""

    def __init__(self, db: Database, memory_repo: MemoryRepository) -> None:
        self.db = db
        self.memory_repo = memory_repo

    def replace_outgoing(
        self,
        space_id: str,
        from_memory_id: str,
        specs: list[tuple[str, str, str]],
    ) -> list[MemoryRelation]:
        """Replace all outgoing edges owned by `from_memory_id` and resolve existing targets."""
        relations: list[MemoryRelation] = []
        with self.db.transaction() as conn:
            conn.execute(
                "DELETE FROM memory_relations WHERE space_id = ? AND from_memory_id = ?",
                (space_id, from_memory_id),
            )
            for rel_type, target_text, context in specs:
                cleaned_target = parse_memory_uri(target_text)
                if not cleaned_target:
                    continue
                target_item = self.memory_repo.get(space_id, cleaned_target)
                to_id = (
                    target_item.memory_id
                    if target_item and target_item.status == "active"
                    else ""
                )
                if to_id == from_memory_id:
                    continue  # skip self-loops
                rel = MemoryRelation(
                    relation_id=new_id("rel"),
                    space_id=space_id,
                    from_memory_id=from_memory_id,
                    relation_type=rel_type,
                    to_target_text=cleaned_target,
                    to_memory_id=to_id,
                    context=context,
                )
                conn.execute(
                    """
                    INSERT INTO memory_relations
                        (relation_id, space_id, from_memory_id, relation_type, to_target_text, to_memory_id, context)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        rel.relation_id,
                        rel.space_id,
                        rel.from_memory_id,
                        rel.relation_type,
                        rel.to_target_text,
                        rel.to_memory_id,
                        rel.context,
                    ),
                )
                relations.append(rel)
        return relations

    def resolve_pending_inbound(self, memory: MemoryItem) -> int:
        """Link any unresolved forward references pointing to `memory`'s permalink or title."""
        if memory.status != "active":
            return 0
        with self.db.transaction() as conn:
            cur = conn.execute(
                """
                UPDATE memory_relations
                SET to_memory_id = ?
                WHERE space_id = ?
                  AND to_memory_id = ''
                  AND from_memory_id != ?
                  AND (LOWER(to_target_text) = LOWER(?) OR LOWER(to_target_text) = LOWER(?))
                """,
                (
                    memory.memory_id,
                    memory.space_id,
                    memory.memory_id,
                    memory.permalink,
                    memory.title,
                ),
            )
            return int(cur.rowcount)

    def clear_inbound_on_inactive(
        self,
        space_id: str,
        target_memory_id: str,
        *,
        redact_target_text: bool = False,
    ) -> None:
        """Clear `to_memory_id` on inbound edges when target becomes inactive; optionally redact forgotten target labels."""
        with self.db.transaction() as conn:
            if redact_target_text:
                conn.execute(
                    """
                    UPDATE memory_relations
                    SET to_memory_id = '',
                        to_target_text = '[FORGOTTEN]',
                        context = ''
                    WHERE space_id = ? AND to_memory_id = ?
                    """,
                    (space_id, target_memory_id),
                )
            else:
                conn.execute(
                    """
                    UPDATE memory_relations
                    SET to_memory_id = ''
                    WHERE space_id = ? AND to_memory_id = ?
                    """,
                    (space_id, target_memory_id),
                )
            conn.execute(
                "DELETE FROM memory_relations WHERE space_id = ? AND from_memory_id = ?",
                (space_id, target_memory_id),
            )

    def get_neighbors(self, space_id: str, memory_id: str) -> list[MemoryRelation]:
        rows = self.db.query_all(
            """
            SELECT * FROM memory_relations
            WHERE space_id = ? AND (from_memory_id = ? OR to_memory_id = ?)
            """,
            (space_id, memory_id, memory_id),
        )
        return [
            MemoryRelation(
                relation_id=r["relation_id"],
                space_id=r["space_id"],
                from_memory_id=r["from_memory_id"],
                relation_type=r["relation_type"],
                to_target_text=r["to_target_text"],
                to_memory_id=r["to_memory_id"],
                context=r["context"],
            )
            for r in rows
        ]

    def list_all(self, space_id: str) -> list[MemoryRelation]:
        rows = self.db.query_all(
            "SELECT * FROM memory_relations WHERE space_id = ?", (space_id,)
        )
        return [
            MemoryRelation(
                relation_id=r["relation_id"],
                space_id=r["space_id"],
                from_memory_id=r["from_memory_id"],
                relation_type=r["relation_type"],
                to_target_text=r["to_target_text"],
                to_memory_id=r["to_memory_id"],
                context=r["context"],
            )
            for r in rows
        ]


class TraceRepository:
    """Stores idempotency keys and full-pipeline observability traces."""

    def __init__(self, db: Database) -> None:
        self.db = db

    def get_idempotent_result(self, space_id: str, key: str) -> dict[str, Any] | None:
        if not key:
            return None
        row = self.db.query_one(
            "SELECT result_json FROM idempotency_records WHERE space_id = ? AND idempotency_key = ?",
            (space_id, key),
        )
        return json.loads(row["result_json"]) if row else None

    def save_idempotent_result(
        self, space_id: str, key: str, proposal_id: str, result_dict: dict[str, Any]
    ) -> None:
        if not key:
            return
        with self.db.transaction() as conn:
            conn.execute(
                """
                INSERT OR IGNORE INTO idempotency_records
                    (space_id, idempotency_key, proposal_id, result_json, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    space_id,
                    key,
                    proposal_id,
                    json.dumps(result_dict, ensure_ascii=False),
                    utc_now_iso(),
                ),
            )

    def record_trace(
        self,
        space_id: str,
        operation: str,
        input_summary: str,
        details: dict[str, Any],
        llm_calls: int = 0,
        tokens_used: int = 0,
        duration_ms: float = 0.0,
    ) -> str:
        trace_id = new_id("trc")
        with self.db.transaction() as conn:
            conn.execute(
                """
                INSERT INTO trace_runs (
                    trace_id, space_id, operation, input_summary,
                    details_json, llm_calls, tokens_used, duration_ms, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    trace_id,
                    space_id,
                    operation,
                    input_summary[:240],
                    json.dumps(details, ensure_ascii=False),
                    llm_calls,
                    tokens_used,
                    round(duration_ms, 2),
                    utc_now_iso(),
                ),
            )
        return trace_id

    def list_traces(self, space_id: str, limit: int = 30) -> list[dict[str, Any]]:
        rows = self.db.query_all(
            "SELECT * FROM trace_runs WHERE space_id = ? ORDER BY created_at DESC LIMIT ?",
            (space_id, limit),
        )
        return [
            {
                "trace_id": r["trace_id"],
                "space_id": r["space_id"],
                "operation": r["operation"],
                "input_summary": r["input_summary"],
                "details": json.loads(r["details_json"]),
                "llm_calls": int(r["llm_calls"]),
                "tokens_used": int(r["tokens_used"]),
                "duration_ms": float(r["duration_ms"]),
                "created_at": r["created_at"],
            }
            for r in rows
        ]

    def redact_for_forgotten(
        self,
        space_id: str,
        *,
        memory_id: str = "",
        note_ids: tuple[str, ...] = (),
    ) -> int:
        """Scrub trace summaries/details and purge idempotency caches referencing forgotten memory or note IDs."""
        tokens = [t for t in (memory_id, *note_ids) if t and t.strip()]
        if not tokens:
            return 0
        redacted_count = 0
        with self.db.transaction() as conn:
            for tok in tokens:
                pat = f"%{tok}%"
                cur = conn.execute(
                    """
                    UPDATE trace_runs
                    SET input_summary = '[FORGOTTEN]',
                        details_json = '{"redacted": true}'
                    WHERE space_id = ? AND (details_json LIKE ? OR input_summary LIKE ?)
                    """,
                    (space_id, pat, pat),
                )
                redacted_count += int(cur.rowcount or 0)
                conn.execute(
                    """
                    DELETE FROM idempotency_records
                    WHERE space_id = ? AND result_json LIKE ?
                    """,
                    (space_id, pat),
                )
        return redacted_count
