"""Task-level provider routing (TASK-016)."""
from __future__ import annotations

from ..config import TASK_ROUTING, provider_order


def providers_for_task(task: str) -> list[str]:
    """Return ordered providers for a task, restricted to configured ones."""
    configured = set(provider_order())
    preferred = TASK_ROUTING.get(task)
    if preferred:
        chain = [p for p in preferred if p in configured]
    else:
        chain = provider_order()
    return chain or provider_order()
