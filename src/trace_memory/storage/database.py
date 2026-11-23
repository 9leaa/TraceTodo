"""SQLite WAL + FTS5 database manager and CJK-aware tokenization helper."""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
import re
import sqlite3
import threading
from typing import Iterator


_CJK_CHAR_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]")
_WORD_OR_CJK_RE = re.compile(
    r"[a-zA-Z0-9_\u00c0-\u024f]+|[\u3040-\u30ff]+|[\uac00-\ud7af]+|[\u3400-\u4dbf\u4e00-\u9fff]+"
)


_CJK_SHORTHAND_MAP: tuple[tuple[str, str], ...] = (
    ("半程马拉松", "半马"),
    ("全程马拉松", "全马"),
)


def tokenize_for_fts(text: str) -> str:
    """Segment mixed CJK + ASCII text into FTS5-friendly space-separated tokens.

    Why this exists:
    SQLite's built-in `unicode61` tokenizer treats contiguous Chinese sentences as a
    single giant token, breaking partial phrase BM25 recall. Emitting ASCII words
    alongside CJK/Kana/Hangul bigrams + unigrams gives high-precision BM25 matching for both
    identifiers (`ERR_4029`, `Q1`) and East Asian phrases without external C dependencies.
    """
    tokens: list[str] = []
    for match in _WORD_OR_CJK_RE.finditer(text.lower()):
        chunk = match.group(0)
        first = chunk[0]
        if _CJK_CHAR_RE.match(first) or ("\u3040" <= first <= "\u30ff") or ("\uac00" <= first <= "\ud7af"):
            if len(chunk) == 1:
                tokens.append(chunk)
            else:
                # Bigrams for phrase precision + unigrams for recall
                for i in range(len(chunk) - 1):
                    tokens.append(chunk[i : i + 2])
                for ch in chunk:
                    tokens.append(ch)
                for full_phrase, short_alias in _CJK_SHORTHAND_MAP:
                    if full_phrase in chunk:
                        tokens.append(short_alias)
        else:
            tokens.append(chunk)
    return " ".join(tokens)


_STOP_QUERY_TERMS = frozenset(
    {
        "的", "了", "是", "在", "我", "你", "他", "她", "它", "有", "个", "吗", "呢", "啊", "吧",
        "和", "与", "及", "或", "叫", "都", "也",
        "我的", "我是", "我在", "我养", "养的", "什么", "怎么", "哪个", "哪里", "多少", "为什么",
        "名字", "叫什", "么名", "现在", "以前", "之前", "最近", "可以", "需要", "已经",
    }
)

