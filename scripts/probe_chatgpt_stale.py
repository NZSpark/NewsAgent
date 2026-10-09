"""Probe the local chatgpt-web proxy for STALE responses.

Observation (2026-10-10): chatgpt-web pings fine, but for real tasks it
sometimes returns the PREVIOUS turn's reply (e.g. 'PONG' instead of JSON).
This script sends N classification prompts with unique tags and counts how
often the reply is a correct JSON answer vs. a stale/unrelated response.

Usage:  .venv/bin/python scripts/probe_chatgpt_stale.py [n]
"""
from __future__ import annotations

import sys
import time
import uuid

from newsagent.llm.providers import make_client, model_for

PROVIDER = "chatgpt-web"


def main() -> int:
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 6
    client = make_client(PROVIDER)
    model = model_for(PROVIDER)

    ok = stale = fail = 0
    for i in range(n):
        tag = uuid.uuid4().hex[:6]
        messages = [
            {"role": "system", "content": "You classify news. Reply with JSON only."},
            {
                "role": "user",
                "content": (
                    'Is this AI-related? Reply exactly {"ai": true}. '
                    f"Text: OpenAI announced a new frontier model. (tag {tag})"
                ),
            },
        ]
        t0 = time.time()
        try:
            resp = client.chat.completions.create(
                model=model, messages=messages, max_tokens=200, timeout=300.0
            )
            content = (resp.choices[0].message.content or "").strip()
            dt = int((time.time() - t0) * 1000)
            if '"ai"' in content:
                ok += 1
                print(f"  run{i + 1}: OK    {dt}ms  {content!r}")
            else:
                stale += 1
                print(f"  run{i + 1}: STALE {dt}ms  {content!r}")
        except Exception as exc:  # noqa: BLE001
            fail += 1
            print(f"  run{i + 1}: FAIL  {int((time.time() - t0) * 1000)}ms "
                  f"{type(exc).__name__}: {str(exc)[:80]}")

    print(f"\nSUMMARY: ok={ok} stale={stale} fail={fail} / {n}")
    return 0 if ok == n else 1


if __name__ == "__main__":
    raise SystemExit(main())
