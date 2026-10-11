"""Unified fetch interface (TASK-008)."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

import httpx

from ..config import get_config
from ..logging_setup import FetchError, get_logger
from ..sources.models import ArticleCandidate, Source
from .hf_papers import fetch_hf_papers
from .html import fetch_article_body, fetch_html
from .rss import fetch_rss

log = get_logger("fetch")


def build_client() -> httpx.Client:
    cfg = get_config()
    return httpx.Client(
        headers={"User-Agent": cfg.user_agent},
        timeout=cfg.fetch_timeout,
        follow_redirects=True,
    )


def enrich_bodies(
    candidates: list[ArticleCandidate],
    client: httpx.Client,
) -> list[ArticleCandidate]:
    """Fetch article bodies for candidates that only carry a title/link.

    Runs concurrently, capped by `fetch_max_articles` per source. Missing bodies
    are non-fatal: the candidate is kept with its existing (possibly empty)
    content so the pipeline can still record the link.
    """
    cfg = get_config()
    if not cfg.fetch_body:
        return candidates
    empty = [c for c in candidates if not c.content]
    # Prefer the newest entries so the per-source cap targets fresh content.
    empty.sort(key=lambda c: c.published_at or "", reverse=True)
    need = empty[: cfg.fetch_max_articles]
    if not need:
        return candidates

    def _one(c: ArticleCandidate) -> None:
        c.content = fetch_article_body(c.url, client, cfg.fetch_timeout)

    workers = max(1, min(cfg.fetch_concurrency, len(need)))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        list(pool.map(_one, need))
    fetched = sum(1 for c in need if c.content)
    log.info("enriched %d/%d bodies", fetched, len(need))
    return candidates


def fetch(source: Source, client: httpx.Client | None = None) -> list[ArticleCandidate]:
    """Fetch one source. parser_type decides the strategy; failures are isolated."""
    cfg = get_config()
    owns_client = client is None
    client = client or build_client()
    try:
        if source.parser_type == "rss":
            candidates = fetch_rss(source, client, cfg.fetch_timeout)
        elif source.parser_type == "hf_papers":
            candidates = fetch_hf_papers(source, client, cfg.fetch_timeout)
        elif source.parser_type == "api":
            # API parsers live in sources/parsers; fall back to RSS for now.
            try:
                candidates = fetch_rss(source, client, cfg.fetch_timeout)
            except FetchError:
                candidates = fetch_html(source, client, cfg.fetch_timeout)
        else:
            candidates = fetch_html(source, client, cfg.fetch_timeout)
        return enrich_bodies(candidates, client)
    finally:
        if owns_client:
            client.close()
