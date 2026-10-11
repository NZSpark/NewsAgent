"""Slash-command parsing, allowlist, and in-process CLI dispatch (CHAT-004/005).

Security model:
- Only inputs whose first non-space char is `/` are treated as commands.
- Command names are allowlisted; anything else is refused (never sent to the LLM
  or executed).
- Arguments are parsed with shlex and validated by the existing CLI parser via
  `build_parser().parse_args(...)`; we never use subprocess or `shell=True`.
"""
from __future__ import annotations

import contextlib
import io
import shlex

from ..logging_setup import get_logger
from .models import ChatOutput

log = get_logger("chat.commands")

# Commands the Chat session handles itself (not forwarded to the CLI).
BUILTIN = {"help", "quit"}

# Approved CLI commands. This mirrors the registered subcommands in
# cli/main.py; sensitive/interactive ones (wechat login/bind/logout, chat)
# are intentionally excluded.
ALLOWED: set[str] = {
    "fetch", "sources", "recent", "search", "event", "health", "pipeline",
    "ask", "semantic-cluster", "research", "report", "index", "semantic-search",
}

# Subcommands that must never be reachable through Chat.
DENIED_SUBCOMMANDS = {"wechat", "chat"}


def is_command(text: str) -> bool:
    return text.lstrip().startswith("/")


def parse(text: str) -> tuple[str, list[str]]:
    """Split a `/name args...` line. Raises ValueError on bad quoting."""
    stripped = text.strip()
    if not stripped.startswith("/"):
        raise ValueError("not a command")
    body = stripped[1:]
    tokens = shlex.split(body)
    if not tokens:
        raise ValueError("empty command")
    return tokens[0].lower(), tokens[1:]


HELP_TEXT = """可用命令：
/fetch [--source ID] [--limit N]   抓取信息源
/sources                          查看信息源状态
/recent [--hours N] [--limit N]   查看近期事件
/search QUERY [--limit N]         检索本地事件
/event EVENT_ID                   查看单个事件
/health                           检查来源与 LLM 健康
/pipeline [--limit N] [--hours N] 分类+聚类+排序
/ask QUESTION                     基于本地事件问答
/semantic-cluster [--hours N]     语义聚类
/research [EVENT_ID] [--limit N] [--min-importance F] [--live]
/report [--hours N] [--formats md,html,pdf]
/index [--limit N]                建立向量索引
/semantic-search QUERY [--limit N] [--articles]
/help                             显示本帮助
/quit                             退出会话""".strip()


class CommandError(Exception):
    """Raised for unknown/denied commands and parse errors."""


def _reject_side_effect_args(command: str, tokens: list[str]) -> None:
    """Block arguments that would cause duplicate/dangerous side effects."""
    if command == "report" and ("--send-wechat" in tokens or "--wechat-mode" in tokens):
        raise CommandError(
            "/report 在 Chat 中不支持 --send-wechat / --wechat-mode；"
            "结果由广播器发送。"
        )


def run_command(text: str) -> ChatOutput:
    """Parse and execute an allowlisted command, capturing stdout/stderr.

    Returns a ChatOutput; command failures become error outputs, never session
    termination. CLI dispatch is serialized by the session (CHAT-005).
    """
    try:
        name, tokens = parse(text)
    except ValueError as exc:
        return ChatOutput(f"命令解析失败：{exc}\n\n{HELP_TEXT}", source="command", is_error=True)

    if name in DENIED_SUBCOMMANDS:
        return ChatOutput(f"命令 /{name} 在 Chat 中不可用。\n\n{HELP_TEXT}", source="command", is_error=True)
    if name not in ALLOWED:
        return ChatOutput(f"未知命令：/{name}\n\n{HELP_TEXT}", source="command", is_error=True)

    try:
        _reject_side_effect_args(name, tokens)
    except CommandError as exc:
        return ChatOutput(str(exc), source="command", is_error=True)

    from ..cli.main import build_parser

    parser = build_parser()
    try:
        args = parser.parse_args([name, *tokens])
    except SystemExit:
        # argparse prints its own usage to stderr (captured below) and exits;
        # convert into a readable result instead of killing the session.
        return ChatOutput(f"参数错误：/{name} {' '.join(tokens)}\n\n{HELP_TEXT}", source="command", is_error=True)

    if getattr(args, "command", None) not in ALLOWED:
        return ChatOutput(f"命令 /{name} 不被允许。", source="command", is_error=True)

    out_buf, err_buf = io.StringIO(), io.StringIO()
    try:
        with contextlib.redirect_stdout(out_buf), contextlib.redirect_stderr(err_buf):
            rc = args.func(args)
    except SystemExit as exc:
        rc = exc.code if isinstance(exc.code, int) else 1
    except Exception as exc:  # noqa: BLE001 - never let one command kill the session
        log.warning("command /%s failed: %s", name, exc)
        return ChatOutput(f"命令执行失败：/{name}: {exc}", source="command", is_error=True)

    out = out_buf.getvalue().strip()
    err = err_buf.getvalue().strip()
    rc = rc or 0
    if not out and not err:
        out = f"/{name} 执行完成（无输出，exit={rc}）。"
    if rc != 0:
        detail = err or out
        return ChatOutput(f"/{name} 失败（exit={rc}）：\n{detail}", source="command", is_error=True)
    return ChatOutput(out, source="command", is_error=False)
