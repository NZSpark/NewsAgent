"""Probe local LLM providers with a real chat call (longer timeout)."""
from __future__ import annotations

import sys
import time

from newsagent.llm.providers import make_client, model_for


def main() -> int:
    providers = sys.argv[1:] or ["deepseek-web", "gemini-web", "chatgpt-web"]
    for provider in providers:
        model = model_for(provider)
        client = make_client(provider)
        t0 = time.time()
        try:
            resp = client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": "Reply with exactly: PONG"}],
                max_tokens=16,
                timeout=120.0,
            )
            content = (resp.choices[0].message.content or "").strip()
            print(f"[{provider}] {int((time.time()-t0)*1000)}ms -> {content!r}")
        except Exception as exc:  # noqa: BLE001
            print(f"[{provider}] FAILED after {int((time.time()-t0)*1000)}ms: {type(exc).__name__}: {exc}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
