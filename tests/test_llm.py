"""LLM client retry / fallback / circuit breaker tests (mocked providers)."""
from __future__ import annotations

import pytest

from newsagent.llm import client as llm_client
from newsagent.llm.circuit import FAILURE_THRESHOLD, get_circuit, reset_circuits
from newsagent.logging_setup import LLMError, SchemaError


@pytest.fixture(autouse=True)
def _reset():
    reset_circuits()
    yield
    reset_circuits()


def test_generate_uses_first_provider(monkeypatch):
    calls = []

    def fake(provider, messages, model):
        calls.append(provider)
        return '{"is_ai_related": true, "topics": [], "confidence": 0.9}', {}

    monkeypatch.setattr(llm_client, "_call_once", fake)
    out = llm_client.generate("classify_article", [{"role": "user", "content": "x"}],
                              schema={"is_ai_related": (True, bool)})
    assert out["is_ai_related"] is True
    assert calls == ["deepseek-web"]


def test_generate_falls_back_on_failure(monkeypatch):
    calls = []

    def fake(provider, messages, model):
        calls.append(provider)
        if provider == "deepseek-web":
            raise RuntimeError("upstream 502")
        return '{"is_ai_related": true, "topics": [], "confidence": 0.8}', {}

    monkeypatch.setattr(llm_client, "_call_once", fake)
    out = llm_client.generate("classify_article", [{"role": "user", "content": "x"}],
                              schema={"is_ai_related": (True, bool)})
    assert out["is_ai_related"] is True
    # deepseek retried once (max_retries=1) then gemini succeeded
    assert calls[0] == "deepseek-web" and calls[-1] == "gemini-web"


def test_generate_all_fail_raises(monkeypatch):
    def fake(provider, messages, model):
        raise RuntimeError("down")

    monkeypatch.setattr(llm_client, "_call_once", fake)
    with pytest.raises(LLMError):
        llm_client.generate("classify_article", [{"role": "user", "content": "x"}])


def test_generate_invalid_json_falls_back(monkeypatch):
    calls = []

    def fake(provider, messages, model):
        calls.append(provider)
        if provider == "deepseek-web":
            return "not json at all", {}
        return '{"summary": "ok"}', {}

    monkeypatch.setattr(llm_client, "_call_once", fake)
    out = llm_client.generate("summarize_article", [{"role": "user", "content": "x"}],
                              schema={"summary": (True, str)})
    assert out["summary"] == "ok"


def test_circuit_opens_after_threshold():
    c = get_circuit("deepseek-web")
    for _ in range(FAILURE_THRESHOLD):
        c.on_failure()
    assert c.state == "OPEN"
    assert c.allow() is False
    c.on_success()
    assert c.state == "CLOSED"


def test_allow_prose_accepts_non_json(monkeypatch):
    def fake(provider, messages, model):
        return "这是一段中文回答，不是 JSON。", {}

    monkeypatch.setattr(llm_client, "_call_once", fake)
    out = llm_client.generate(
        "user_query", [{"role": "user", "content": "x"}],
        schema={"answer": (True, str)}, allow_prose=True,
    )
    assert out["text"].startswith("这是一段中文回答")


def test_prose_rejected_without_flag(monkeypatch):
    def fake(provider, messages, model):
        return "plain prose no json", {}

    monkeypatch.setattr(llm_client, "_call_once", fake)
    with pytest.raises(LLMError):
        llm_client.generate("classify_article", [{"role": "user", "content": "x"}])


def test_json_mode_false_returns_text(monkeypatch):
    def fake(provider, messages, model):
        return "raw text", {}

    monkeypatch.setattr(llm_client, "_call_once", fake)
    out = llm_client.generate("user_query", [{"role": "user", "content": "x"}], json_mode=False)
    assert out["text"] == "raw text"


def test_schema_error_on_bad_shape(monkeypatch):
    def fake(provider, messages, model):
        return '{"is_ai_related": "yes"}', {}

    monkeypatch.setattr(llm_client, "_call_once", fake)
    with pytest.raises(LLMError):
        llm_client.generate("classify_article", [{"role": "user", "content": "x"}],
                            schema={"is_ai_related": (True, bool)})
