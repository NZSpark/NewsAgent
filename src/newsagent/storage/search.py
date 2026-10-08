"""Local event search API (TASK-035)."""
from __future__ import annotations

from ..logging_setup import get_logger
from .database import connect
from .repository import EventRepository

log = get_logger("storage.search")


def get_recent_events(hours: int = 24, limit: int = 50) -> list[dict]:
    conn = connect()
    rows = EventRepository(conn).list_recent(hours=hours, limit=limit)
    result = [dict(r) for r in rows]
    conn.close()
    return result


def get_event(event_id: str) -> dict | None:
    conn = connect()
    repo = EventRepository(conn)
    row = repo.get(event_id)
    if row is None:
        conn.close()
        return None
    event = dict(row)
    event["articles"] = [dict(a) for a in repo.articles_for(event_id)]
    event["claims"] = [dict(c) for c in repo.claims_for(event_id)]
    conn.close()
    return event


def search_local_events(query: str, limit: int = 50) -> list[dict]:
    conn = connect()
    rows = EventRepository(conn).search(query, limit=limit)
    result = [dict(r) for r in rows]
    conn.close()
    return result
