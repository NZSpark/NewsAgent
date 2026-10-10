"""WeChat config model + validation (TASK-019 ~ TASK-025).

Non-sensitive configuration only. Credentials and recipient state live in a
separate protected directory handled by wechat.credentials / wechat.state.
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

from ..config import PROJECT_ROOT
from ..logging_setup import NewsAgentError, get_logger

log = get_logger("wechat.config")

# TASK-020: canonical send modes.
SEND_MODES = ("summary", "full", "summary_pdf")
DEFAULT_MODE = "summary_pdf"  # TASK-021

# TASK-023: scheduled action values.
SCHEDULE_ACTIONS = ("generate_and_send", "send_existing")

_TIME_RE = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")


class WeChatConfigError(NewsAgentError):
    """Invalid WeChat configuration."""


def _env_path(name: str, default: str) -> Path:
    raw = os.environ.get(name, default)
    path = Path(raw)
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    return path


@dataclass(frozen=True)
class RetryConfig:
    max_retries: int = 3  # TASK-022
    interval_seconds: int = 10

    def __post_init__(self) -> None:
        if self.max_retries < 0:
            raise WeChatConfigError(
                f"retry.max_retries must be >= 0, got {self.max_retries}"
            )
        if self.interval_seconds < 0:
            raise WeChatConfigError(
                f"retry.interval_seconds must be >= 0, got {self.interval_seconds}"
            )


@dataclass(frozen=True)
class ScheduleConfig:
    """Config-only schedule fields. This NEVER starts an internal scheduler."""

    daily_time: str = "08:00"
    mode: str | None = None  # None => inherit global default_mode
    action: str = "generate_and_send"

    def __post_init__(self) -> None:
        if not _TIME_RE.match(self.daily_time):
            raise WeChatConfigError(
                f"schedule.daily_time must be HH:MM (24h), got {self.daily_time!r}"
            )
        if self.mode is not None and self.mode not in SEND_MODES:
            raise WeChatConfigError(
                f"schedule.mode must be one of {SEND_MODES} or null, got {self.mode!r}"
            )
        if self.action not in SCHEDULE_ACTIONS:
            raise WeChatConfigError(
                f"schedule.action must be one of {SCHEDULE_ACTIONS}, got {self.action!r}"
            )


@dataclass(frozen=True)
class WeChatConfig:
    enabled: bool = True
    default_mode: str = DEFAULT_MODE
    retry: RetryConfig = field(default_factory=RetryConfig)
    split_long_text: bool = True
    schedule: ScheduleConfig = field(default_factory=ScheduleConfig)

    def __post_init__(self) -> None:
        if self.default_mode not in SEND_MODES:
            raise WeChatConfigError(
                f"default_mode must be one of {SEND_MODES}, got {self.default_mode!r}"
            )

    def resolve_mode(self, cli_mode: str | None = None, schedule_mode: str | None = None) -> str:
        """TASK-084 ~ TASK-086: CLI > schedule override > global default."""
        for candidate in (cli_mode, schedule_mode, self.default_mode):
            if candidate is None:
                continue
            if candidate not in SEND_MODES:
                raise WeChatConfigError(
                    f"mode must be one of {SEND_MODES}, got {candidate!r}"
                )
            return candidate
        raise WeChatConfigError("no send mode resolved")


def config_dir() -> Path:
    """TASK-026: protected WeChat config directory (default ~/.newsagent/wechat)."""
    return _env_path("NEWSAGENT_WECHAT_DIR", str(Path.home() / ".newsagent" / "wechat"))


def config_file() -> Path:
    return config_dir() / "config.json"


def parse_config(raw: dict | None) -> WeChatConfig:
    """TASK-024: strict validation with explicit errors on invalid values."""
    if raw is None:
        return WeChatConfig()
    section = raw.get("wechat", raw)
    if not isinstance(section, dict):
        raise WeChatConfigError("wechat config must be an object")

    retry_raw = section.get("retry", {}) or {}
    retry = RetryConfig(
        max_retries=int(retry_raw.get("max_retries", 3)),
        interval_seconds=int(retry_raw.get("interval_seconds", 10)),
    )
    sched_raw = section.get("schedule", {}) or {}
    schedule = ScheduleConfig(
        daily_time=str(sched_raw.get("daily_time", "08:00")),
        mode=sched_raw.get("mode"),
        action=str(sched_raw.get("action", "generate_and_send")),
    )
    return WeChatConfig(
        enabled=bool(section.get("enabled", True)),
        default_mode=str(section.get("default_mode", DEFAULT_MODE)),
        retry=retry,
        split_long_text=bool((section.get("message", {}) or {}).get("split_long_text", True)),
        schedule=schedule,
    )


def load_config(path: Path | None = None) -> WeChatConfig:
    """Load config.json if present; missing file yields defaults."""
    target = path or config_file()
    if not target.exists():
        return WeChatConfig()
    try:
        raw = json.loads(target.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise WeChatConfigError(f"corrupt config file {target}: {exc}") from exc
    return parse_config(raw)
