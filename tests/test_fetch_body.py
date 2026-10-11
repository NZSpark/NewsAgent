"""Regression tests: article bodies are fetched and non-article links skipped."""
from __future__ import annotations

from newsagent.fetch import enrich_bodies
from newsagent.fetch.html import _looks_like_non_article
from newsagent.sources.models import ArticleCandidate


class _Resp:
    status_code = 200
    headers = {"content-type": "text/html"}

    def __init__(self, text: str) -> None:
        self.text = text


class _Client:
    def get(self, url, *a, **k):  # noqa: ANN001, ANN002, ANN003
        return _Resp(f"<html><body><article><p>Body for {url}</p></article></body></html>")


def test_non_article_paths_skipped():
    assert _looks_like_non_article("https://x.com/author/jane")
    assert _looks_like_non_article("https://x.com/category/ai")
    assert _looks_like_non_article("https://x.com/")
    assert not _looks_like_non_article("https://x.com/2026/10/09/a-real-article/")


def test_enrich_bodies_fills_empty_content(monkeypatch):
    monkeypatch.setenv("NEWSAGENT_FETCH_MAX_ARTICLES", "5")
    cands = [
        ArticleCandidate("s", "S", "media", "https://x.com/a", "Title A", content=""),
        ArticleCandidate("s", "S", "media", "https://x.com/b", "Title B", content="already"),
        ArticleCandidate("s", "S", "media", "https://x.com/c", "Title C", content=""),
    ]
    out = enrich_bodies(cands, _Client())
    assert out[0].content  # filled
    assert out[1].content == "already"  # untouched
    assert out[2].content  # filled
