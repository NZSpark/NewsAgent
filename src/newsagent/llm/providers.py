"""Provider registry + client factory (TASK-014).

Strictly follows doc/local_llm.md:
  api = openai-completions, apiKey = none, text only.
  Never send `developer` role, never send `reasoning_effort`.
"""
from __future__ import annotations

from openai import OpenAI

from ..config import provider_config, provider_order


def make_client(provider: str) -> OpenAI:
    pc = provider_config(provider)
    # apiKey is fixed to "none" for all local Web providers.
    return OpenAI(base_url=pc.base_url, api_key="none", timeout=120.0, max_retries=0)


def model_for(provider: str) -> str:
    return provider_config(provider).model


def available_providers() -> list[str]:
    return provider_order()
