"""Deterministic dedup (TASK-012).

Order: exact URL -> canonical URL -> content hash -> title similarity.
"""
from __future__ import annotations

from difflib import SequenceMatcher

from ..logging_setup import get_logger
from ..sources.models import Article
from ..storage.repository import ArticleRepository

log = get_logger("pipeline.dedup")

TITLE_SIMILARITY_THRESHOLD = 0.92


def is_duplicate(article: Article, repo: ArticleRepository) -> bool:
    # 1. exact URL
    existing = repo.find_by_url(article.url)
    if existing:
        return True
    # 2. canonical URL
    existing = repo.find_by_url(article.canonical_url)
    if existing:
        return True
    # 3. content hash
    existing = repo.find_by_hash(article.content_hash)
    if existing:
        return True
    # 4. title similarity against recent articles from same source
    for row in repo.list_recent(hours=72):
        if row["source_id"] != article.source_id:
            continue
        ratio = SequenceMatcher(None, row["title"].lower(), article.title.lower()).ratio()
        if ratio >= TITLE_SIMILARITY_THRESHOLD:
            return True
    return False
