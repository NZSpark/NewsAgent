"""Central configuration (TASK-002).

All business code reads config from here; no scattered hard-coded paths.
Environment variables with the NEWSAGENT_ / LLM_ prefix override defaults.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]


# ----------------------------- LLM providers ----------------------------- #

# Strictly mirrors doc/local_llm.md. api = openai-completions, apiKey = none.
PROVIDER_DEFAULTS: dict[str, dict[str, str]] = {
    "deepseek-web": {
        "base_url": "http://127.0.0.1:8000/v1",
        "model": "deepseek-chat",
    },
    "gemini-web": {
        "base_url": "http://127.0.0.1:8001/v1",
        "model": "gemini-chat",
    },
    "chatgpt-web": {
        "base_url": "http://127.0.0.1:8002/v1",
        "model": "chatgpt-chat",
    },
}

# Task-level routing (design_chatgpt.md section 11).
# value = ordered list of providers (first = preferred, rest = fallbacks)
TASK_ROUTING: dict[str, list[str]] = {
    "classify_article": ["deepseek-web", "gemini-web", "chatgpt-web"],
    "summarize_article": ["deepseek-web", "gemini-web", "chatgpt-web"],
    "cluster_review": ["gemini-web", "chatgpt-web"],
    "evidence_review": ["gemini-web", "chatgpt-web"],
    "complex_analysis": ["chatgpt-web", "gemini-web", "deepseek-web"],
    "user_query": ["chatgpt-web", "gemini-web", "deepseek-web"],
}


def _env(name: str, default: str) -> str:
    return os.environ.get(name, default)


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return float(raw)
    except ValueError:
        return default


@dataclass(frozen=True)
class ProviderConfig:
    name: str
    base_url: str
    model: str


def provider_config(name: str) -> ProviderConfig:
    """Load one provider, honoring env overrides like DEEPSEEK_WEB_BASE_URL."""
    defaults = PROVIDER_DEFAULTS[name]
    env_prefix = name.upper().replace("-", "_")
    return ProviderConfig(
        name=name,
        base_url=_env(f"{env_prefix}_BASE_URL", defaults["base_url"]),
        model=_env(f"{env_prefix}_MODEL", defaults["model"]),
    )


def provider_order() -> list[str]:
    raw = _env("LLM_PROVIDERS", ",".join(PROVIDER_DEFAULTS))
    order = [p.strip() for p in raw.split(",") if p.strip()]
    return [p for p in order if p in PROVIDER_DEFAULTS] or list(PROVIDER_DEFAULTS)


@dataclass(frozen=True)
class Config:
    # storage
    db_path: Path = field(
        default_factory=lambda: _resolve(_env("NEWSAGENT_DB_PATH", "data/newsagent.db"))
    )
    # sources
    sites_md: Path = field(
        default_factory=lambda: _resolve(_env("NEWSAGENT_SITES_MD", "doc/sites.md"))
    )
    # fetch
    fetch_timeout: float = field(default_factory=lambda: _env_float("NEWSAGENT_FETCH_TIMEOUT", 15))
    fetch_concurrency: int = field(default_factory=lambda: _env_int("NEWSAGENT_FETCH_CONCURRENCY", 6))
    fetch_retries: int = field(default_factory=lambda: _env_int("NEWSAGENT_FETCH_RETRIES", 2))
    user_agent: str = field(
        default_factory=lambda: _env("NEWSAGENT_USER_AGENT", "NewsAgent/0.1")
    )
    jina_api_key: str = field(default_factory=lambda: _env("JINA_API_KEY", ""))
    # llm
    llm_timeout: float = field(default_factory=lambda: _env_float("LLM_TIMEOUT", 120))
    llm_max_retries: int = field(default_factory=lambda: _env_int("LLM_MAX_RETRIES", 1))
    llm_concurrency: int = field(default_factory=lambda: _env_int("LLM_CONCURRENCY", 3))
    # scheduler intervals (minutes)
    interval_official: int = field(default_factory=lambda: _env_int("NEWSAGENT_INTERVAL_OFFICIAL", 30))
    interval_media: int = field(default_factory=lambda: _env_int("NEWSAGENT_INTERVAL_MEDIA", 60))
    interval_community: int = field(default_factory=lambda: _env_int("NEWSAGENT_INTERVAL_COMMUNITY", 60))
    interval_research: int = field(default_factory=lambda: _env_int("NEWSAGENT_INTERVAL_RESEARCH", 180))
    interval_newsletter: int = field(default_factory=lambda: _env_int("NEWSAGENT_INTERVAL_NEWSLETTER", 720))
    # logging
    log_level: str = field(default_factory=lambda: _env("NEWSAGENT_LOG_LEVEL", "INFO"))

    def interval_for_category(self, category: str) -> int:
        return {
            "official": self.interval_official,
            "media": self.interval_media,
            "community": self.interval_community,
            "research": self.interval_research,
            "newsletter": self.interval_newsletter,
        }.get(category, self.interval_media)


def _resolve(p: str) -> Path:
    path = Path(p)
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    return path


_config: Config | None = None


def get_config() -> Config:
    global _config
    if _config is None:
        _config = Config()
    return _config


def reset_config() -> None:
    """Test helper: force re-read of environment variables."""
    global _config
    _config = None
