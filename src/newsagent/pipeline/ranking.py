"""Scoring: freshness, novelty, source/evidence, base ranking (TASK-031..034)."""
from __future__ import annotations

import math
from datetime import datetime, timezone

from ..logging_setup import get_logger
from ..storage.database import connect

log = get_logger("pipeline.ranking")

SOURCE_PRIORITY = {
    "official": 1.0,
    "research": 0.8,
    "media": 0.7,
    "newsletter": 0.6,
    "community": 0.4,
}

EVIDENCE_SCORE = {
    "primary": 1.0,
    "independent_report": 0.8,
    "research": 0.9,
    "discussion": 0.4,
}


def _parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def freshness_score(published_at: str | None, half_life_hours: float = 24.0) -> float:
    ts = _parse_time(published_at)
    if ts is None:
        return 0.5
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    age_hours = max(0.0, (datetime.now(timezone.utc) - ts).total_seconds() / 3600)
    return math.exp(-age_hours / half_life_hours)


def novelty_score(source_count: int) -> float:
    """Fewer independent sources = more novel. Saturates as source count grows."""
    return 1.0 / (1.0 + max(0, source_count - 1) * 0.5)


def source_score(relation_types: list[str]) -> float:
    if not relation_types:
        return 0.3
    scores = [EVIDENCE_SCORE.get(rt, 0.4) for rt in relation_types]
    return sum(scores) / len(scores)


def compute_base(freshness: float, priority: float, evidence: float, novelty: float) -> float:
    return round(freshness * priority * evidence * novelty, 4)


def run_ranking() -> dict:
    """Recompute base_score for all events from their linked articles."""
    conn = connect()
    events = list(conn.execute("SELECT * FROM events"))
    updated = 0
    for event in events:
        links = list(
            conn.execute(
                """SELECT ea.relation_type, s.category FROM event_articles ea
                   JOIN articles a ON a.id = ea.article_id
                   JOIN sources s ON s.id = a.source_id
                   WHERE ea.event_id=?""",
                (event["event_id"],),
            )
        )
        if not links:
            continue
        relation_types = [link["relation_type"] for link in links]
        categories = [link["category"] or "media" for link in links]
        priority = max(SOURCE_PRIORITY.get(c, 0.5) for c in categories)
        evidence = source_score(relation_types)
        novelty = novelty_score(len(links))
        fresh = freshness_score(event["published_at"] or event["first_seen_at"])
        base = compute_base(fresh, priority, evidence, novelty)
        conn.execute(
            """UPDATE events SET freshness_score=?, source_score=?, novelty_score=?, base_score=?
               WHERE event_id=?""",
            (round(fresh, 4), round(evidence, 4), round(novelty, 4), base, event["event_id"]),
        )
        updated += 1
    conn.commit()
    conn.close()
    result = {"events_ranked": updated}
    log.info("ranking run done: %s", result)
    return result
