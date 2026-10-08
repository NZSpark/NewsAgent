"""Storage + dedup integration tests."""
from __future__ import annotations

import pytest

from newsagent.pipeline.dedup import is_duplicate
from newsagent.sources.models import Article, Source
from newsagent.storage.database import connect, init_db
from newsagent.storage.repository import ArticleRepository, EventRepository, PipelineRunRepository, SourceRepository
from newsagent.storage.models import Claim, Event


@pytest.fixture()
def conn(tmp_path):
    c = connect(tmp_path / "test.db")
    init_db(c)
    yield c
    c.close()


def _article(url="https://e.com/a", title="Hello AI", chash=""):
    return Article(
        source_id="s1", url=url, canonical_url=url, title=title,
        published_at=None, author=None, content="body", language="en",
        content_hash=chash,
    )


def test_init_db_idempotent(conn):
    init_db(conn)
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"sources", "articles", "events", "event_articles", "claims", "llm_runs", "pipeline_runs"} <= tables


def test_article_insert_and_dedup(conn):
    repo = ArticleRepository(conn)
    art = _article()
    assert repo.insert(art) is True
    assert repo.insert(art) is False  # duplicate ignored
    assert repo.get(art.id) is not None
    assert repo.find_by_url(art.canonical_url) is not None
    assert repo.find_by_hash(art.content_hash) is not None


def test_is_duplicate_by_canonical(conn):
    repo = ArticleRepository(conn)
    repo.insert(_article())
    other = _article(url="https://e.com/b", title="Totally different headline")
    other.canonical_url = "https://e.com/a"
    assert is_duplicate(other, repo) is True


def test_is_not_duplicate(conn):
    repo = ArticleRepository(conn)
    repo.insert(_article())
    other = _article(url="https://e.com/zzz", title="Another story entirely")
    other.content_hash = "unique-hash-xyz"
    assert is_duplicate(other, repo) is False


def test_source_repository(conn):
    repo = SourceRepository(conn)
    repo.upsert(Source(id="s1", name="S1", url="https://s1.com"))
    repo.mark_success("s1", "2026-10-09T00:00:00Z")
    repo.mark_error("s1")
    row = dict(repo.list_all()[0])
    assert row["last_success_at"]
    assert row["failure_count"] >= 1


def test_event_and_claims(conn):
    arepo = ArticleRepository(conn)
    erepo = EventRepository(conn)
    art = _article()
    arepo.insert(art)
    erepo.upsert(Event(event_id="e1", title="Big AI Event"))
    erepo.link_article("e1", art.id, "primary")
    erepo.add_claim(Claim(event_id="e1", claim="X happened", evidence_type="confirmed", source_urls=[art.url]))
    event = erepo.get("e1")
    assert event["title"] == "Big AI Event"
    assert len(erepo.articles_for("e1")) == 1
    claims = erepo.claims_for("e1")
    assert claims and claims[0]["claim"] == "X happened"


def test_pipeline_run_repo(conn):
    repo = PipelineRunRepository(conn)
    run = repo.start()
    run.status = "success"
    run.articles_new = 5
    repo.finish(run)
    history = repo.history()
    assert history and history[0]["articles_new"] == 5
