"""Offline tests for News Chat (CHAT-001..CHAT-013). No real network/LLM."""
from __future__ import annotations

import queue

import pytest

from newsagent.chat.broadcast import Broadcaster, split_for_wechat
from newsagent.chat.chat import Conversation
from newsagent.chat.commands import ALLOWED, is_command, parse, run_command
from newsagent.chat.models import ChatMessage, ChatOutput
from newsagent.chat.session import ChatSession
from newsagent.chat.wechat import SyncCursor, parse_updates


# --- models --------------------------------------------------------------- #

def test_message_rejects_bad_source():
    with pytest.raises(ValueError):
        ChatMessage(source="telegram", text="hi")


def test_empty_text_is_ok_at_model_level():
    assert ChatMessage(source="console", text="").text == ""


# --- command parsing (CHAT-004) ------------------------------------------- #

def test_is_command():
    assert is_command("  /help")
    assert not is_command("hello")


def test_parse_quoted_args():
    name, tokens = parse('/search "人工智能 芯片" --limit 5')
    assert name == "search"
    assert tokens == ["人工智能 芯片", "--limit", "5"]


def test_parse_lowercases_name():
    name, _ = parse("/Recent --hours 48")
    assert name == "recent"


def test_unknown_command_refused():
    out = run_command("/definitely-not-real")
    assert out.is_error and "未知命令" in out.text


def test_denied_subcommands_refused():
    for bad in ("/wechat", "/chat"):
        out = run_command(bad)
        assert out.is_error


def test_report_rejects_send_wechat():
    out = run_command("/report --send-wechat")
    assert out.is_error and "--send-wechat" in out.text


def test_allowlist_matches_expected():
    assert {"recent", "search", "ask", "fetch", "pipeline", "report"} <= ALLOWED
    assert "wechat" not in ALLOWED
    assert "chat" not in ALLOWED


def test_command_bad_quoting_is_readable_error():
    out = run_command('/search "unterminated')
    assert out.is_error


def test_command_help_is_builtin_not_dispatched():
    # /help is handled by the session; run_command would reject it as unknown.
    out = run_command("/help")
    assert out.is_error  # not in ALLOWED, so it falls through to unknown


# --- command dispatch (CHAT-005) ------------------------------------------ #

def test_recent_command_dispatches(monkeypatch):
    called = {}

    def fake_recent(args):
        called["hours"] = args.hours
        print("[{\"title\": \"x\"}]")
        return 0

    import newsagent.cli.main as main
    monkeypatch.setattr(main, "cmd_recent", fake_recent)
    out = run_command("/recent --hours 48")
    assert not out.is_error
    assert called["hours"] == 48
    assert "title" in out.text


def test_command_exception_becomes_error_output(monkeypatch):
    def boom(args):
        raise RuntimeError("kaboom")

    import newsagent.cli.main as main
    monkeypatch.setattr(main, "cmd_health", boom)
    out = run_command("/health")
    assert out.is_error and "kaboom" in out.text


def test_command_no_subprocess_shell():
    import inspect
    import newsagent.chat.commands as c
    # Check executable lines only (docstring mentions the forbidden pattern).
    src = inspect.getsource(c)
    code_lines = [
        ln for ln in src.splitlines()
        if ln.strip() and not ln.strip().startswith("#")
    ]
    joined = "\n".join(code_lines)
    assert "subprocess" not in joined.split('"""')[-1]
    assert "os.system" not in joined


# --- conversation (CHAT-003) ---------------------------------------------- #

def test_conversation_calls_llm_once(monkeypatch):
    calls = []

    def fake_generate(**kwargs):
        calls.append(kwargs)
        return {"text": "你好，我是助手。"}

    import newsagent.chat.chat as chat
    monkeypatch.setattr(chat, "generate", fake_generate)
    conv = Conversation()
    out = conv.respond(ChatMessage(source="console", text="你好"))
    assert out.text == "你好，我是助手。"
    assert len(calls) == 1
    assert calls[0]["task"] == "user_query"


def test_conversation_history_is_bounded(monkeypatch):
    monkeypatch.setattr("newsagent.chat.chat.generate", lambda **k: {"text": "ok"})
    conv = Conversation(max_turns=2)
    for i in range(5):
        conv.respond(ChatMessage(source="console", text=f"m{i}"))
    assert len(conv._history) <= 4  # 2 turns * 2 messages


def test_conversation_survives_llm_failure(monkeypatch):
    from newsagent.logging_setup import LLMError

    def boom(**k):
        raise LLMError("down")

    monkeypatch.setattr("newsagent.chat.chat.generate", boom)
    out = Conversation().respond(ChatMessage(source="console", text="hi"))
    assert out.is_error


# --- broadcast (CHAT-002) ------------------------------------------------- #

class _Console:
    def __init__(self): self.lines = []
    def write(self, text): self.lines.append(text)


class _WeChat:
    def __init__(self, ok=True):
        self.ok = ok
        self.sent = []

    def send(self, text):
        from newsagent.chat.broadcast import ChannelStatus
        self.sent.append(text)
        return ChannelStatus("wechat", self.ok, detail="" if self.ok else "boom")


