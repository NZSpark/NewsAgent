"""Dual-channel output broadcaster (CHAT-002).

A single result is delivered once to each connected output channel. One
channel failing must not prevent the other, and must never trigger re-running
the originating command.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from ..logging_setup import get_logger
from .models import ChatOutput

log = get_logger("chat.broadcast")

# Match the iLink single-message limit (ilink.MAX_TEXT_LEN = 1800).
WECHAT_CHUNK = 1800


def split_for_wechat(text: str, limit: int = WECHAT_CHUNK) -> list[str]:
    """Split text into ordered chunks <= limit, preferring line boundaries."""
    if len(text) <= limit:
        return [text]
    chunks: list[str] = []
    remaining = text
    while len(remaining) > limit:
        cut = remaining.rfind("\n", 0, limit)
        if cut < limit // 2:
            cut = limit
        chunks.append(remaining[:cut])
        remaining = remaining[cut:].lstrip("\n")
    if remaining:
        chunks.append(remaining)
    return chunks


class ConsoleSink(Protocol):
    def write(self, text: str) -> None: ...


class WeChatSink(Protocol):
    def send(self, text: str) -> "ChannelStatus": ...


@dataclass
class ChannelStatus:
    channel: str
    ok: bool
    detail: str = ""
    chunks: int = 0


@dataclass
class BroadcastResult:
    console: ChannelStatus | None = None
    wechat: ChannelStatus | None = None

    def to_dict(self) -> dict:
        return {
            "console": self.console.__dict__ if self.console else None,
            "wechat": self.wechat.__dict__ if self.wechat else None,
        }


class Broadcaster:
    def __init__(self, console: ConsoleSink | None, wechat: WeChatSink | None) -> None:
        self._console = console
        self._wechat = wechat

    def broadcast(self, output: ChatOutput) -> BroadcastResult:
        text = output.text if output.text.strip() else "（无输出）"
        result = BroadcastResult()
        if self._console is not None:
            try:
                self._console.write(text)
                result.console = ChannelStatus("console", True, chunks=1)
            except Exception as exc:  # noqa: BLE001 - channel isolation
                log.warning("console output failed: %s", exc)
                result.console = ChannelStatus("console", False, detail=str(exc))
        if self._wechat is not None:
            chunks = split_for_wechat(text)
            sent = 0
            try:
                for chunk in chunks:
                    status = self._wechat.send(chunk)
                    if not status.ok:
                        result.wechat = ChannelStatus("wechat", False, detail=status.detail, chunks=sent)
                        break
                    sent += 1
                else:
                    result.wechat = ChannelStatus("wechat", True, chunks=sent)
            except Exception as exc:  # noqa: BLE001 - channel isolation
                log.warning("wechat output failed: %s", exc)
                result.wechat = ChannelStatus("wechat", False, detail=str(exc), chunks=sent)
        return result
