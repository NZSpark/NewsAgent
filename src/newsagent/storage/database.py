"""SQLite schema + connection management (TASK-009)."""
from __future__ import annotations

import sqlite3
from pathlib import Path

from ..config import get_config
from ..logging_setup import StorageError, get_logger

log = get_logger("storage.database")

SCHEMA = """
CREATE TABLE IF NOT EXISTS sources (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    url TEXT NOT NULL,
    category TEXT,
    language TEXT,
    enabled INTEGER DEFAULT 1,
    priority INTEGER DEFAULT 5,
    crawl_interval INTEGER DEFAULT 60,
    parser_type TEXT,
    last_success_at TEXT,
    last_error_at TEXT,
    last_seen_published_at TEXT,
    failure_count INTEGER DEFAULT 0,
    etag TEXT,
    last_modified TEXT
);

CREATE TABLE IF NOT EXISTS articles (
    id TEXT PRIMARY KEY,
    source_id TEXT NOT NULL,
    url TEXT NOT NULL,
    canonical_url TEXT NOT NULL,
    title TEXT NOT NULL,
    published_at TEXT,
    author TEXT,
    content TEXT,
    language TEXT,
    content_hash TEXT NOT NULL,
    retrieved_at TEXT NOT NULL,
    first_seen_at TEXT NOT NULL,
    is_ai_related INTEGER,
    topics TEXT,
    entities TEXT,
    summary TEXT,
    importance REAL,
    confidence REAL
);

CREATE INDEX IF NOT EXISTS idx_articles_canonical ON articles(canonical_url);
CREATE INDEX IF NOT EXISTS idx_articles_hash ON articles(content_hash);
CREATE INDEX IF NOT EXISTS idx_articles_published ON articles(published_at);
CREATE INDEX IF NOT EXISTS idx_articles_source ON articles(source_id);

CREATE TABLE IF NOT EXISTS events (
    event_id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    first_seen_at TEXT,
    last_seen_at TEXT,
    published_at TEXT,
    importance_score REAL DEFAULT 0,
    novelty_score REAL DEFAULT 0,
    confidence_score REAL DEFAULT 0,
    freshness_score REAL DEFAULT 0,
    source_score REAL DEFAULT 0,
    base_score REAL DEFAULT 0,
    category TEXT,
    entities TEXT,
    summary TEXT,
    why_it_matters TEXT,
    status TEXT DEFAULT 'active'
);

CREATE INDEX IF NOT EXISTS idx_events_published ON events(published_at);
CREATE INDEX IF NOT EXISTS idx_events_score ON events(base_score);

CREATE TABLE IF NOT EXISTS event_articles (
    event_id TEXT NOT NULL,
    article_id TEXT NOT NULL,
    relation_type TEXT DEFAULT 'independent_report',
    PRIMARY KEY (event_id, article_id)
);

CREATE TABLE IF NOT EXISTS claims (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id TEXT NOT NULL,
    claim TEXT NOT NULL,
    evidence_type TEXT NOT NULL,
    source_urls TEXT
);

CREATE INDEX IF NOT EXISTS idx_claims_event ON claims(event_id);

CREATE TABLE IF NOT EXISTS llm_runs (
    run_id TEXT PRIMARY KEY,
    task_type TEXT,
    provider TEXT,
    model TEXT,
    prompt_version TEXT,
    started_at TEXT,
    finished_at TEXT,
    input_tokens INTEGER,
    output_tokens INTEGER,
    latency_ms INTEGER,
    success INTEGER,
    error_type TEXT,
    fallback_from TEXT
);

CREATE INDEX IF NOT EXISTS idx_llm_runs_task ON llm_runs(task_type);

CREATE TABLE IF NOT EXISTS pipeline_runs (
    run_id TEXT PRIMARY KEY,
    started_at TEXT,
    finished_at TEXT,
    status TEXT,
    sources_total INTEGER,
    sources_success INTEGER,
    articles_seen INTEGER,
    articles_new INTEGER,
    errors TEXT
);

CREATE TABLE IF NOT EXISTS embeddings (
    item_type TEXT NOT NULL,
    item_id TEXT NOT NULL,
    dim INTEGER NOT NULL,
    vector TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (item_type, item_id)
);

CREATE INDEX IF NOT EXISTS idx_embeddings_type ON embeddings(item_type);
"""


def connect(db_path: Path | None = None) -> sqlite3.Connection:
    cfg = get_config()
    path = db_path or cfg.db_path
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        conn = sqlite3.connect(str(path))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn
    except sqlite3.Error as exc:
        raise StorageError(f"cannot open database {path}: {exc}") from exc


def init_db(conn: sqlite3.Connection) -> None:
    """Idempotent schema creation."""
    try:
        conn.executescript(SCHEMA)
        conn.commit()
    except sqlite3.Error as exc:
        raise StorageError(f"schema init failed: {exc}") from exc
    log.debug("database schema ready")
