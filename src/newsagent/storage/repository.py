"""Article / Event / Source repositories (TASK-010, TASK-013)."""
from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone

from ..logging_setup import StorageError, get_logger
from ..sources.models import Article, Source
from .models import Claim, Event, PipelineRun

log = get_logger("storage.repository")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class SourceRepository:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def upsert(self, source: Source) -> None:
        self.conn.execute(
            """
            INSERT INTO sources (id, name, url, category, language, enabled, priority,
                                 crawl_interval, parser_type, last_success_at, last_error_at,
                                 last_seen_published_at, failure_count, etag, last_modified)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(id) DO UPDATE SET
                name=excluded.name, url=excluded.url, category=excluded.category,
                language=excluded.language, enabled=excluded.enabled, priority=excluded.priority,
                crawl_interval=excluded.crawl_interval, parser_type=excluded.parser_type,
                etag=excluded.etag, last_modified=excluded.last_modified
            """,
            (
                source.id, source.name, source.url, source.category, source.language,
                int(source.enabled), source.priority, source.crawl_interval, source.parser_type,
                source.last_success_at, source.last_error_at, source.last_seen_published_at,
                source.failure_count, source.etag, source.last_modified,
            ),
        )
        self.conn.commit()

    def mark_success(self, source_id: str, published_at: str | None) -> None:
        self.conn.execute(
            "UPDATE sources SET last_success_at=?, failure_count=0, last_seen_published_at=COALESCE(?, last_seen_published_at) WHERE id=?",
            (_now(), published_at, source_id),
        )
        self.conn.commit()

    def mark_error(self, source_id: str) -> None:
        self.conn.execute(
            "UPDATE sources SET last_error_at=?, failure_count=failure_count+1 WHERE id=?",
            (_now(), source_id),
        )
        self.conn.commit()

    def list_all(self) -> list[sqlite3.Row]:
        return list(self.conn.execute("SELECT * FROM sources ORDER BY priority DESC, name"))

    def health(self) -> list[sqlite3.Row]:
        return list(
            self.conn.execute(
                """SELECT id, name, category, last_success_at, last_error_at, failure_count
                   FROM sources ORDER BY failure_count DESC, name"""
            )
        )


