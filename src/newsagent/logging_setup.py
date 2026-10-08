"""Logging + error model (TASK-003).

A single place to configure logging so every layer reports consistent context.
"""
from __future__ import annotations

import logging
import sys

from .config import get_config


_CONFIGURED = False


class NewsAgentError(Exception):
    """Base class for all NewsAgent errors."""


class SourceError(NewsAgentError):
    """A single source failed (fetch/parse). Pipeline must continue."""


class FetchError(SourceError):
    """Network / HTTP level failure."""


class ParseError(SourceError):
    """Content could not be parsed into candidates."""


class LLMError(NewsAgentError):
    """LLM call failed after retries and fallbacks."""


class SchemaError(LLMError):
    """LLM returned output that failed schema validation."""


class StorageError(NewsAgentError):
    """SQLite / repository failure."""


def setup_logging(level: str | None = None) -> None:
    global _CONFIGURED
    if _CONFIGURED:
        return
    cfg = get_config()
    logging.basicConfig(
        level=(level or cfg.log_level).upper(),
        format="%(asctime)s %(levelname)-7s %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        stream=sys.stderr,
    )
    _CONFIGURED = True


def get_logger(name: str) -> logging.Logger:
    setup_logging()
    return logging.getLogger(name)
