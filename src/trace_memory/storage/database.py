"""SQLite WAL + FTS5 database manager and CJK-aware tokenization helper."""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
import re
import sqlite3
import threading
from typing import Iterator


_CJK_CHAR_RE = re.compile(r"[\u4e00-\u9fff]")
_WORD_OR_CJK_RE = re.compile(r"[a-zA-Z0-9_]+|[\u4e00-\u9fff]+")


def tokenize_for_fts(text: str) -> str:
    """Segment mixed CJK + ASCII text into FTS5-friendly space-separated tokens.

    Why this exists:
    SQLite's built-in `unicode61` tokenizer treats contiguous Chinese sentences as a
    single giant token, breaking partial phrase BM25 recall. Emitting ASCII words
    alongside CJK bigrams + unigrams gives high-precision BM25 matching for both
    identifiers (`ERR_4029`, `Q1`) and Chinese phrases without external C dependencies.
    """
    tokens: list[str] = []
    for match in _WORD_OR_CJK_RE.finditer(text.lower()):
        chunk = match.group(0)
        if _CJK_CHAR_RE.match(chunk[0]):
            if len(chunk) == 1:
                tokens.append(chunk)
            else:
                # Bigrams for phrase precision + unigrams for recall
                for i in range(len(chunk) - 1):
                    tokens.append(chunk[i : i + 2])
                for ch in chunk:
                    tokens.append(ch)
        else:
            tokens.append(chunk)
    return " ".join(tokens)


_STOP_QUERY_TERMS = frozenset(
    {
        "的", "了", "是", "在", "我", "你", "他", "她", "它", "有", "个", "吗", "呢", "啊", "吧",
        "我的", "我是", "我在", "我养", "养的", "什么", "怎么", "哪个", "哪里", "多少", "为什么",
        "名字", "叫什", "么名", "现在", "以前", "之前", "最近", "可以", "需要", "已经",
    }
)

# Generic relation/intent words: kept ONLY if the query has no other specific subject terms
_GENERIC_META_TERMS = frozenset(
    {
        "喜欢", "最喜", "欢的", "我最", "偏好", "习惯", "爱好", "讨厌",
        "待办", "任务", "事情", "记录", "情况",
    }
)


def build_fts_match_query(query: str) -> str:
    """Build a safe OR-combined FTS5 MATCH expression from raw user query text."""
    segmented = tokenize_for_fts(query)
    raw_terms = [t for t in segmented.split() if t and t not in _STOP_QUERY_TERMS]
    if not raw_terms:
        return ""
    # Prefer bigrams / multi-char words when available to keep BM25 focused
    longer = [t for t in raw_terms if len(t) >= 2]
    candidates = longer if longer else raw_terms
    # Topic Compatibility Gate (inspired by suixinji Layer-3 no-answer gate):
    # If specific topic terms exist (e.g. "科幻", "电影"), drop generic meta-verbs ("喜欢")
    # so "我最喜欢的科幻电影是什么" never false-matches "喜欢喝冰美式咖啡"!
    specific = [t for t in candidates if t not in _GENERIC_META_TERMS]
    chosen = specific if specific else candidates
    # Deduplicate preserving order, cap at 24 terms
    deduped: list[str] = []
    seen: set[str] = set()
    for term in chosen:
        if term not in seen:
            seen.add(term)
            deduped.append(f'"{term}"')
        if len(deduped) >= 24:
            break
    return " OR ".join(deduped)


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS raw_notes (
    note_id TEXT PRIMARY KEY,
    space_id TEXT NOT NULL,
    content TEXT NOT NULL,
    source_type TEXT NOT NULL,
    actor_id TEXT NOT NULL,
    revision INTEGER NOT NULL DEFAULT 1,
    is_forgotten INTEGER NOT NULL DEFAULT 0,
    forgotten_spans_json TEXT NOT NULL DEFAULT '[]',
    event_time TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_raw_notes_space_time
    ON raw_notes(space_id, is_forgotten, created_at DESC);

CREATE VIRTUAL TABLE IF NOT EXISTS notes_fts USING fts5(
    note_id UNINDEXED,
    space_id UNINDEXED,
    segmented_text
);

CREATE TABLE IF NOT EXISTS memories (
    memory_id TEXT PRIMARY KEY,
    space_id TEXT NOT NULL,
    permalink TEXT NOT NULL,
    memory_type TEXT NOT NULL,
    title TEXT NOT NULL,
    summary TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    canonical_key TEXT NOT NULL DEFAULT '',
    tags_json TEXT NOT NULL DEFAULT '[]',
    assertion_kind TEXT NOT NULL DEFAULT 'stated',
    status TEXT NOT NULL DEFAULT 'active',
    version INTEGER NOT NULL DEFAULT 1,
    user_locked INTEGER NOT NULL DEFAULT 0,
    rejected_inferences_json TEXT NOT NULL DEFAULT '[]',
    evidences_json TEXT NOT NULL DEFAULT '[]',
    event_time TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(space_id, permalink)
);

CREATE INDEX IF NOT EXISTS idx_memories_space_type_status
    ON memories(space_id, memory_type, status);
CREATE INDEX IF NOT EXISTS idx_memories_space_canonical
    ON memories(space_id, canonical_key, status);

CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts USING fts5(
    memory_id UNINDEXED,
    space_id UNINDEXED,
    memory_type UNINDEXED,
    segmented_title,
    segmented_body
);

CREATE TABLE IF NOT EXISTS memory_versions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    memory_id TEXT NOT NULL,
    space_id TEXT NOT NULL,
    version INTEGER NOT NULL,
    title TEXT NOT NULL,
    summary TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    assertion_kind TEXT NOT NULL,
    status TEXT NOT NULL,
    change_reason TEXT NOT NULL,
    actor_id TEXT NOT NULL,
    origin TEXT NOT NULL,
    evidences_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(memory_id, version)
);

