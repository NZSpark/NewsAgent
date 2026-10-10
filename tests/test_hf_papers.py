"""Regression tests: HF Papers (paperswithcode) parser must yield titles + abstracts."""
from __future__ import annotations

from pathlib import Path

from newsagent.fetch.hf_papers import fetch_hf_papers
from newsagent.sources.models import Source

FIXTURE = Path(__file__).parent / "fixtures" / "hf_papers_trending.html"


class _Resp:
    status_code = 200
    url = "https://huggingface.co/papers/trending"

    def __init__(self, text: str) -> None:
        self.text = text


class _Client:
    def get(self, *a, **k):  # noqa: ANN002, ANN003
        return _Resp(FIXTURE.read_text(encoding="utf-8"))


def test_infer_parser_routes_papers_sources():
    assert Source.infer_parser("https://paperswithcode.com/") == "hf_papers"
    assert Source.infer_parser("https://huggingface.co/papers/trending") == "hf_papers"
    # Plain HF blog still uses RSS.
    assert Source.infer_parser("https://huggingface.co/blog/feed.xml") == "rss"


def test_hf_papers_extracts_title_and_abstract():
    src = Source(
        id="papers_with_code", name="Papers with Code",
        url="https://huggingface.co/papers/trending",
        category="research", parser_type="hf_papers",
    )
    cands = fetch_hf_papers(src, _Client(), timeout=5)
    assert cands, "expected paper candidates"
    for c in cands:
        assert c.url.startswith("https://huggingface.co/papers/")
        assert c.title and "Submitted by" not in c.title
        assert len(c.content) > 120, f"abstract too short for {c.title!r}"
