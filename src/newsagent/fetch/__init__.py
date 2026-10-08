"""Unified fetch interface (TASK-008)."""
from __future__ import annotations

import httpx

from ..config import get_config
from ..logging_setup import FetchError, get_logger
from ..sources.models import ArticleCandidate, Source
from .html import fetch_html
from .rss import fetch_rss

log = get_logger("fetch")


def build_client() -> httpx.Client:
    cfg = get_config()
    return httpx.Client(
        headers={"User-Agent": cfg.user_agent},
        timeout=cfg.fetch_timeout,
        follow_redirects=True,
    )


def fetch(source: Source, client: httpx.Client | None = None) -> list[ArticleCandidate]:
    """Fetch one source. parser_type decides the strategy; failures are isolated."""
    cfg = get_config()
    owns_client = client is None
    client = client or build_client()
    try:
        if source.parser_type == "rss":
            return fetch_rss(source, client, cfg.fetch_timeout)
        if source.parser_type == "api":
            # API parsers live in sources/parsers; fall back to RSS for now.
            try:
                return fetch_rss(source, client, cfg.fetch_timeout)
            except FetchError:
                return fetch_html(source, client, cfg.fetch_timeout)
        return fetch_html(source, client, cfg.fetch_timeout)
    finally:
        if owns_client:
            client.close()
