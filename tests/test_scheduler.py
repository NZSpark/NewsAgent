"""Scheduler due-logic and backoff tests (TASK-038, TASK-039)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from newsagent.logging_setup import FetchError
from newsagent.sources.models import Source
from newsagent.sources.scheduler import due_sources, with_backoff


def test_due_when_no_state():
    s = Source(id="s", name="S", url="u", crawl_interval=60)
    assert due_sources([s], {}) == [s]


def test_not_due_within_interval():
    s = Source(id="s", name="S", url="u", crawl_interval=60)
    recent = datetime.now(timezone.utc).isoformat()
    assert due_sources([s], {"s": recent}) == []


def test_due_after_interval():
    s = Source(id="s", name="S", url="u", crawl_interval=60)
    old = (datetime.now(timezone.utc) - timedelta(minutes=90)).isoformat()
    assert due_sources([s], {"s": old}) == [s]


def test_backoff_succeeds_first_try():
    assert with_backoff(lambda: "ok", retries=2) == "ok"


def test_backoff_retries_then_succeeds():
    attempts = {"n": 0}

    def flaky():
        attempts["n"] += 1
        if attempts["n"] < 2:
            raise FetchError("429 too many requests")
        return "ok"

    assert with_backoff(flaky, retries=3) == "ok"
    assert attempts["n"] == 2


def test_backoff_non_transient_raises_immediately():
    attempts = {"n": 0}

    def hard_fail():
        attempts["n"] += 1
        raise FetchError("404 not found")

    with pytest.raises(FetchError):
        with_backoff(hard_fail, retries=3)
    assert attempts["n"] == 1
