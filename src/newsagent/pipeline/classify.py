"""AI relevance classification + article summary (TASK-023, TASK-024)."""
from __future__ import annotations

import json

from ..logging_setup import LLMError, get_logger
from ..llm import generate
from ..llm.prompts import get_prompt
from ..llm.schemas import ARTICLE_ANALYSIS
from ..storage.database import connect
from ..storage.repository import ArticleRepository

log = get_logger("pipeline.classify")

EXCERPT_CHARS = 800


def _excerpt(content: str) -> str:
    return (content or "")[:EXCERPT_CHARS]


def analyze_article(row, conn=None) -> dict | None:
    """Classify + summarize a single article row. Returns analysis dict or None."""
    title = row["title"]
    source = row["source_id"]
    excerpt = _excerpt(row["content"])

    try:
        classifier = get_prompt("classify_article")
        cls = generate(
            task="classify_article",
            messages=[
                {"role": "system", "content": classifier.system},
                {"role": "user", "content": classifier.render(title=title, source=source, excerpt=excerpt)},
            ],
            schema=ARTICLE_ANALYSIS,
            prompt_version=classifier.version,
            conn=conn,
        )
    except LLMError as exc:
        log.warning("classify failed for %s: %s", title, exc)
        return None

    result = {
        "is_ai_related": bool(cls.get("is_ai_related", False)),
        "topics": cls.get("topics") or [],
        "entities": cls.get("entities") or [],
        "confidence": cls.get("confidence"),
        "summary": None,
        "importance": None,
    }
    if not result["is_ai_related"]:
        return result

    try:
        summarizer = get_prompt("summarize_article")
        summary = generate(
            task="summarize_article",
            messages=[
                {"role": "system", "content": summarizer.system},
                {"role": "user", "content": summarizer.render(title=title, source=source, content=excerpt or title)},
            ],
            schema=ARTICLE_ANALYSIS,
            prompt_version=summarizer.version,
            conn=conn,
        )
        result["summary"] = summary.get("summary")
        result["importance"] = summary.get("importance")
    except LLMError as exc:
        log.warning("summarize failed for %s: %s", title, exc)
    return result


def run_classify(limit: int = 30) -> dict:
    """Classify up to `limit` unanalyzed articles."""
    conn = connect()
    repo = ArticleRepository(conn)
    rows = repo.list_unanalyzed(limit=limit)
    ok = failed = filtered = 0
    for row in rows:
        analysis = analyze_article(row, conn=conn)
        if analysis is None:
            failed += 1
            continue
        repo.save_analysis(
            row["id"], analysis["is_ai_related"], analysis["topics"],
            analysis["entities"], analysis["summary"], analysis["importance"],
            analysis["confidence"],
        )
        ok += 1
        if not analysis["is_ai_related"]:
            filtered += 1
    conn.close()
    result = {"analyzed": ok, "filtered_out": filtered, "failed": failed}
    log.info("classify run done: %s", result)
    return result
