"""iLink Bot client interface (TASK-066 ~ TASK-070).

The concrete iLink protocol implementation is BLOCKED on protocol
verification (doc/wechat_phase0_survey.md). This module defines the adapter
boundary so the rest of the code is testable with a mock client.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from ..logging_setup import NewsAgentError, get_logger

log = get_logger("wechat.client")

# TASK-069: limits must come from verified protocol; placeholders marked.
# UNVERIFIED: these values are NOT confirmed against a real iLink Bot.
MAX_TEXT_LEN = 2000


class WeChatError(NewsAgentError):
    """Base WeChat send error."""


class RecoverableSendError(WeChatError):
    """Network/timeout/temporary server error: eligible for retry (TASK-089)."""


class PermanentSendError(WeChatError):
    """Auth failure, bad input, unbound recipient: do NOT retry (TASK-089)."""


class AuthExpiredError(PermanentSendError):
    """Credentials invalid/expired: re-login required (TASK-091)."""


class NotBoundError(PermanentSendError):
    """Recipient not bound / context invalid (TASK-092)."""


@dataclass(frozen=True)
class SendResult:
    ok: bool
    message_id: str | None = None
    detail: str = ""


class WeChatClient(Protocol):
    """The adapter boundary the sender depends on."""

    def send_text(self, recipient: dict, text: str) -> SendResult: ...

    def send_file(self, recipient: dict, path: Path, caption: str = "") -> SendResult: ...


class UnavailableClient:
    """TASK-018 placeholder: real client not implemented (protocol blocked).

    Any send attempt fails loudly; it never pretends to succeed (TASK-070).
    """

    def send_text(self, recipient: dict, text: str) -> SendResult:  # noqa: D102
        raise PermanentSendError(
            "real iLink Bot client is not implemented: protocol verification "
            "is blocked (see doc/wechat_phase0_survey.md)"
        )

    def send_file(self, recipient: dict, path: Path, caption: str = "") -> SendResult:  # noqa: D102
        raise PermanentSendError(
            "real iLink Bot client is not implemented: protocol verification "
            "is blocked (see doc/wechat_phase0_survey.md)"
        )


def split_text(text: str, limit: int = MAX_TEXT_LEN) -> list[str]:
    """TASK-075/TASK-076: split on paragraph/line boundaries, never mid-UTF-8.

    Python strings are code points, so slicing never breaks a UTF-8 sequence.
    """
    text = text.strip()
    if not text:
        return []
    if len(text) <= limit:
        return [text]

    chunks: list[str] = []
    current = ""
    for para in text.split("\n"):
        block = para if not current else "\n" + para
        if len(current) + len(block) <= limit:
            current += block
            continue
        if current:
            chunks.append(current)
        # a single paragraph longer than the limit: hard-split it
        if len(para) > limit:
            for i in range(0, len(para), limit):
                piece = para[i : i + limit]
                if len(piece) == limit or i + limit >= len(para):
                    chunks.append(piece)
                else:
                    chunks.append(piece)
            current = ""
        else:
            current = para
    if current:
        chunks.append(current)
    return [c for c in chunks if c]


def number_chunks(chunks: list[str], title: str = "") -> list[str]:
    """TASK-077: label segments so order is clear."""
    total = len(chunks)
    if total <= 1:
        return chunks
    out: list[str] = []
    for i, chunk in enumerate(chunks, 1):
        head = f"（{i}/{total}）"
        if i == 1 and title:
            head = f"{title} {head}"
        out.append(f"{head}\n{chunk}")
    return out
