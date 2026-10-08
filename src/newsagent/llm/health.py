"""Provider health check (TASK-001) + health reporting (TASK-041)."""
from __future__ import annotations

import time

from ..config import provider_order
from ..logging_setup import get_logger
from .circuit import get_circuit
from .providers import make_client, model_for

log = get_logger("llm.health")


def check_provider(provider: str, timeout: float = 10.0) -> dict:
    t0 = time.time()
    try:
        client = make_client(provider)
        resp = client.chat.completions.create(
            model=model_for(provider),
            messages=[{"role": "user", "content": "ping"}],
            max_tokens=5,
            timeout=timeout,
        )
        ok = bool(resp.choices)
        return {
            "provider": provider,
            "healthy": ok,
            "latency_ms": int((time.time() - t0) * 1000),
            "circuit": get_circuit(provider).state,
        }
    except Exception as exc:  # noqa: BLE001
        log.warning("provider %s health check failed: %s", provider, exc)
        return {
            "provider": provider,
            "healthy": False,
            "latency_ms": int((time.time() - t0) * 1000),
            "error": type(exc).__name__,
            "circuit": get_circuit(provider).state,
        }


def check_all() -> list[dict]:
    return [check_provider(p) for p in provider_order()]
