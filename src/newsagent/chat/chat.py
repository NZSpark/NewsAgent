"""Ordinary-text LLM conversation with bounded shared history (CHAT-003).

Uses the project's unified `newsagent.llm.generate` (task-routed providers),
not a separate config system. Conversation history is in-memory, shared across
terminal and WeChat, and bounded to the most recent turns.
"""
from __future__ import annotations

from collections import deque

from ..logging_setup import LLMError, get_logger
from ..llm import generate
from .models import ChatMessage, ChatOutput, MAX_MESSAGE_CHARS

log = get_logger("chat.llm")

SYSTEM_PROMPT = (
    "你是 NewsAgent 助手，一个运行在本地的 AI 新闻分析工具。"
    "用简体中文简洁、准确地回答用户的问题。"
    "你可以介绍如何使用 NewsAgent 的斜杠命令（以 / 开头），但不要声称执行了任何命令。"
    "不要生成或建议任何 shell 命令。"
)

_MAX_TURNS = 10  # user+assistant exchanges retained


class Conversation:
    """Bounded, in-memory conversation history shared by all inputs."""

    def __init__(self, max_turns: int = _MAX_TURNS) -> None:
        self._max_messages = max_turns * 2
        self._history: deque[dict] = deque(maxlen=self._max_messages)

    def _messages(self, user_text: str) -> list[dict]:
        return [
            {"role": "system", "content": SYSTEM_PROMPT},
            *self._history,
            {"role": "user", "content": user_text},
        ]

    def respond(self, message: ChatMessage) -> ChatOutput:
        text = message.text.strip()[:MAX_MESSAGE_CHARS]
        if not text:
            return ChatOutput("（空消息）", source="chat")
        try:
            data = generate(
                task="user_query",
                messages=self._messages(text),
                json_mode=False,
                allow_prose=True,
            )
        except LLMError as exc:
            log.warning("chat LLM failed: %s", exc)
            return ChatOutput(
                "抱歉，暂时无法生成回复（LLM 服务不可用）。你仍可以使用 /help 查看命令。",
                source="chat",
                is_error=True,
            )
        reply = (data.get("text") or "").strip()
        if not reply:
            reply = "（模型没有返回内容）"
        self._history.append({"role": "user", "content": text})
        self._history.append({"role": "assistant", "content": reply[:MAX_MESSAGE_CHARS]})
        return ChatOutput(reply, source="chat", is_error=False)
