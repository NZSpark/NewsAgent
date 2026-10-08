"""Source / Article / Event data models (TASK-004, TASK-006, design section 6)."""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone


# Category -> parser type heuristics
PARSER_BY_HOST: dict[str, str] = {
    "arxiv.org": "rss",
    "huggingface.co": "rss",
    "news.ycombinator.com": "api",
    "github.com": "html",
    "reddit.com": "rss",
}

_CATEGORY_KEYWORDS = {
    "official": ["openai", "anthropic", "deepmind", "meta ai", "microsoft research", "nvidia", "mistral", "cohere", "hugging face"],
    "research": ["arxiv", "papers with code", "semantic scholar", "alphaxiv"],
    "community": ["hacker news", "reddit", "lobsters", "github trending"],
    "newsletter": ["the batch", "import ai", "tldr", "rundown", "ben's bites", "smol.ai"],
}


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _slugify(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")
    return slug or "source"


@dataclass
class Source:
    id: str
    name: str
    url: str
    category: str = "media"
    language: str = "en"
    enabled: bool = True
    priority: int = 5
    crawl_interval: int = 60
    parser_type: str = "rss"
    # runtime state (TASK-005)
    last_success_at: str | None = None
    last_error_at: str | None = None
    last_seen_published_at: str | None = None
    failure_count: int = 0
    etag: str | None = None
    last_modified: str | None = None

    @staticmethod
    def make_id(name: str) -> str:
        return _slugify(name)

    @staticmethod
    def infer_category(name: str, section: str = "") -> str:
        haystack = f"{name} {section}".lower()
        for category, keywords in _CATEGORY_KEYWORDS.items():
            if any(kw in haystack for kw in keywords):
                return category
        return "media"

    @staticmethod
    def infer_parser(url: str) -> str:
        for host, parser in PARSER_BY_HOST.items():
            if host in url:
                return parser
        return "html"


@dataclass
class ArticleCandidate:
    """Raw item produced by a fetcher before normalization (TASK-008)."""

    source_id: str
    source_name: str
    category: str
    url: str
    title: str
    published_at: str | None = None
    author: str | None = None
    content: str = ""
    language: str = "en"


@dataclass
class Article:
    """Normalized article ready for storage (design section 6)."""

    source_id: str
    url: str
    canonical_url: str
    title: str
    published_at: str | None
    author: str | None
    content: str
    language: str
    content_hash: str
    retrieved_at: str = field(default_factory=lambda: utcnow().isoformat())
    id: str = ""

    def __post_init__(self) -> None:
        if not self.content_hash:
            self.content_hash = content_hash(self.content)
        if not self.id:
            self.id = article_id(self.canonical_url, self.content_hash)


def content_hash(text: str) -> str:
    normalized = re.sub(r"\s+", " ", (text or "").strip().lower())
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def article_id(canonical_url: str, chash: str) -> str:
    return hashlib.sha1(f"{canonical_url}|{chash}".encode("utf-8")).hexdigest()
