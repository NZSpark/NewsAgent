"""LLM package: unified client, router, schemas, prompts, usage."""
from .client import extract_json, generate, generate_async, shutdown
from .prompts import get_prompt
from .router import providers_for_task

__all__ = ["extract_json", "generate", "generate_async", "shutdown", "get_prompt", "providers_for_task"]
