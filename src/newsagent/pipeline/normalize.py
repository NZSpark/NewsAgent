"""Normalizer (TASK-011): pure-Python cleanup, no LLM."""
from __future__ import annotations

import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from ..sources.models import Article, ArticleCandidate, content_hash

_TRACKING_PARAMS = {
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
    "fbclid", "gclid", "mc_cid", "mc_eid", "ref", "ref_src", "source",
    "igshid", "spm", "cmp", "CMP",
}

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"[ \t\xa0]+")
_MULTI_NL_RE = re.compile(r"\n{3,}")


def canonicalize_url(url: str) -> str:
    try:
        parts = urlsplit(url.strip())
    except ValueError:
        return url.strip()
    scheme = (parts.scheme or "https").lower()
    netloc = parts.netloc.lower()
    if netloc.startswith("www."):
        netloc = netloc[4:]
    query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=False) if k not in _TRACKING_PARAMS]
    path = parts.path.rstrip("/") or "/"
    return urlunsplit((scheme, netloc, path, urlencode(query), ""))


def clean_html(text: str) -> str:
    if not text:
        return ""
    without_tags = _TAG_RE.sub(" ", text)
    without_tags = (
        without_tags.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
        .replace("&quot;", '"').replace("&#39;", "'").replace("&nbsp;", " ")
    )
    lines = [_WS_RE.sub(" ", line).strip() for line in without_tags.splitlines()]
    body = "\n".join(line for line in lines if line)
    return _MULTI_NL_RE.sub("\n\n", body).strip()


def detect_language(text: str) -> str:
    if not text:
        return "en"
    cjk = len(re.findall(r"[\u4e00-\u9fff]", text))
    return "zh" if cjk > len(text) * 0.05 else "en"


def normalize(candidate: ArticleCandidate) -> Article:
    cleaned = clean_html(candidate.content)
    canonical = canonicalize_url(candidate.url)
    language = candidate.language or detect_language(f"{candidate.title} {cleaned}")
    return Article(
        source_id=candidate.source_id,
        url=candidate.url,
        canonical_url=canonical,
        title=candidate.title.strip(),
        published_at=candidate.published_at,
        author=candidate.author,
        content=cleaned,
        language=language,
        content_hash=content_hash(f"{candidate.title}\n{cleaned}"),
    )
