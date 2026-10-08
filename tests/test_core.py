"""Core tests: config, normalizer, dedup, LLM JSON parsing, schemas, ranking."""
from __future__ import annotations

import json

import pytest

from newsagent.llm.client import extract_json
from newsagent.llm.schemas import ARTICLE_ANALYSIS, validate
from newsagent.llm.prompts import get_prompt
from newsagent.llm.router import providers_for_task
from newsagent.logging_setup import SchemaError
from newsagent.pipeline.normalize import canonicalize_url, clean_html, detect_language, normalize
from newsagent.pipeline.ranking import compute_base, freshness_score, novelty_score, source_score
from newsagent.sources.models import Article, ArticleCandidate, content_hash
from newsagent.sources.registry import parse_sites_md


# --- registry ------------------------------------------------------------- #

def test_parse_sites_md(tmp_path):
    md = tmp_path / "sites.md"
    md.write_text(
        "## 官方博客\n\n| 机构 | 网址 |\n| --- | --- |\n"
        "| OpenAI | [https://openai.com/blog](https://openai.com/blog) |\n",
        encoding="utf-8",
    )
    sources = parse_sites_md(md)
    assert len(sources) == 1
    assert sources[0].name == "OpenAI"
    assert sources[0].category == "official"


def test_parse_missing_file(tmp_path):
    assert parse_sites_md(tmp_path / "nope.md") == []


# --- normalize ------------------------------------------------------------ #

def test_canonicalize_strips_tracking():
    url = "https://www.example.com/a/?utm_source=x&id=1"
    out = canonicalize_url(url)
    assert "utm_source" not in out
    assert out.startswith("https://example.com/a")
    assert "id=1" in out


def test_canonicalize_trims_slash_and_www():
    assert canonicalize_url("http://Example.com/b/") == "http://example.com/b"


def test_clean_html():
    assert clean_html("<p>Hello &amp; <b>world</b></p>") == "Hello & world"


def test_detect_language():
    assert detect_language("这是一篇关于人工智能的中文文章") == "zh"
    assert detect_language("This is an English article about AI") == "en"


def test_normalize_builds_article():
    cand = ArticleCandidate(
        source_id="s1", source_name="S", category="media",
        url="https://example.com/x?utm_source=y", title="T", content="<p>Body</p>",
    )
    art = normalize(cand)
    assert art.canonical_url == "https://example.com/x"
    assert art.content == "Body"
    assert art.content_hash
    assert art.id


# --- dedup models --------------------------------------------------------- #

def test_content_hash_stable():
    assert content_hash("Hello   World") == content_hash("hello world")


def test_article_id_derives():
    art = Article(
        source_id="s", url="u", canonical_url="https://e.com/a", title="t",
        published_at=None, author=None, content="c", language="en", content_hash="",
    )
    assert art.id and art.content_hash


# --- LLM ------------------------------------------------------------------ #

def test_extract_json_plain():
    assert extract_json('{"a": 1}') == {"a": 1}


def test_extract_json_fenced():
    assert extract_json('```json\n{"a": 1}\n```') == {"a": 1}


def test_extract_json_embedded():
    assert extract_json('blah {"a": 2} trailing') == {"a": 2}


def test_extract_json_empty():
    with pytest.raises(SchemaError):
        extract_json("")


def test_validate_article_analysis_ok():
    data = validate({"is_ai_related": True, "topics": [], "confidence": 0.9}, ARTICLE_ANALYSIS, "article")
    assert data["is_ai_related"] is True


def test_validate_missing_required():
    with pytest.raises(SchemaError):
        validate({"topics": []}, ARTICLE_ANALYSIS, "article")


def test_validate_wrong_type():
    with pytest.raises(SchemaError):
        validate({"is_ai_related": "yes"}, ARTICLE_ANALYSIS, "article")


def test_prompt_registry_versions():
    assert get_prompt("classify_article").version == "article_classifier:v1"
    assert get_prompt("event_analysis").version == "event_analyzer:v1"


def test_router_task_order():
    assert providers_for_task("user_query")[0] == "chatgpt-web"
    assert providers_for_task("classify_article")[0] == "deepseek-web"


# --- ranking -------------------------------------------------------------- #

def test_freshness_decays():
    assert freshness_score(None) == 0.5
    # very old content decays toward (and may underflow to) zero
    assert 0 <= freshness_score("2020-01-01T00:00:00+00:00") < 0.01
    assert freshness_score("2026-10-09T05:00:00+00:00") > freshness_score("2026-10-01T00:00:00+00:00")


def test_novelty_decreases_with_sources():
    assert novelty_score(1) > novelty_score(3) > novelty_score(5)


def test_source_score_prefers_primary():
    assert source_score(["primary"]) > source_score(["discussion"])


def test_compute_base():
    assert compute_base(1.0, 1.0, 1.0, 1.0) == 1.0
    assert compute_base(0.5, 0.5, 0.5, 0.5) == 0.0625