CREATE TABLE IF NOT EXISTS memory_evidence_links (
    memory_id TEXT NOT NULL,
    space_id TEXT NOT NULL,
    note_id TEXT NOT NULL,
    note_revision INTEGER NOT NULL,
    quote TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (memory_id, note_id, note_revision)
);

CREATE INDEX IF NOT EXISTS idx_evidence_links_note
    ON memory_evidence_links(space_id, note_id);

CREATE TABLE IF NOT EXISTS memory_relations (
    relation_id TEXT PRIMARY KEY,
    space_id TEXT NOT NULL,
    from_memory_id TEXT NOT NULL,
    relation_type TEXT NOT NULL,
    to_target_text TEXT NOT NULL,
    to_memory_id TEXT NOT NULL DEFAULT '',
    context TEXT NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_relations_from
    ON memory_relations(space_id, from_memory_id);
CREATE INDEX IF NOT EXISTS idx_relations_to
    ON memory_relations(space_id, to_memory_id);
CREATE INDEX IF NOT EXISTS idx_relations_unresolved
    ON memory_relations(space_id, to_target_text);

CREATE TABLE IF NOT EXISTS idempotency_records (
    space_id TEXT NOT NULL,
    idempotency_key TEXT NOT NULL,
    proposal_id TEXT NOT NULL,
    result_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (space_id, idempotency_key)
);

CREATE TABLE IF NOT EXISTS pending_proposals (
    proposal_id TEXT PRIMARY KEY,
    space_id TEXT NOT NULL,
    proposal_json TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    created_at TEXT NOT NULL,
    resolved_at TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS trace_runs (
    trace_id TEXT PRIMARY KEY,
    space_id TEXT NOT NULL,
    operation TEXT NOT NULL,
    input_summary TEXT NOT NULL,
    details_json TEXT NOT NULL,
    llm_calls INTEGER NOT NULL DEFAULT 0,
    tokens_used INTEGER NOT NULL DEFAULT 0,
    duration_ms REAL NOT NULL DEFAULT 0.0,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_trace_runs_space_time
    ON trace_runs(space_id, created_at DESC);

CREATE TABLE IF NOT EXISTS vector_records (
    collection TEXT NOT NULL,
    doc_id TEXT NOT NULL,
    space_id TEXT NOT NULL,
    vector_json TEXT NOT NULL,
    norm REAL NOT NULL,
    metadata_json TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (collection, doc_id)
);

CREATE INDEX IF NOT EXISTS idx_vector_records_filter
    ON vector_records(collection, space_id);

CREATE TABLE IF NOT EXISTS users (
    user_id TEXT PRIMARY KEY,
    username TEXT UNIQUE NOT NULL,
    display_name TEXT NOT NULL,
    password_hash TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'user',
    status TEXT NOT NULL DEFAULT 'active',
    space_id TEXT UNIQUE NOT NULL,
    daily_note_limit INTEGER NOT NULL DEFAULT 60,
    daily_llm_limit INTEGER NOT NULL DEFAULT 80,
    daily_token_limit INTEGER NOT NULL DEFAULT 100000,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    last_login_at TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS user_sessions (
    token_hash TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    device_label TEXT NOT NULL DEFAULT '',
    expires_at TEXT NOT NULL,
    created_at TEXT NOT NULL,
    last_used_at TEXT NOT NULL,
    revoked INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_user_sessions_user
    ON user_sessions(user_id, revoked);

CREATE TABLE IF NOT EXISTS supervision_shares (
    share_id TEXT PRIMARY KEY,
    token_hash TEXT UNIQUE NOT NULL,
    space_id TEXT NOT NULL,
    owner_user_id TEXT NOT NULL,
    label TEXT NOT NULL DEFAULT '好友监督',
    revoked INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_supervision_shares_space
    ON supervision_shares(space_id, revoked);

CREATE TABLE IF NOT EXISTS daily_usage (
    usage_date TEXT NOT NULL,
    scope_key TEXT NOT NULL,
    notes_count INTEGER NOT NULL DEFAULT 0,
    blocked_noise_count INTEGER NOT NULL DEFAULT 0,
    llm_calls INTEGER NOT NULL DEFAULT 0,
    embed_calls INTEGER NOT NULL DEFAULT 0,
    tokens_used INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (usage_date, scope_key)
);

CREATE TABLE IF NOT EXISTS system_settings (
    key TEXT PRIMARY KEY,
    value_json TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS security_audit_logs (
    log_id TEXT PRIMARY KEY,
    actor_user_id TEXT NOT NULL DEFAULT '',
    space_id TEXT NOT NULL DEFAULT '',
    action TEXT NOT NULL,
    status TEXT NOT NULL,
    detail_json TEXT NOT NULL DEFAULT '{}',
    client_ip TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_security_audit_created
    ON security_audit_logs(created_at DESC);

CREATE TABLE IF NOT EXISTS ingest_jobs (
    job_id TEXT PRIMARY KEY,
    space_id TEXT NOT NULL,
    note_id TEXT NOT NULL,
    actor_id TEXT NOT NULL DEFAULT 'user',
    idempotency_key TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'queued',
    attempts INTEGER NOT NULL DEFAULT 0,
    error_message TEXT NOT NULL DEFAULT '',
    result_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_ingest_jobs_status_space
    ON ingest_jobs(status, space_id, created_at ASC);

CREATE INDEX IF NOT EXISTS idx_ingest_jobs_note
    ON ingest_jobs(space_id, note_id);

CREATE TABLE IF NOT EXISTS space_sync_state (
    space_id TEXT PRIMARY KEY,
    sync_version INTEGER NOT NULL DEFAULT 1,
    last_reason TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL
);
"""


class Database:
    """Thread-safe SQLite WAL connection and transaction helper."""

    def __init__(self, db_path: Path) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._local = threading.local()
        self.initialize_schema()

    def _get_conn(self) -> sqlite3.Connection:
        conn: sqlite3.Connection | None = getattr(self._local, "conn", None)
        if conn is None:
            conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
            conn.row_factory = sqlite3.Row
            # SQLite requires WAL mode for concurrent read/write access across Web/MCP/CLI
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            conn.execute("PRAGMA foreign_keys=ON")
            conn.execute("PRAGMA busy_timeout=15000")
            self._local.conn = conn
        return conn

    def initialize_schema(self) -> None:
        conn = self._get_conn()
        conn.executescript(SCHEMA_SQL)
        conn.commit()

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        conn = self._get_conn()
        if conn.in_transaction:
            # Nested savepoint
            sp_name = "sp_nested"
            conn.execute(f"SAVEPOINT {sp_name}")
            try:
                yield conn
                conn.execute(f"RELEASE SAVEPOINT {sp_name}")
            except Exception:
                conn.execute(f"ROLLBACK TO SAVEPOINT {sp_name}")
                conn.execute(f"RELEASE SAVEPOINT {sp_name}")
                raise
        else:
            conn.execute("BEGIN IMMEDIATE")
            try:
                yield conn
                conn.commit()
            except Exception:
                conn.rollback()
                raise

    def query_all(self, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
        return self._get_conn().execute(sql, params).fetchall()

    def query_one(self, sql: str, params: tuple = ()) -> sqlite3.Row | None:
        return self._get_conn().execute(sql, params).fetchone()

    def close(self) -> None:
        conn: sqlite3.Connection | None = getattr(self._local, "conn", None)
        if conn is not None:
            conn.close()
            self._local.conn = None
