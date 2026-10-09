"""Provider health check (TASK-001) + health reporting (TASK-041)."""
from __future__ import annotations

import time

from ..config import get_config, provider_order
from ..logging_setup import get_logger
from .circuit import get_circuit
from .providers import make_client, model_for

log = get_logger("llm.health")

# Web-driven local providers can legitimately take 20s+ for a single turn.
# Health checks must use the same generous timeout as real calls, otherwise a
# slow-but-working provider is misreported as down.
DEFAULT_TIMEOUT = 60.0


def check_provider(provider: str, timeout: float | None = None) -> dict:
    timeout = timeout or max(get_config().llm_timeout, DEFAULT_TIMEOUT)
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