_QUERY_STOP_SPLIT_RE = re.compile(
    r"(我的|我是|我在|我养|养的|叫什么名字|是什么|什么|怎么|哪个|哪里|多少|为什么|现在|以前|之前|最近|可以|需要|已经|[的了是在我你他她它有个吗呢啊吧和与及或叫])"
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
    # Strip stop-particles before bigramming CJK chunks so "我的猫" preserves "猫"
    cleaned = _QUERY_STOP_SPLIT_RE.sub(" ", query)
    segmented = tokenize_for_fts(cleaned)
    raw_terms = [t for t in segmented.split() if t and t not in _STOP_QUERY_TERMS]
    if not raw_terms:
        segmented = tokenize_for_fts(query)
        raw_terms = [t for t in segmented.split() if t and t not in _STOP_QUERY_TERMS]
    if not raw_terms:
        return ""
    # Prefer bigrams / multi-char words, while preserving standalone unigrams not covered inside longer tokens
    longer = [t for t in raw_terms if len(t) >= 2]
    if longer:
        extra_unigrams = [
            t
            for t in raw_terms
            if len(t) == 1 and not any(t in lt for lt in longer)
        ]
        candidates = longer + extra_unigrams
    else:
        candidates = raw_terms
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
    updated_at TEXT NOT NULL,
    note_kind TEXT NOT NULL DEFAULT 'note',
    title TEXT NOT NULL DEFAULT '',
    pinned INTEGER NOT NULL DEFAULT 0,
    tags_json TEXT NOT NULL DEFAULT '[]',
    mood TEXT NOT NULL DEFAULT '',
    journal_date TEXT NOT NULL DEFAULT '',
    extract_memory INTEGER NOT NULL DEFAULT 1
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

CREATE TABLE IF NOT EXISTS space_preferences (
    space_id TEXT PRIMARY KEY,
    theme_id TEXT NOT NULL DEFAULT 'classic',
    enabled_modules_json TEXT NOT NULL DEFAULT '{}',
    pomodoro_minutes INTEGER NOT NULL DEFAULT 25,
    extra_json TEXT NOT NULL DEFAULT '{}',
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS focus_sessions (
    session_id TEXT PRIMARY KEY,
    space_id TEXT NOT NULL,
    task_memory_id TEXT NOT NULL DEFAULT '',
    task_title TEXT NOT NULL DEFAULT '自由专注',
    mode TEXT NOT NULL DEFAULT 'pomodoro',
    planned_minutes INTEGER NOT NULL DEFAULT 25,
    duration_seconds INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'completed',
    abort_reason TEXT NOT NULL DEFAULT '',
    note TEXT NOT NULL DEFAULT '',
    started_at TEXT NOT NULL,
    ended_at TEXT NOT NULL,
    focus_date TEXT NOT NULL,
    actor_id TEXT NOT NULL DEFAULT 'user',
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_focus_sessions_space_date
    ON focus_sessions(space_id, focus_date DESC, ended_at DESC);
CREATE INDEX IF NOT EXISTS idx_focus_sessions_task
    ON focus_sessions(space_id, task_memory_id);

CREATE TABLE IF NOT EXISTS media_attachments (
    media_id TEXT PRIMARY KEY,
    space_id TEXT NOT NULL,
    note_id TEXT NOT NULL DEFAULT '',
    file_rel_path TEXT NOT NULL,
    thumb_rel_path TEXT NOT NULL DEFAULT '',
    mime_type TEXT NOT NULL DEFAULT 'image/jpeg',
    width INTEGER NOT NULL DEFAULT 0,
    height INTEGER NOT NULL DEFAULT 0,
    size_bytes INTEGER NOT NULL DEFAULT 0,
    caption TEXT NOT NULL DEFAULT '',
    ocr_text TEXT NOT NULL DEFAULT '',
    vision_status TEXT NOT NULL DEFAULT 'pending',
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_media_attachments_space_note
    ON media_attachments(space_id, note_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_media_attachments_space_time
    ON media_attachments(space_id, created_at DESC);

CREATE TABLE IF NOT EXISTS countdown_days (
    countdown_id TEXT PRIMARY KEY,
    space_id TEXT NOT NULL,
    title TEXT NOT NULL,
    kind TEXT NOT NULL DEFAULT 'countdown',
    calendar_type TEXT NOT NULL DEFAULT 'solar',
    target_date TEXT NOT NULL,
    lunar_year INTEGER NOT NULL DEFAULT 0,
    lunar_month INTEGER NOT NULL DEFAULT 0,
    lunar_day INTEGER NOT NULL DEFAULT 0,
    is_leap_month INTEGER NOT NULL DEFAULT 0,
    repeat_rule TEXT NOT NULL DEFAULT 'none',
    icon TEXT NOT NULL DEFAULT '🎯',
    color_theme TEXT NOT NULL DEFAULT 'sky',
    pinned INTEGER NOT NULL DEFAULT 0,
    note TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_countdown_days_space
    ON countdown_days(space_id, pinned DESC, target_date ASC);
"""


class Database:
    """Thread-safe SQLite WAL connection and transaction helper."""

    def __init__(self, db_path: Path) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._local = threading.local()
        self._conns_lock = threading.Lock()
        self._all_conns: set[sqlite3.Connection] = set()
        self._sp_counter = 0
        self.initialize_schema()

    def _get_conn(self) -> sqlite3.Connection:
        conn: sqlite3.Connection | None = getattr(self._local, "conn", None)
        if conn is None:
            conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
            conn.row_factory = sqlite3.Row
            # Set busy_timeout BEFORE WAL mode so lock contention during initialization waits cleanly
            conn.execute("PRAGMA busy_timeout=15000")
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            conn.execute("PRAGMA foreign_keys=ON")
            self._local.conn = conn
            with self._conns_lock:
                self._all_conns.add(conn)
        return conn

    def initialize_schema(self) -> None:
        conn = self._get_conn()
        conn.executescript(SCHEMA_SQL)
        self._ensure_column_migrations(conn)
        conn.commit()

    @staticmethod
    def _ensure_column_migrations(conn: sqlite3.Connection) -> None:
        existing_cols = {
            str(r[1]) for r in conn.execute("PRAGMA table_info(raw_notes)").fetchall()
        }
        migrations = [
            ("note_kind", "ALTER TABLE raw_notes ADD COLUMN note_kind TEXT NOT NULL DEFAULT 'note'"),
            ("title", "ALTER TABLE raw_notes ADD COLUMN title TEXT NOT NULL DEFAULT ''"),
            ("pinned", "ALTER TABLE raw_notes ADD COLUMN pinned INTEGER NOT NULL DEFAULT 0"),
            ("tags_json", "ALTER TABLE raw_notes ADD COLUMN tags_json TEXT NOT NULL DEFAULT '[]'"),
            ("mood", "ALTER TABLE raw_notes ADD COLUMN mood TEXT NOT NULL DEFAULT ''"),
            ("journal_date", "ALTER TABLE raw_notes ADD COLUMN journal_date TEXT NOT NULL DEFAULT ''"),
            ("extract_memory", "ALTER TABLE raw_notes ADD COLUMN extract_memory INTEGER NOT NULL DEFAULT 1"),
        ]
        for col_name, ddl in migrations:
            if col_name not in existing_cols:
                conn.execute(ddl)
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_raw_notes_kind_pin
                ON raw_notes(space_id, is_forgotten, note_kind, pinned DESC, updated_at DESC)
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_raw_notes_journal_date
                ON raw_notes(space_id, is_forgotten, note_kind, journal_date DESC)
            """
        )

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        conn = self._get_conn()
        if conn.in_transaction:
            # Nested savepoint
            self._sp_counter += 1
            sp_name = f"sp_nested_{self._sp_counter}"
            conn.execute(f"SAVEPOINT {sp_name}")
            try:
                yield conn
                conn.execute(f"RELEASE SAVEPOINT {sp_name}")
            except BaseException:
                conn.execute(f"ROLLBACK TO SAVEPOINT {sp_name}")
                conn.execute(f"RELEASE SAVEPOINT {sp_name}")
                raise
        else:
            conn.execute("BEGIN IMMEDIATE")
            try:
                yield conn
                conn.commit()
            except BaseException:
                conn.rollback()
                raise

    def query_all(self, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
        return self._get_conn().execute(sql, params).fetchall()

    def query_one(self, sql: str, params: tuple = ()) -> sqlite3.Row | None:
        return self._get_conn().execute(sql, params).fetchone()

    def close_thread_connection(self) -> None:
        """Close only the calling thread's SQLite connection without tearing down other threads."""
        conn = getattr(self._local, "conn", None)
        if conn is None:
            return
        self._local.conn = None
        with self._conns_lock:
            self._all_conns.discard(conn)
        try:
            conn.close()
        except sqlite3.Error:
            pass

    def close(self) -> None:
        with self._conns_lock:
            conns = list(self._all_conns)
            self._all_conns.clear()
        for conn in conns:
            try:
                conn.close()
            except sqlite3.Error:
                pass
        self._local.conn = None
