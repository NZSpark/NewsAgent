"""Session orchestration and lifecycle (CHAT-007/011/012).

Wires input adapters -> shared queue -> serial dispatcher -> broadcaster.
Owns the STARTING/RUNNING/STOPPING/STOPPED state machine and idempotent
shutdown. Core logic is injectable for offline tests.
"""
from __future__ import annotations

import queue
import threading

from ..logging_setup import get_logger
from .broadcast import Broadcaster
from .chat import Conversation
from .commands import HELP_TEXT, is_command, run_command
from .models import ChatMessage, ChatOutput, SessionState

log = get_logger("chat.session")

_QUIT = "/quit"


class ChatSession:
    """Serial message processor shared by console and WeChat inputs."""

    def __init__(self, broadcaster: Broadcaster, conversation: Conversation | None = None,
                 input_queue: "queue.Queue | None" = None) -> None:
        self._broadcaster = broadcaster
        self._conversation = conversation or Conversation()
        self._queue: queue.Queue = input_queue or queue.Queue()
        self._stop_requested = threading.Event()
        self._stopped = threading.Event()
        self.state = SessionState.STARTING
        self.exit_code = 0

    # -- input side -- #

    def submit(self, message: ChatMessage) -> None:
        if self.state in (SessionState.STOPPING, SessionState.STOPPED):
            return
        self._queue.put(message)

    def request_stop(self) -> None:
        """Idempotent shutdown request (used by /quit, Ctrl-C, EOF)."""
        if self._stop_requested.is_set():
            return
        self._stop_requested.set()
        # Wake the dispatcher if it is blocked on get().
        self._queue.put(None)

    # -- processing -- #

    def _handle(self, message: ChatMessage) -> bool:
        """Process one message. Returns False to stop the session."""
        text = message.text.strip()
        if text.lower() == _QUIT:
            self._broadcaster.broadcast(ChatOutput("会话结束，再见。", source="session"))
            return False
        if is_command(text):
            name = text[1:].split(maxsplit=1)[0].lower() if text[1:].strip() else ""
            if name == "help":
                self._broadcaster.broadcast(ChatOutput(HELP_TEXT, source="session"))
                return True
            output = run_command(text)
        else:
            output = self._conversation.respond(message)
        self._broadcaster.broadcast(output)
        return True

    def run(self) -> int:
        """Block, processing messages until stop/quit/EOF."""
        self.state = SessionState.RUNNING
        log.info("chat session running")
        try:
            while not self._stop_requested.is_set():
                item = self._queue.get()
                if item is None:
                    break
                try:
                    if not self._handle(item):
                        break
                except Exception as exc:  # noqa: BLE001 - one message must not kill the loop
                    log.warning("message handling failed: %s", exc)
                    self._broadcaster.broadcast(
                        ChatOutput(f"处理消息时出错：{exc}", source="session", is_error=True)
                    )
        finally:
            self._shutdown()
        return self.exit_code

    def _shutdown(self) -> None:
        if self._stopped.is_set():
            return
        self.state = SessionState.STOPPING
        self._stop_requested.set()
        self.state = SessionState.STOPPED
        self._stopped.set()
        log.info("chat session stopped")
