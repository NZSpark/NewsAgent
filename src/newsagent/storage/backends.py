"""Storage backend abstraction (TASK-045).

The design says PostgreSQL/pgvector is only adopted once SQLite is a proven
bottleneck. To keep that option open without paying for it now, all repositories
are accessed through this tiny factory. Swapping to Postgres later means adding
one backend module and changing STORAGE_BACKEND.

Currently implemented: `sqlite` (default). `postgres` is a documented stub that
raises until implemented.
"""
from __future__ import annotations

from ..logging_setup import get_logger

log = get_logger("storage.backends")

BACKENDS = {"sqlite", "postgres"}


def get_backend(name: str = "sqlite") -> str:
    if name not in BACKENDS:
        raise ValueError(f"unknown storage backend: {name}")
    if name == "postgres":
        raise NotImplementedError(
            "PostgreSQL/pgvector backend is intentionally not implemented. "
            "SQLite has not been proven a bottleneck. See doc/tasks_chatgpt.md TASK-045."
        )
    return name
