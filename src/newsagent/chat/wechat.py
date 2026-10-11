"""WeChat (iLink) input/output adapters + sync cursor + dedup (CHAT-008/009/010).

Reuses `ILinkClient.get_updates()` / `send_text()` and the protected-state
helpers from `wechat.credentials`. No LLM or command logic lives here.

Protocol note: inbound field names (`msgs`, `from_user_id`, `context_token`,
`get_updates_buf`) mirror the existing `login.bind_recipient` code and the
reference adapter. They are exercised by mock tests; real-account verification
is tracked separately (CHAT-014).
"""
from __future__ import annotations

import queue
import threading
import time
from collections import deque

from ..logging_setup import get_logger
from ..wechat.client import AuthExpiredError, PermanentSendError, WeChatError
from ..wechat.config import config_dir
from ..wechat.credentials import write_protected
from .broadcast import ChannelStatus
from .models import ChatMessage

log = get_logger("chat.wechat")

SYNC_FILE = "chat_sync.json"
_DEDUP_MAX = 500


class SyncCursor:
    """Persisted sync cursor + bounded recent-message-id dedup (CHAT-009)."""

    def __init__(self, path=None) -> None:
        self._path = path or (config_dir() / SYNC_FILE)
        self.buf = ""
        self._seen: deque[str] = deque(maxlen=_DEDUP_MAX)
        self._seen_set: set[str] = set()
        self._load()

    def _load(self) -> None:
        from ..wechat.credentials import _read_json  # local import: same package
        try:
            data = _read_json(self._path)
        except Exception as exc:  # noqa: BLE001 - corrupt file must be explicit
            raise WeChatError(f"chat sync state unreadable: {exc}") from exc
        self.buf = str(data.get("get_updates_buf") or "")

    def update(self, new_buf: str | None) -> None:
        if not new_buf or new_buf == self.buf:
            return
        self.buf = str(new_buf)
        try:
            write_protected(self._path, {"get_updates_buf": self.buf})
        except Exception as exc:  # noqa: BLE001
            log.warning("failed to persist chat sync cursor: %s", exc)

    def is_duplicate(self, message_id: str | None) -> bool:
        if not message_id:
            return False
        if message_id in self._seen_set:
            return True
        if len(self._seen) == self._seen.maxlen:
            self._seen_set.discard(self._seen[0])
        self._seen.append(message_id)
        self._seen_set.add(message_id)
        return False


def parse_updates(resp: dict, *, account_id: str, recipient_id: str) -> list[ChatMessage]:
    """Convert an iLink getupdates response into ChatMessages.

    Only text messages from the bound recipient are accepted; the bot's own
    messages, group/unbound senders, and non-text items are ignored.
    """
    out: list[ChatMessage] = []
    for msg in resp.get("msgs") or []:
        sender = str(msg.get("from_user_id") or "").strip()
        if not sender or sender == account_id or (recipient_id and sender != recipient_id):
            continue
        text = _extract_text(msg)
        if not text:
            continue
        out.append(
            ChatMessage(
                source="wechat",
                text=text,
                message_id=str(msg.get("message_id") or msg.get("client_id") or "") or None,
                sender_id=sender,
                context_token=str(msg.get("context_token") or "").strip() or None,
            )
        )
    return out


def _extract_text(msg: dict) -> str:
    for item in msg.get("item_list") or []:
        if item.get("type") == 1:  # ITEM_TEXT
            text = (item.get("text_item") or {}).get("text")
            if text:
                return str(text)
    return ""


class WeChatInput:
    """Long-poll thread that pushes parsed messages into the shared queue."""

    def __init__(self, client, account_id: str, recipient_id: str, out_queue: "queue.Queue",
                 cursor: SyncCursor, *, poll_timeout_ms: int = 35_000,
                 max_backoff: float = 30.0, stop_event: threading.Event | None = None) -> None:
        self._client = client
        self._account_id = account_id
        self._recipient_id = recipient_id
        self._queue = out_queue
        self._cursor = cursor
        self._poll_timeout_ms = poll_timeout_ms
        self._max_backoff = max_backoff
        self._stop = stop_event or threading.Event()
        self._thread: threading.Thread | None = None
        self.fatal_error: str | None = None

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="chat-wechat", daemon=True)
        self._thread.start()

    def _run(self) -> None:
        backoff = 1.0
        while not self._stop.is_set():
            try:
                resp = self._client.get_updates(self._cursor.buf, timeout_ms=self._poll_timeout_ms)
                backoff = 1.0
            except AuthExpiredError as exc:
                self.fatal_error = f"微信登录失效：{exc}"
                log.warning("wechat auth expired: %s", exc)
                break
            except WeChatError as exc:
                log.warning("wechat poll error: %s", exc)
                time.sleep(backoff)
                backoff = min(backoff * 2, self._max_backoff)
                continue

            # Persist the cursor before enqueueing so restart resumes near here.
            self._cursor.update(resp.get("get_updates_buf"))
            for message in parse_updates(resp, account_id=self._account_id, recipient_id=self._recipient_id):
                if self._cursor.is_duplicate(message.message_id):
                    continue
                self._queue.put(message)

    def stop(self) -> None:
        self._stop.set()

    def join(self, timeout: float | None = None) -> None:
        if self._thread is not None:
            self._thread.join(timeout=timeout)


class WeChatOutput:
    """Sends broadcaster output via iLink, tracking the live context_token."""

    def __init__(self, client, recipient: dict) -> None:
        self._client = client
        self._recipient = dict(recipient)

    def set_context(self, context_token: str | None) -> None:
        if context_token:
            self._recipient["context_token"] = context_token

    def send(self, text: str) -> ChannelStatus:
        try:
            result = self._client.send_text(self._recipient, text)
        except PermanentSendError as exc:
            return ChannelStatus("wechat", False, detail=str(exc))
        except WeChatError as exc:
            return ChannelStatus("wechat", False, detail=str(exc))
        return ChannelStatus("wechat", bool(result.ok), detail=result.detail)
