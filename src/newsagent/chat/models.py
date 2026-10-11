"""Chat message / output / state contracts (CHAT-001).

These are the stable interfaces between input adapters, the session
controller, and output adapters. Core session logic must not depend on a real
terminal or a real WeChat connection.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Literal

MessageSource = Literal["console", "wechat"]

# Hard limits so a single message/response cannot grow unbounded.
MAX_MESSAGE_CHARS = 8000


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class ChatMessage:
    """A normalized inbound message from any input adapter.

    `context_token` and other credentials must never be written to ordinary
    logs or included in generic error objects.
    """

    source: MessageSource
    text: str
    message_id: str | None = None
    sender_id: str | None = None
    context_token: str | None = None
    received_at: str = field(default_factory=_utcnow)

    def __post_init__(self) -> None:
        if self.source not in ("console", "wechat"):
            raise ValueError(f"unsupported message source: {self.source!r}")


@dataclass(frozen=True)
class ChatOutput:
    """A single result to be broadcast to all output channels."""

    text: str
    source: str = "chat"  # origin: chat | command | session (for logs)
    is_error: bool = False


class SessionState(str, Enum):
    STARTING = "STARTING"
    RUNNING = "RUNNING"
    STOPPING = "STOPPING"
    STOPPED = "STOPPED"
