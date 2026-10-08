"""LLM usage logging (TASK-022)."""
from __future__ import annotations

import sqlite3
import uuid
from datetime import datetime, timezone

from ..logging_setup import get_logger

log = get_logger("llm.usage")


def record(
    conn: sqlite3.Connection | None,
    task_type: str,
    provider: str,
    model: str,
    prompt_version: str,
    started_at: datetime,
    success: bool,
    latency_ms: int,
    input_tokens: int | None = None,
    output_tokens: int | None = None,
    error_type: str | None = None,
    fallback_from: str | None = None,
) -> None:
    log.info(
        "llm task=%s provider=%s success=%s latency=%dms fallback_from=%s error=%s",
        task_type, provider, success, latency_ms, fallback_from, error_type,
    )
    if conn is None:
        return
    try:
        conn.execute(
            """INSERT INTO llm_runs (run_id, task_type, provider, model, prompt_version,
               started_at, finished_at, input_tokens, output_tokens, latency_ms, success,
               error_type, fallback_from) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                str(uuid.uuid4()), task_type, provider, model, prompt_version,
                started_at.isoformat(), datetime.now(timezone.utc).isoformat(),
                input_tokens, output_tokens, latency_ms, int(success), error_type, fallback_from,
            ),
        )
        conn.commit()
    except sqlite3.Error as exc:  # logging must never break the pipeline
        log.debug("failed to record llm_run: %s", exc)
