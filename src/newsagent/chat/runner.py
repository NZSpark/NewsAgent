"""Real-wiring entry point for `news chat` (CHAT-007/008/010/011/012).

Kept separate from session/broadcast so those remain trivially testable with
fakes. This module owns config checks, thread startup, and resource cleanup.
"""
from __future__ import annotations

import queue
import signal
import threading

from ..logging_setup import get_logger
from .broadcast import Broadcaster
from .console import ConsoleInput, ConsoleOutput
from .session import ChatSession

log = get_logger("chat.runner")


def _build_wechat(queue_: "queue.Queue", stop_event: threading.Event):
    """Return (wechat_input, wechat_output, client) or (None, None, None)."""
    from ..wechat.client import WeChatError
    from ..wechat.credentials import load_account, load_recipient, is_bound
    from ..wechat.ilink import ILinkClient
    from .wechat import SyncCursor, WeChatInput, WeChatOutput

    if not is_bound():
        print("微信未绑定，跳过微信通道（仅本地模式）。如需启用：news wechat login && news wechat bind")
        return None, None, None
    account = load_account()
    if not account:
        print("微信未登录，跳过微信通道（仅本地模式）。")
        return None, None, None
    try:
        client = ILinkClient(account)
    except WeChatError as exc:
        print(f"微信客户端初始化失败：{exc}（仅本地模式）")
        return None, None, None

    recipient = load_recipient()
    account_id = str(account.get("account_id") or getattr(client, "account_id", ""))
    recipient_id = str(recipient.get("id") or "")
    cursor = SyncCursor()
    win = WeChatInput(client, account_id, recipient_id, queue_, cursor, stop_event=stop_event)
    wout = WeChatOutput(client, recipient)
    return win, wout, client


def run_chat(*, no_wechat: bool = False) -> int:
    """Start the session. Returns a process exit code."""
    input_queue: "queue.Queue" = queue.Queue()
    stop_event = threading.Event()

    wechat_in = wechat_out = client = None
    if not no_wechat:
        try:
            wechat_in, wechat_out, client = _build_wechat(input_queue, stop_event)
        except Exception as exc:  # noqa: BLE001 - degrade to local-only
            print(f"微信通道不可用：{exc}（仅本地模式）")
            wechat_in = wechat_out = client = None

    broadcaster = Broadcaster(ConsoleOutput(), wechat_out)
    session = ChatSession(broadcaster, input_queue=input_queue)

    console_in = ConsoleInput(input_queue)

    # Ctrl-C routes to the same idempotent shutdown path.
    def _on_sigint(signum, frame):  # noqa: ANN001, ARG001
        session.request_stop()
        stop_event.set()
        console_in.stop()

    previous = signal.getsignal(signal.SIGINT)
    signal.signal(signal.SIGINT, _on_sigint)

    try:
        if wechat_in is not None:
            wechat_in.start()
        console_in.start()
        print("News Chat 已启动。输入消息或 /help 查看命令，/quit 退出。")
        rc = session.run()
        if wechat_in is not None and getattr(wechat_in, "fatal_error", None):
            broadcaster.broadcast(__import__("newsagent.chat.models", fromlist=["ChatOutput"]).ChatOutput(
                wechat_in.fatal_error, source="session", is_error=True))
        return rc
    finally:
        session.request_stop()
        stop_event.set()
        console_in.stop()
        if wechat_in is not None:
            wechat_in.stop()
            wechat_in.join(timeout=3)
        if client is not None:
            try:
                client.close()
            except Exception:  # noqa: BLE001
                pass
        try:
            signal.signal(signal.SIGINT, previous)
        except (ValueError, TypeError):
            pass
