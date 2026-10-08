"""P7 tests: semantic cluster, research, report, vector search, backend (TASK-026/042/043/044/045)."""
from __future__ import annotations

import pytest

from newsagent.agents.report import build_report
from newsagent.pipeline import semantic_cluster as sc
from newsagent.storage import vector
from newsagent.storage.backends import get_backend
from newsagent.storage.database import connect, init_db
from newsagent.storage.models import Event
from newsagent.storage.repository import EventRepository


@pytest.fixture()
def conn(tmp_path):
    c = connect(tmp_path / "p7.db")
    init_db(c)
    yield c
    c.close()


# --- vector (TASK-044) ---------------------------------------------------- #

def test_embed_is_normalized():
    v = vector.embed("OpenAI releases a new model")
    assert len(v) == vector.DIM
    norm = sum(x * x for x in v) ** 0.5
    assert abs(norm - 1.0) < 1e-6


def test_embed_deterministic():
    assert vector.embed("hello world") == vector.embed("hello world")


def test_cosine_similarity():
    a = vector.embed("OpenAI GPT model release")
    b = vector.embed("OpenAI GPT model release")
    c = vector.embed("weather forecast tomorrow")
    assert vector.cosine(a, b) > vector.cosine(a, c)


def test_index_and_semantic_search(conn):
    repo = EventRepository(conn)
    repo.upsert(Event(event_id="e1", title="OpenAI releases GPT-5", summary="new model"))
    repo.upsert(Event(event_id="e2", title="Local weather report", summary="rain"))
    n = vector.index_events(conn)
    assert n == 2
    results = vector.semantic_search(conn, "GPT-5 model release")
    assert results
    assert results[0]["event_id"] == "e1"


# --- semantic cluster (TASK-026) ------------------------------------------ #

def test_semantic_cluster_skips_when_few(conn, monkeypatch):
    out = sc.semantic_cluster(hours=48, min_events=10)
    assert out["skipped"] is True


def test_semantic_cluster_merges(conn, monkeypatch):
    repo = EventRepository(conn)
    for i in range(5):
        repo.upsert(Event(event_id=f"e{i}", title=f"Event {i}"))

    def fake_generate(task, messages, schema=None, **kwargs):
        return {"groups": [{"event_ids": ["e0", "e1"]}]}

    monkeypatch.setattr(sc, "generate", fake_generate)
    out = sc.semantic_cluster(hours=48, min_events=3, conn=conn)
    assert out["merged"] == 1
    assert repo.get("e0") is not None
    assert repo.get("e1") is None


def test_semantic_cluster_rejects_hallucinated_ids(conn, monkeypatch):
    repo = EventRepository(conn)
    for i in range(5):
        repo.upsert(Event(event_id=f"e{i}", title=f"Event {i}"))

    def fake_generate(task, messages, schema=None, **kwargs):
        return {"groups": [{"event_ids": ["e0", "does-not-exist"]}]}

    monkeypatch.setattr(sc, "generate", fake_generate)
    out = sc.semantic_cluster(hours=48, min_events=3, conn=conn)
    assert out["merged"] == 0
    assert repo.get("e0") is not None


# --- report (TASK-043) ---------------------------------------------------- #

def test_build_report_empty(conn, monkeypatch):
    monkeypatch.setattr("newsagent.agents.report.connect", lambda *a, **k: conn)
    report = build_report(hours=24)
    assert "AI 日报" in report
    assert "暂无事件" in report


def test_build_report_with_events(conn, monkeypatch):
    repo = EventRepository(conn)
    repo.upsert(Event(event_id="e1", title="Big AI Release", summary="something happened"))
    monkeypatch.setattr("newsagent.agents.report.connect", lambda *a, **k: conn)
    report = build_report(hours=24)
    assert "Big AI Release" in report
    assert "头条" in report


# --- backend (TASK-045) --------------------------------------------------- #

def test_sqlite_backend_ok():
    assert get_backend("sqlite") == "sqlite"


def test_postgres_backend_not_implemented():
    with pytest.raises(NotImplementedError):
        get_backend("postgres")


def test_unknown_backend_rejected():
    with pytest.raises(ValueError):
        get_backend("mysql")
