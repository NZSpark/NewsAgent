"""HuggingFace Papers (trending) fetcher.

paperswithcode.com now redirects to https://huggingface.co/papers/trending,
which server-renders each paper as a card containing the title and the full
abstract. The generic HTML fetcher only reads anchor text and therefore
produced title-only, content-less candidates; this parser walks the card
structure to recover title + abstract.
"""
from __future__ import annotations

import re
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup

from ..logging_setup import FetchError, get_logger
from ..sources.models import ArticleCandidate, Source

log = get_logger("fetch.hf_papers")

_PAPER_HREF_RE = re.compile(r"^/papers/\d")
_MIN_ABSTRACT_LEN = 120


def _find_title(card) -> str:
    """The paper title lives in an <h3> with the 'text-xl' class."""
    for h3 in card.find_all("h3"):
        if "text-xl" in (h3.get("class") or []):
            return h3.get_text(" ", strip=True)
    h3 = card.find("h3")
    return h3.get_text(" ", strip=True) if h3 else ""


def _find_abstract(card, title: str) -> str:
    """Longest <p> in the card is the abstract body."""
    best = ""
    for p in card.find_all("p"):
        text = p.get_text(" ", strip=True)
        if len(text) > len(best):
            best = text
    if len(best) >= _MIN_ABSTRACT_LEN:
        return best
    # Fallback: card text minus the title and attribution noise.
    text = card.get_text(" ", strip=True)
    if title and title in text:
        text = text.split(title, 1)[1]
    return text.strip()


def fetch_hf_papers(source: Source, client: httpx.Client, timeout: float) -> list[ArticleCandidate]:
    try:
        resp = client.get(source.url, timeout=timeout, follow_redirects=True)
    except httpx.HTTPError as exc:
        raise FetchError(f"HF papers request failed for {source.name}: {exc}") from exc
    if resp.status_code >= 400:
        raise FetchError(f"HF papers {source.name} returned HTTP {resp.status_code}")

    soup = BeautifulSoup(resp.text, "html.parser")
    base = str(resp.url) or source.url

    seen: set[str] = set()
    candidates: list[ArticleCandidate] = []
    for anchor in soup.find_all("a", href=True):
        href = anchor["href"].strip()
        if not _PAPER_HREF_RE.match(href):
            continue
        absolute = urljoin(base, href)
        if absolute in seen:
            continue

        # Climb to the card container that holds both title and abstract.
        card = anchor
        title = ""
        for _ in range(6):
            card = card.parent
            if card is None:
                break
            title = _find_title(card)
            if title:
                break
        if not title:
            continue

        seen.add(absolute)
        candidates.append(
            ArticleCandidate(
                source_id=source.id,
                source_name=source.name,
                category=source.category,
                url=absolute,
                title=title,
                content=_find_abstract(card, title),
                language=source.language,
            )
        )

    if not candidates:
        raise FetchError(f"HF papers {source.name}: no paper cards found")
    log.info("%s: %d paper cards", source.name, len(candidates))
    return candidates[:50]
