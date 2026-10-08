"""Scheduler + fetch retry/backoff (TASK-038, TASK-039) and source health (TASK-040)."""
from __future__ import annotations

import time
from datetime import datetime, timezone

import httpx

from ..config import get_config
from ..logging_setup import FetchError, get_logger
from ..storage.database import connect, init_db
from ..storage.repository import SourceRepository
from .models import Source
from .registry import parse_sites_md

log = get_logger("sources.scheduler")

BACKOFF_BASE = 1.0
BACKOFF_FACTOR = 2.0


def _parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def due_sources(sources: list[Source], state: dict[str, str | None]) -> list[Source]:
    """Return sources whose crawl interval has elapsed since last success/error."""
    now = datetime.now(timezone.utc)
    due: list[Source] = []
    for source in sources:
        last = _parse_time(state.get(source.id))
        if last is None:
            due.append(source)
            continue
        elapsed_min = (now - last).total_seconds() / 60
        if elapsed_min >= source.crawl_interval:
            due.append(source)
    return due


def with_backoff(fn, retries: int, on_retry=None):
    """Run fn with exponential backoff on transient errors (429 / 5xx / network)."""
    last_exc: Exception | None = None
    for attempt in range(retries + 1):
        try:
            return fn()
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            if status not in (429, 500, 502, 503, 504):
                raise
            last_exc = exc
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            last_exc = exc
        except FetchError as exc:
            last_exc = exc
            if "429" not in str(exc) and "50" not in str(exc):
                raise
        if attempt < retries:
            delay = BACKOFF_BASE * (BACKOFF_FACTOR ** attempt)
            log.info("retry %d/%d after %.1fs", attempt + 1, retries, delay)
            if on_retry:
                on_retry(attempt, last_exc)
            time.sleep(delay)
    raise last_exc if last_exc else FetchError("unknown fetch failure")


def source_health() -> list[dict]:
    conn = connect()
    init_db(conn)
    rows = [dict(r) for r in SourceRepository(conn).health()]
    conn.close()
    for row in rows:
        if row.get("failure_count", 0) >= 5:
            row["status"] = "failed"
        elif row.get("failure_count", 0) > 0:
            row["status"] = "warning"
        elif row.get("last_success_at"):
            row["status"] = "healthy"
        else:
            row["status"] = "unknown"
    return rows


def run_due_sources() -> dict:
    """One scheduler tick: fetch only sources that are due."""
    from ..pipeline.runner import run_fetch

    conn = connect()
    init_db(conn)
    repo = SourceRepository(conn)
    state = {r["id"]: (r["last_success_at"] or r["last_error_at"]) for r in repo.list_all()}
    conn.close()

    all_sources = [s for s in parse_sites_md(get_config().sites_md) if s.enabled]
    due = due_sources(all_sources, state)
    if not due:
        log.info("scheduler: no sources due")
        return {"due": 0, "result": None}
    log.info("scheduler: %d sources due", len(due))
    result = run_fetch(only_source_ids={s.id for s in due})
    return {"due": len(due), "result": result}
