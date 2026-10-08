"""Storage-level models (TASK-009/010)."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone


@dataclass
class Event:
    event_id: str
    title: str
    first_seen_at: str = ""
    last_seen_at: str = ""
    published_at: str | None = None
    importance_score: float = 0.0
    novelty_score: float = 0.0
    confidence_score: float = 0.0
    freshness_score: float = 0.0
    source_score: float = 0.0
    base_score: float = 0.0
    category: str | None = None
    entities: list[str] = field(default_factory=list)
    summary: str | None = None
    why_it_matters: str | None = None
    status: str = "active"


@dataclass
class Claim:
    event_id: str
    claim: str
    evidence_type: str  # confirmed | reported | discussed | inferred
    source_urls: list[str] = field(default_factory=list)


@dataclass
class PipelineRun:
    run_id: str
    started_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    finished_at: str | None = None
    status: str = "running"
    sources_total: int = 0
    sources_success: int = 0
    articles_seen: int = 0
    articles_new: int = 0
    errors: list[str] = field(default_factory=list)