def test_broadcast_both_channels():
    c, w = _Console(), _WeChat()
    Broadcaster(c, w).broadcast(ChatOutput("hello"))
    assert c.lines == ["hello"]
    assert w.sent == ["hello"]


def test_broadcast_wechat_failure_keeps_console():
    c, w = _Console(), _WeChat(ok=False)
    res = Broadcaster(c, w).broadcast(ChatOutput("hello"))
    assert c.lines == ["hello"]
    assert res.wechat.ok is False
    assert res.console.ok is True


def test_broadcast_console_failure_keeps_wechat():
    class BadConsole:
        def write(self, text): raise RuntimeError("no tty")
    w = _WeChat()
    res = Broadcaster(BadConsole(), w).broadcast(ChatOutput("hello"))
    assert w.sent == ["hello"]
    assert res.console.ok is False


def test_split_for_wechat_respects_limit():
    text = "\n".join(f"line {i}" for i in range(500))
    chunks = split_for_wechat(text, limit=100)
    assert all(len(c) <= 100 for c in chunks)
    assert "".join(chunks).replace("\n", "") == text.replace("\n", "")


def test_broadcast_long_message_chunks():
    w = _WeChat()
    Broadcaster(None, w).broadcast(ChatOutput("x" * 4000))
    assert len(w.sent) >= 3


def test_broadcast_empty_is_not_silent():
    c = _Console()
    Broadcaster(c, None).broadcast(ChatOutput(""))
    assert c.lines  # not silently dropped


# --- wechat sync cursor + dedup (CHAT-009) -------------------------------- #

def test_sync_cursor_roundtrip(tmp_path):
    p = tmp_path / "sync.json"
    cur = SyncCursor(path=p)
    cur.update("buf-1")
    assert SyncCursor(path=p).buf == "buf-1"


def test_dedup_repeated_message_id(tmp_path):
    cur = SyncCursor(path=tmp_path / "sync.json")
    assert cur.is_duplicate("m1") is False
    assert cur.is_duplicate("m1") is True
    assert cur.is_duplicate("m2") is False


def test_dedup_bounded(tmp_path):
    cur = SyncCursor(path=tmp_path / "sync.json")
    for i in range(700):
        cur.is_duplicate(f"m{i}")
    assert len(cur._seen) <= 500


# --- wechat message parsing (CHAT-008) ------------------------------------ #

def _text_msg(sender, text, mid="m1"):
    return {"from_user_id": sender, "message_id": mid, "item_list": [{"type": 1, "text_item": {"text": text}}]}


def test_parse_updates_accepts_bound_recipient():
    resp = {"msgs": [_text_msg("alice", "hi")]}
    msgs = parse_updates(resp, account_id="bot", recipient_id="alice")
    assert len(msgs) == 1 and msgs[0].text == "hi"


def test_parse_updates_ignores_bot_and_strangers():
    resp = {"msgs": [_text_msg("bot", "self"), _text_msg("stranger", "spam"), _text_msg("alice", "ok")]}
    msgs = parse_updates(resp, account_id="bot", recipient_id="alice")
    assert [m.text for m in msgs] == ["ok"]


def test_parse_updates_ignores_non_text():
    resp = {"msgs": [{"from_user_id": "alice", "item_list": [{"type": 2}]}]}
    assert parse_updates(resp, account_id="bot", recipient_id="alice") == []


# --- session lifecycle (CHAT-007/012) ------------------------------------- #

class _Sink:
    def __init__(self): self.outputs = []
    def write(self, text): self.outputs.append(text)


def _session():
    sink = _Sink()
    b = Broadcaster(sink, None)
    q = queue.Queue()
    return ChatSession(b, input_queue=q), sink, q


def test_quit_stops_session():
    sess, sink, q = _session()
    q.put(ChatMessage(source="console", text="/quit"))
    rc = sess.run()
    assert rc == 0
    assert any("再见" in o for o in sink.outputs)


def test_help_lists_commands():
    sess, sink, q = _session()
    q.put(ChatMessage(source="console", text="/help"))
    q.put(ChatMessage(source="console", text="/quit"))
    sess.run()
    assert any("/recent" in o for o in sink.outputs)


def test_shutdown_idempotent():
    sess, _, _ = _session()
    sess.request_stop()
    sess.request_stop()  # must not raise


def test_processing_error_does_not_kill_session(monkeypatch):
    sess, sink, q = _session()

    def boom(message):
        raise RuntimeError("bad message")

    monkeypatch.setattr(sess, "_handle", boom)
    q.put(ChatMessage(source="console", text="hi"))
    q.put(None)
    sess.run()  # returns without raising


def test_shared_conversation_across_sources(monkeypatch):
    calls = []
    monkeypatch.setattr("newsagent.chat.chat.generate", lambda **k: (calls.append(k), {"text": "r"})[1])
    from newsagent.chat.chat import Conversation
    sess, sink, q = _session()
    sess._conversation = Conversation()
    q.put(ChatMessage(source="console", text="from console"))
    q.put(ChatMessage(source="wechat", text="from wechat"))
    q.put(None)
    sess.run()
    assert len(calls) == 2  # both used the same LLM service
