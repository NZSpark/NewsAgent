"""Semantic clustering (TASK-026): LLM-assisted merge of rule-based clusters.

Runs after `cluster_articles`. Given a batch of candidate event titles, ask the
LLM which ones refer to the SAME underlying event. Only merges; never splits.
"""
from __future__ import annotations

from ..logging_setup import LLMError, get_logger
from ..llm import generate
from ..llm.prompts import SEMANTIC_CLUSTER
from ..storage.database import connect
from ..storage.repository import EventRepository

log = get_logger("pipeline.semantic_cluster")

MIN_EVENTS = 3
MAX_EVENTS = 40


def _load_candidate_events(conn, hours: int) -> list[dict]:
    rows = list(
        conn.execute(
            """SELECT event_id, title, summary FROM events
               WHERE COALESCE(published_at, first_seen_at) >= datetime('now', ?)
               ORDER BY base_score DESC LIMIT ?""",
            (f"-{hours} hours", MAX_EVENTS),
        )
    )
    return [dict(r) for r in rows]


def _apply_merges(conn, merges: list[dict]) -> int:
    """Merge each group into its first (canonical) event."""
    merged = 0
    for group in merges:
        ids = [g for g in group.get("event_ids", []) if isinstance(g, str)]
        if len(ids) < 2:
            continue
        keep, drop = ids[0], ids[1:]
        for dup in drop:
            links = list(
                conn.execute(
                    "SELECT article_id, relation_type FROM event_articles WHERE event_id=?",
                    (dup,),
                )
            )
            for link in links:
                relation = link["relation_type"] if link["relation_type"] != "primary" else "independent_report"
                conn.execute(
                    "INSERT OR IGNORE INTO event_articles (event_id, article_id, relation_type) VALUES (?,?,?)",
                    (keep, link["article_id"], relation),
                )
            conn.execute("DELETE FROM event_articles WHERE event_id=?", (dup,))
            conn.execute("DELETE FROM events WHERE event_id=?", (dup,))
            merged += 1
    conn.commit()
    return merged


def semantic_cluster(hours: int = 48, min_events: int = MIN_EVENTS, conn=None) -> dict:
    """Merge rule clusters that describe the same event. No-op when few events.

    Pass an existing `conn` (e.g. in tests) to avoid opening a new connection;
    when omitted, a connection is opened and closed here.
    """
    owns_conn = conn is None
    conn = conn or connect()
    events = _load_candidate_events(conn, hours)
    if len(events) < min_events:
        if owns_conn:
            conn.close()
        return {"candidates": len(events), "merged": 0, "skipped": True}

    listing = "\n".join(f"- [{e['event_id']}] {e['title']}" for e in events)
    try:
        result = generate(
            task="cluster_review",
            messages=[
                {"role": "system", "content": SEMANTIC_CLUSTER.system},
                {"role": "user", "content": SEMANTIC_CLUSTER.render(events=listing)},
            ],
            schema={"groups": (False, list)},
            prompt_version=SEMANTIC_CLUSTER.version,
            conn=conn,
        )
    except LLMError as exc:
        log.warning("semantic cluster skipped (LLM unavailable): %s", exc)
        if owns_conn:
            conn.close()
        return {"candidates": len(events), "merged": 0, "skipped": True, "error": str(exc)}

    valid_ids = {e["event_id"] for e in events}
    merges = []
    for group in result.get("groups", []) or []:
        if not isinstance(group, dict):
            continue
        ids = [i for i in group.get("event_ids", []) if i in valid_ids]
        if len(ids) >= 2:
            merges.append({"event_ids": ids})

    merged = _apply_merges(conn, merges)
    if owns_conn:
        conn.close()
    out = {"candidates": len(events), "merged": merged, "skipped": False}
    log.info("semantic cluster done: %s", out)
    return out
