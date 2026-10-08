"""Unified LLM client: retry + fallback + concurrency + circuit breaker.

TASK-015 (client), TASK-017 (retry/fallback), TASK-018 (concurrency),
TASK-019 (circuit breaker).

Business code calls llm.generate(task=..., messages=...) and never touches
base URLs or the OpenAI client directly.
"""
from __future__ import annotations

import json
import re
import sqlite3
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

from ..config import get_config
from ..logging_setup import LLMError, SchemaError, get_logger
from . import usage
from .circuit import get_circuit
from .providers import make_client, model_for
from .router import providers_for_task
from .schemas import validate

log = get_logger("llm.client")

_JSON_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)
_executor: ThreadPoolExecutor | None = None
_executor_lock = threading.Lock()


def _get_executor() -> ThreadPoolExecutor:
    global _executor
    with _executor_lock:
        if _executor is None:
            _executor = ThreadPoolExecutor(max_workers=get_config().llm_concurrency)
    return _executor


def extract_json(text: str) -> dict:
    """Extract the first JSON object from a model response."""
    if not text:
        raise SchemaError("empty LLM response")
    text = text.strip()
    fence = _JSON_FENCE_RE.search(text)
    if fence:
        text = fence.group(1).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        try:
            return json.loads(text[start : end + 1])
        except json.JSONDecodeError as exc:
            raise SchemaError(f"cannot parse JSON: {exc}") from exc
    raise SchemaError("no JSON object found in response")


def _call_once(provider: str, messages: list[dict], model: str) -> tuple[str, dict]:
    client = make_client(provider)
    resp = client.chat.completions.create(
        model=model,
        messages=messages,  # only system/user/assistant
        temperature=0.2,
        # NOTE: no `reasoning_effort`, no `developer` role (local providers).
    )
    content = resp.choices[0].message.content or ""
    meta: dict = {}
    usage_obj = getattr(resp, "usage", None)
    if usage_obj is not None:
        meta["input_tokens"] = getattr(usage_obj, "prompt_tokens", None)
        meta["output_tokens"] = getattr(usage_obj, "completion_tokens", None)
    return content, meta


def generate(
    task: str,
    messages: list[dict],
    schema: dict | None = None,
    provider: str | None = None,
    prompt_version: str = "",
    conn: sqlite3.Connection | None = None,
    json_mode: bool = True,
) -> dict:
    """Generate a JSON object using task-routed providers with retry/fallback."""
    cfg = get_config()
    chain = [provider] if provider else providers_for_task(task)
    last_error: Exception | None = None
    fallback_from: str | None = None

    for candidate in chain:
        circuit = get_circuit(candidate)
        if not circuit.allow():
            log.info("provider %s circuit is %s, skipping", candidate, circuit.state)
            fallback_from = fallback_from or candidate
            continue

        model = model_for(candidate)
        for attempt in range(cfg.llm_max_retries + 1):
            started = datetime.now(timezone.utc)
            t0 = time.time()
            try:
                raw, meta = _call_once(candidate, messages, model)
                data = extract_json(raw) if json_mode else {"text": raw}
                if schema is not None and json_mode:
                    data = validate(data, schema, name=task)
                circuit.on_success()
                usage.record(
                    conn, task, candidate, model, prompt_version, started, True,
                    int((time.time() - t0) * 1000), meta.get("input_tokens"),
                    meta.get("output_tokens"), fallback_from=fallback_from,
                )
                return data
            except Exception as exc:  # noqa: BLE001 - retry/fallback boundary
                last_error = exc
                usage.record(
                    conn, task, candidate, model, prompt_version, started, False,
                    int((time.time() - t0) * 1000), error_type=type(exc).__name__,
                    fallback_from=fallback_from,
                )
                log.warning("provider %s attempt %d failed: %s", candidate, attempt, exc)
                if attempt < cfg.llm_max_retries:
                    time.sleep(0.5)
        circuit.on_failure()
        fallback_from = fallback_from or candidate

    raise LLMError(f"all providers failed for task '{task}': {last_error}")


def generate_async(task: str, messages: list[dict], **kwargs):
    """Submit to the shared thread pool (TASK-018 concurrency control)."""
    return _get_executor().submit(generate, task, messages, **kwargs)


def shutdown() -> None:
    global _executor
    with _executor_lock:
        if _executor is not None:
            _executor.shutdown(wait=False)
            _executor = None
