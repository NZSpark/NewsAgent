"""News Chat: unified terminal + WeChat conversational entry point.

Modules:
- models:    message/output/state contracts (CHAT-001)
- broadcast: dual-channel output broadcaster (CHAT-002)
- chat:      ordinary-text LLM conversation (CHAT-003)
- commands:  slash-command parsing + allowlist + CLI dispatch (CHAT-004/005)
- console:   terminal input adapter (CHAT-006)
- session:   session orchestration + lifecycle (CHAT-007)
- wechat:    iLink inbound/outbound + sync cursor (CHAT-008/009/010)
"""
from .models import ChatMessage, ChatOutput, SessionState

__all__ = ["ChatMessage", "ChatOutput", "SessionState"]
