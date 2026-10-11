"""Terminal input/output adapter (CHAT-006).

Reads stdin line by line on a dedicated thread so it never blocks the WeChat
long-poll. EOF signals the session to shut down.
"""
from __future__ import annotations

import queue
import sys
import threading

from .models import ChatMessage


class ConsoleInput:
    """Line reader that pushes ChatMessages into a shared queue."""

    def __init__(self, out_queue: "queue.Queue", prompt: str = "you> ") -> None:
        self._queue = out_queue
        self._prompt = prompt
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="chat-console", daemon=True)
        self._thread.start()

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                if sys.stdin.isatty():
                    line = input(self._prompt)
                else:
                    line = sys.stdin.readline()
                    if line == "":
                        break
            except (EOFError, KeyboardInterrupt):
                break
            except Exception:  # noqa: BLE001 - never kill the thread silently
                break
            text = line.rstrip("\n")
            if not text.strip():
                continue
            self._queue.put(ChatMessage(source="console", text=text))
        # Signal end-of-input so the session can stop.
        self._queue.put(None)

    def stop(self) -> None:
        self._stop.set()


class ConsoleOutput:
    """Writes full results to stdout, preserving Chinese and multi-line text."""

    def write(self, text: str) -> None:
        print(text)
        sys.stdout.flush()