class ArticleRepository:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def insert(self, article: Article) -> bool:
        """Insert if new. Returns True when a new row was written."""
        try:
            cur = self.conn.execute(
                """
                INSERT OR IGNORE INTO articles
                (id, source_id, url, canonical_url, title, published_at, author, content,
                 language, content_hash, retrieved_at, first_seen_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    article.id, article.source_id, article.url, article.canonical_url,
                    article.title, article.published_at, article.author, article.content,
                    article.language, article.content_hash, article.retrieved_at, _now(),
                ),
            )
            self.conn.commit()
            return cur.rowcount > 0
        except sqlite3.Error as exc:
            raise StorageError(f"insert article failed: {exc}") from exc

    def get(self, article_id: str) -> sqlite3.Row | None:
        return self.conn.execute("SELECT * FROM articles WHERE id=?", (article_id,)).fetchone()

    def find_by_url(self, canonical_url: str) -> sqlite3.Row | None:
        return self.conn.execute(
            "SELECT * FROM articles WHERE canonical_url=? LIMIT 1", (canonical_url,)
        ).fetchone()

    def find_by_hash(self, content_hash: str) -> sqlite3.Row | None:
        return self.conn.execute(
            "SELECT * FROM articles WHERE content_hash=? LIMIT 1", (content_hash,)
        ).fetchone()

    def list_recent(self, hours: int = 24) -> list[sqlite3.Row]:
        return list(
            self.conn.execute(
                """SELECT * FROM articles
                   WHERE COALESCE(published_at, first_seen_at) >= datetime('now', ?)
                   ORDER BY COALESCE(published_at, first_seen_at) DESC""",
                (f"-{hours} hours",),
            )
        )

    def list_unanalyzed(self, limit: int = 200) -> list[sqlite3.Row]:
        return list(
            self.conn.execute(
                "SELECT * FROM articles WHERE is_ai_related IS NULL ORDER BY first_seen_at DESC LIMIT ?",
                (limit,),
            )
        )

    def save_analysis(
        self,
        article_id: str,
        is_ai_related: bool,
        topics: list[str],
        entities: list[str],
        summary: str | None,
        importance: float | None,
        confidence: float | None,
    ) -> None:
        self.conn.execute(
            """UPDATE articles SET is_ai_related=?, topics=?, entities=?, summary=?,
               importance=?, confidence=? WHERE id=?""",
            (
                int(is_ai_related), json.dumps(topics, ensure_ascii=False),
                json.dumps(entities, ensure_ascii=False), summary, importance, confidence,
                article_id,
            ),
        )
        self.conn.commit()


class EventRepository:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def upsert(self, event: Event) -> None:
        self.conn.execute(
            """
            INSERT INTO events (event_id, title, first_seen_at, last_seen_at, published_at,
                importance_score, novelty_score, confidence_score, freshness_score, source_score,
                base_score, category, entities, summary, why_it_matters, status)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(event_id) DO UPDATE SET
                title=excluded.title, last_seen_at=excluded.last_seen_at,
                importance_score=excluded.importance_score, novelty_score=excluded.novelty_score,
                confidence_score=excluded.confidence_score, freshness_score=excluded.freshness_score,
                source_score=excluded.source_score, base_score=excluded.base_score,
                category=excluded.category, entities=excluded.entities,
                summary=excluded.summary, why_it_matters=excluded.why_it_matters,
                status=excluded.status
            """,
            (
                event.event_id, event.title, event.first_seen_at or _now(),
                event.last_seen_at or _now(), event.published_at, event.importance_score,
                event.novelty_score, event.confidence_score, event.freshness_score,
                event.source_score, event.base_score, event.category,
                json.dumps(event.entities, ensure_ascii=False), event.summary,
                event.why_it_matters, event.status,
            ),
        )
        self.conn.commit()

    def link_article(self, event_id: str, article_id: str, relation_type: str) -> None:
        self.conn.execute(
            "INSERT OR IGNORE INTO event_articles (event_id, article_id, relation_type) VALUES (?,?,?)",
            (event_id, article_id, relation_type),
        )
        self.conn.commit()

    def articles_for(self, event_id: str) -> list[sqlite3.Row]:
        return list(
            self.conn.execute(
                """SELECT a.*, ea.relation_type FROM event_articles ea
                   JOIN articles a ON a.id = ea.article_id WHERE ea.event_id=?""",
                (event_id,),
            )
        )

    def get(self, event_id: str) -> sqlite3.Row | None:
        return self.conn.execute("SELECT * FROM events WHERE event_id=?", (event_id,)).fetchone()

    def list_recent(self, hours: int = 24, limit: int = 50) -> list[sqlite3.Row]:
        return list(
            self.conn.execute(
                """SELECT * FROM events
                   WHERE COALESCE(published_at, first_seen_at) >= datetime('now', ?)
                   ORDER BY base_score DESC LIMIT ?""",
                (f"-{hours} hours", limit),
            )
        )

    def search(self, query: str, limit: int = 50) -> list[sqlite3.Row]:
        like = f"%{query}%"
        return list(
            self.conn.execute(
                """SELECT * FROM events WHERE title LIKE ? OR summary LIKE ? OR entities LIKE ?
                   ORDER BY base_score DESC LIMIT ?""",
                (like, like, like, limit),
            )
        )

    def add_claim(self, claim: Claim) -> None:
        self.conn.execute(
            "INSERT INTO claims (event_id, claim, evidence_type, source_urls) VALUES (?,?,?,?)",
            (claim.event_id, claim.claim, claim.evidence_type, json.dumps(claim.source_urls, ensure_ascii=False)),
        )
        self.conn.commit()

    def claims_for(self, event_id: str) -> list[sqlite3.Row]:
        return list(self.conn.execute("SELECT * FROM claims WHERE event_id=?", (event_id,)))


class PipelineRunRepository:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def start(self) -> PipelineRun:
        run = PipelineRun(run_id=str(uuid.uuid4()))
        self.conn.execute(
            "INSERT INTO pipeline_runs (run_id, started_at, status) VALUES (?,?,?)",
            (run.run_id, run.started_at, run.status),
        )
        self.conn.commit()
        return run

    def finish(self, run: PipelineRun) -> None:
        self.conn.execute(
            """UPDATE pipeline_runs SET finished_at=?, status=?, sources_total=?, sources_success=?,
               articles_seen=?, articles_new=?, errors=? WHERE run_id=?""",
            (
                _now(), run.status, run.sources_total, run.sources_success,
                run.articles_seen, run.articles_new,
                json.dumps(run.errors, ensure_ascii=False), run.run_id,
            ),
        )
        self.conn.commit()

    def history(self, limit: int = 20) -> list[sqlite3.Row]:
        return list(
            self.conn.execute(
                "SELECT * FROM pipeline_runs ORDER BY started_at DESC LIMIT ?", (limit,)
            )
        )
