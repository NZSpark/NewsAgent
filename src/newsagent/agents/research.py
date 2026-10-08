"""Active research agent (TASK-042).

For a high-importance event: propose research questions, gather extra context
from linked articles + optional live fetch, then produce a research report.
No shell access.
"""
from __future__ import annotations

from ..fetch import build_client
from ..fetch.html import fetch_jina
from ..config import get_config
from ..logging_setup import FetchError, LLMError, get_logger
from ..llm import generate
from ..llm.prompts import RESEARCH_PLANNER, RESEARCH_REPORT
from ..storage.database import connect
from ..storage.repository import EventRepository
from ..storage.search import get_event

log = get_logger("agents.research")

MAX_MATERIAL_CHARS = 2000


def _materials(event: dict, live: bool) -> str:
    parts: list[str] = []
    articles = event.get("articles", [])
    for art in articles[:8]:
        snippet = (art.get("content") or art.get("summary") or "")[:MAX_MATERIAL_CHARS]
        parts.append(f"### {art['title']} ({art['url']})\n{snippet}")
    if live and not parts:
        cfg = get_config()
        client = build_client()
        try:
            text = fetch_jina(event.get("articles", [{}])[0].get("url", ""), cfg.jina_api_key, client, cfg.fetch_timeout)
            parts.append(text[:MAX_MATERIAL_CHARS])
        except FetchError as exc:
            log.debug("live fetch failed: %s", exc)
        finally:
            client.close()
    return "\n\n".join(parts) if parts else "(no materials)"


def research_event(event_id: str, live: bool = False) -> dict | None:
    event = get_event(event_id)
    if event is None:
        return None

    conn = connect()
    materials = _materials(event, live=live)

    try:
        plan = generate(
            task="complex_analysis",
            messages=[
                {"role": "system", "content": RESEARCH_PLANNER.system},
                {"role": "user", "content": RESEARCH_PLANNER.render(title=event["title"], summary=event.get("summary") or "(none)")},
            ],
            schema={"questions": (False, list)},
            prompt_version=RESEARCH_PLANNER.version,
            conn=conn,
        )
        questions = "\n".join(f"- {q}" for q in (plan.get("questions") or [])) or "- (none)"

        report = generate(
            task="complex_analysis",
            messages=[
                {"role": "system", "content": RESEARCH_REPORT.system},
                {"role": "user", "content": RESEARCH_REPORT.render(title=event["title"], questions=questions, materials=materials)},
            ],
            schema={"report": (True, str), "key_findings": (False, list), "open_questions": (False, list)},
            prompt_version=RESEARCH_REPORT.version,
            conn=conn,
        )
    except LLMError as exc:
        log.warning("research failed for %s: %s", event_id, exc)
        conn.close()
        return None

    conn.close()
    return {
        "event_id": event_id,
        "title": event["title"],
        "questions": plan.get("questions") or [],
        "report": report.get("report"),
        "key_findings": report.get("key_findings") or [],
        "open_questions": report.get("open_questions") or [],
        "sources": [a["url"] for a in event.get("articles", []) if a.get("url")],
    }


def research_top_events(limit: int = 3, min_importance: float = 0.6, live: bool = False) -> list[dict]:
    """Research the most important recent events above a threshold."""
    conn = connect()
    rows = list(
        conn.execute(
            """SELECT event_id, importance_score FROM events
               WHERE importance_score >= ? ORDER BY base_score DESC LIMIT ?""",
            (min_importance, limit),
        )
    )
    conn.close()
    reports = []
    for row in rows:
        result = research_event(row["event_id"], live=live)
        if result:
            reports.append(result)
    return reports
