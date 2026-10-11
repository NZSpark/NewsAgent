"""HTML fetcher (TASK-007): generic list/article extraction + Jina fallback."""
from __future__ import annotations

from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup

try:  # trafilatura is optional at import time
    import trafilatura
    _HAS_TRAFILATURA = True
except Exception:  # noqa: BLE001
    _HAS_TRAFILATURA = False

from ..logging_setup import FetchError, get_logger
from ..sources.models import ArticleCandidate, Source

log = get_logger("fetch.html")


def extract_main_text(html: str, url: str) -> str:
    if _HAS_TRAFILATURA:
        try:
            extracted = trafilatura.extract(html, url=url, include_comments=False)
            if extracted:
                return extracted
        except Exception as exc:  # noqa: BLE001
            log.debug("trafilatura failed for %s: %s", url, exc)
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "nav", "footer", "header", "aside"]):
        tag.decompose()
    return soup.get_text(separator="\n", strip=True)


def _same_host(base: str, candidate: str) -> bool:
    try:
        return urlparse(base).netloc == urlparse(candidate).netloc
    except Exception:  # noqa: BLE001
        return False


# URL path segments that mark navigation / hub pages rather than articles.
_NON_ARTICLE_SEGMENTS = (
    "/author/", "/authors/", "/tag/", "/tags/", "/category/", "/categories/",
    "/topics/", "/topic/", "/events/", "/event/", "/sponsored/", "/brand-studio/",
    "/video/", "/videos/", "/podcast/", "/podcasts/", "/page/", "/about/",
    "/contact/", "/privacy/", "/terms/", "/newsletter", "/feed", "/search",
)


def _looks_like_non_article(url: str) -> bool:
    try:
        path = urlparse(url).path.lower()
    except Exception:  # noqa: BLE001
        return False
    if path in ("", "/"):
        return True
    return any(seg in path for seg in _NON_ARTICLE_SEGMENTS)


def fetch_html(source: Source, client: httpx.Client, timeout: float) -> list[ArticleCandidate]:
    try:
        resp = client.get(source.url, timeout=timeout, follow_redirects=True)
    except httpx.HTTPError as exc:
        raise FetchError(f"HTML request failed for {source.name}: {exc}") from exc
    if resp.status_code >= 400:
        raise FetchError(f"HTML {source.name} returned HTTP {resp.status_code}")

    soup = BeautifulSoup(resp.text, "html.parser")
    title_tag = soup.find("title")
    page_title = title_tag.get_text(strip=True) if title_tag else source.name

    seen: set[str] = set()
    candidates: list[ArticleCandidate] = []
    for anchor in soup.find_all("a", href=True):
        href = anchor["href"].strip()
        if not href or href.startswith(("#", "mailto:", "javascript:")):
            continue
        absolute = urljoin(source.url, href)
        if not _same_host(source.url, absolute) or absolute in seen:
            continue
        if _looks_like_non_article(absolute):
            continue
        text = anchor.get_text(" ", strip=True)
        if len(text) < 15:  # skip nav / short links
            continue
        seen.add(absolute)
        candidates.append(
            ArticleCandidate(
                source_id=source.id,
                source_name=source.name,
                category=source.category,
                url=absolute,
                title=text,
                content="",
                language=source.language,
            )
        )

    # Prefer likely articles (longer titles) so the body-fetch cap targets
    # real content instead of navigation / category links.
    candidates.sort(key=lambda c: len(c.title), reverse=True)

    if not candidates:
        # Fallback: the page itself is the article.
        candidates.append(
            ArticleCandidate(
                source_id=source.id,
                source_name=source.name,
                category=source.category,
                url=source.url,
                title=page_title,
                content=extract_main_text(resp.text, source.url),
                language=source.language,
            )
        )

    log.info("%s: %d html candidates", source.name, len(candidates))
    return candidates[:50]


def fetch_article_body(url: str, client: httpx.Client, timeout: float) -> str:
    """Fetch a single article page and extract its main text body.

    Used by both HTML and RSS fetchers to populate content for candidates that
    otherwise carry only a title/link. Failures are non-fatal: return "".
    """
    try:
        resp = client.get(url, timeout=timeout, follow_redirects=True)
    except httpx.HTTPError as exc:
        log.debug("body fetch failed for %s: %s", url, exc)
        return ""
    if resp.status_code >= 400:
        log.debug("body fetch %s returned HTTP %s", url, resp.status_code)
        return ""
    ctype = resp.headers.get("content-type", "")
    if "html" not in ctype and "xml" not in ctype and ctype:
        return ""
    try:
        return extract_main_text(resp.text, url)
    except Exception as exc:  # noqa: BLE001
        log.debug("body extract failed for %s: %s", url, exc)
        return ""


def fetch_jina(url: str, api_key: str, client: httpx.Client, timeout: float) -> str:
    """Jina Reader fallback: https://r.jina.ai/<url> returns LLM-friendly markdown."""
    target = f"https://r.jina.ai/{url}"
    headers = {"Accept": "text/plain"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    try:
        resp = client.get(target, timeout=timeout, headers=headers)
    except httpx.HTTPError as exc:
        raise FetchError(f"Jina fetch failed for {url}: {exc}") from exc
    if resp.status_code >= 400:
        raise FetchError(f"Jina returned HTTP {resp.status_code} for {url}")
    return resp.text
