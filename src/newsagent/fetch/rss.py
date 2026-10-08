"""RSS/Atom fetcher (TASK-006)."""
from __future__ import annotations

from datetime import datetime, timezone
from time import mktime

import feedparser
import httpx

from ..logging_setup import FetchError, get_logger
from ..sources.models import ArticleCandidate, Source

log = get_logger("fetch.rss")


def _parse_time(entry) -> str | None:
    for key in ("published_parsed", "updated_parsed"):
        value = entry.get(key)
        if value:
            try:
                return datetime.fromtimestamp(mktime(value), tz=timezone.utc).isoformat()
            except (ValueError, OverflowError):
                pass
    return None


def _entry_content(entry) -> str:
    if entry.get("content"):
        return " ".join(part.get("value", "") for part in entry["content"])
    return entry.get("summary", "") or entry.get("description", "") or ""


def fetch_rss(source: Source, client: httpx.Client, timeout: float) -> list[ArticleCandidate]:
    headers = {"User-Agent": client.headers.get("User-Agent", "NewsAgent/0.1")}
    if source.etag:
        headers["If-None-Match"] = source.etag
    if source.last_modified:
        headers["If-Modified-Since"] = source.last_modified

    try:
        resp = client.get(source.url, timeout=timeout, headers=headers, follow_redirects=True)
    except httpx.HTTPError as exc:
        raise FetchError(f"RSS request failed for {source.name}: {exc}") from exc

    if resp.status_code == 304:
        log.info("%s: not modified", source.name)
        return []
    if resp.status_code >= 400:
        raise FetchError(f"RSS {source.name} returned HTTP {resp.status_code}")

    source.etag = resp.headers.get("ETag") or source.etag
    source.last_modified = resp.headers.get("Last-Modified") or source.last_modified

    parsed = feedparser.parse(resp.content)
    candidates: list[ArticleCandidate] = []
    for entry in parsed.entries:
        link = entry.get("link")
        title = entry.get("title")
        if not link or not title:
            continue
        candidates.append(
            ArticleCandidate(
                source_id=source.id,
                source_name=source.name,
                category=source.category,
                url=link,
                title=title.strip(),
                published_at=_parse_time(entry),
                author=entry.get("author"),
                content=_entry_content(entry),
                language=source.language,
            )
        )
    log.info("%s: %d entries", source.name, len(candidates))
    return candidates
