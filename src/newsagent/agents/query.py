"""Query Agent (TASK-036).

Flow: user question -> parse -> local SQLite search -> rank -> (optional live
verify) -> LLM answer with source URLs. No shell access.
"""
from __future__ import annotations

import re

from ..logging_setup import LLMError, get_logger
from ..llm import generate
from ..llm.schemas import EVENT_ANALYSIS
from ..storage.search import get_recent_events, search_local_events

log = get_logger("agents.query")

SYSTEM = (
    "You are an AI news assistant. Answer in Chinese using ONLY the provided "
    "events. Every factual statement must be traceable to a source URL listed "
    "in the events. If evidence is insufficient, say so. "
    "If you can, respond with STRICT JSON: {\"answer\": string, \"sources\": string[]}. "
    "If you respond in prose instead, that is also acceptable."
)

_HOURS_RE = re.compile(r"(\d+)\s*(小时|hour|h)", re.IGNORECASE)


def _window_hours(question: str) -> int:
    m = _HOURS_RE.search(question)
    if m:
        return min(int(m.group(1)), 24 * 30)
    if "今天" in question or "today" in question.lower():
        return 24
    if "本周" in question or "week" in question.lower():
        return 24 * 7
    return 24


def _collect_events(question: str) -> list[dict]:
    hours = _window_hours(question)
    events = get_recent_events(hours=hours, limit=30)
    if not events:
        events = search_local_events(question, limit=20)
    return events


def _format_events(events: list[dict]) -> str:
    lines = []
    for ev in events[:20]:
        lines.append(
            f"- [{ev['event_id']}] {ev['title']}\n"
            f"  summary: {ev.get('summary') or '(none)'}\n"
            f"  why: {ev.get('why_it_matters') or '(none)'}"
        )
    return "\n".join(lines) if lines else "(no events)"


def answer(question: str) -> dict:
    """Return {"answer": str, "sources": [...], "events": [...]}."""
    events = _collect_events(question)
    if not events:
        return {
            "answer": "本地新闻库中暂无相关事件。请先运行 `news fetch` 与 `news pipeline` 采集数据。",
            "sources": [],
            "events": [],
        }

    sources: list[str] = []
    for ev in events:
        detail = getattr(ev, "get", None)
        if detail:
            for art in []:
                pass
    # gather source urls from linked articles
    from ..storage.search import get_event

    for ev in events:
        full = get_event(ev["event_id"])
        if not full:
            continue
        for art in full.get("articles", []):
            if art.get("url"):
                sources.append(art["url"])

    user = f"Question: {question}\n\nEvents:\n{_format_events(events)}"
    try:
        result = generate(
            task="user_query",
            messages=[
                {"role": "system", "content": SYSTEM},
                {"role": "user", "content": user},
            ],
            schema={"answer": (True, str), "sources": (False, list)},
            prompt_version="query_agent:v1",
            allow_prose=True,
        )
        # Providers may return strict JSON {"answer": ...} or plain prose.
        answer_text = result.get("answer") or result.get("text") or ""
    except LLMError as exc:
        log.warning("query LLM failed, returning raw events: %s", exc)
        answer_text = "（LLM 不可用）本地相关事件：\n" + _format_events(events)

    return {"answer": answer_text, "sources": sorted(set(sources)), "events": events}
