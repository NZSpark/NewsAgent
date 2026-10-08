"""Event clustering (TASK-025) + evidence collection (TASK-027, TASK-028).

First version uses only deterministic signals: time window, entity overlap,
title token overlap, category. No embeddings.
"""
from __future__ import annotations

import hashlib
import json
import re

from ..logging_setup import get_logger
from ..storage.database import connect
from ..storage.models import Event
from ..storage.repository import ArticleRepository, EventRepository

log = get_logger("pipeline.cluster")

WINDOW_HOURS = 48
TITLE_OVERLAP_THRESHOLD = 0.5
_STOPWORDS = {"the", "a", "an", "of", "to", "in", "and", "for", "on", "with", "is", "are", "new", "ai"}


def _tokens(text: str) -> set[str]:
    words = re.findall(r"[a-zA-Z0-9]+", (text or "").lower())
    return {w for w in words if w not in _STOPWORDS and len(w) > 2}


def _overlap(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / min(len(a), len(b))


def _entities(row) -> set[str]:
    try:
        return {e.lower() for e in json.loads(row["entities"] or "[]")}
    except (json.JSONDecodeError, TypeError):
        return set()


def _event_id(article) -> str:
    seed = f"{article['canonical_url']}"
    return hashlib.sha1(seed.encode("utf-8")).hexdigest()[:16]


def cluster_articles(hours: int = WINDOW_HOURS) -> dict:
    """Group recent AI-related articles into events."""
    conn = connect()
    article_repo = ArticleRepository(conn)
    event_repo = EventRepository(conn)

    rows = [r for r in article_repo.list_recent(hours=hours) if r["is_ai_related"]]
    clusters: list[dict] = []

    for row in rows:
        tokens = _tokens(row["title"])
        entities = _entities(row)
        placed = False
        for cluster in clusters:
            if cluster["source_ids"] and row["source_id"] in cluster["source_ids"] and len(cluster["articles"]) > 1:
                # allow same source only when strong title overlap
                if _overlap(tokens, cluster["tokens"]) < TITLE_OVERLAP_THRESHOLD:
                    continue
            if _overlap(tokens, cluster["tokens"]) >= TITLE_OVERLAP_THRESHOLD or \
               _overlap(entities, cluster["entities"]) >= 0.5:
                cluster["articles"].append(row)
                cluster["tokens"] |= tokens
                cluster["entities"] |= entities
                cluster["source_ids"].add(row["source_id"])
                placed = True
                break
        if not placed:
            clusters.append(
                {
                    "articles": [row],
                    "tokens": set(tokens),
                    "entities": set(entities),
                    "source_ids": {row["source_id"]},
                }
            )

    events_created = 0
    for cluster in clusters:
        articles = cluster["articles"]
        primary = articles[0]
        event_id = _event_id(primary)
        event = Event(
            event_id=event_id,
            title=primary["title"],
            published_at=primary["published_at"],
            category=primary["source_id"],
            entities=sorted(cluster["entities"]),
            summary=primary["summary"],
            importance_score=float(primary["importance"] or 0.0),
        )
        event_repo.upsert(event)
        for i, art in enumerate(articles):
            relation = "primary" if i == 0 else "independent_report"
            event_repo.link_article(event_id, art["id"], relation)
        events_created += 1

    conn.close()
    result = {"articles": len(rows), "events": events_created}
    log.info("cluster run done: %s", result)
    return result
