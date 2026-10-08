"""End-to-end demo with a stubbed LLM so the pipeline works without live proxies."""
from __future__ import annotations

import sys

sys.path.insert(0, "src")

from newsagent.llm import client as llm_client


def fake_call(provider, messages, model):
    user = messages[-1]["content"].lower()
    if "whether an article" in messages[0]["content"].lower():
        related = "ai" in user or "model" in user or "openai" in user
        return (
            '{"is_ai_related": %s, "topics": ["LLM"], "entities": ["OpenAI"], "confidence": 0.9}'
            % ("true" if related else "false"),
            {},
        )
    if "summarize" in messages[0]["content"].lower() or "summary" in user:
        return '{"summary": "AI summary", "importance": 0.7, "confidence": 0.8}', {}
    return '{"answer": "这是本地新闻库的摘要。", "sources": []}', {}


llm_client._call_once = fake_call  # type: ignore[assignment]

from newsagent.pipeline.classify import run_classify
from newsagent.pipeline.cluster import cluster_articles
from newsagent.pipeline.ranking import run_ranking
from newsagent.storage.search import get_recent_events

print("== classify ==", run_classify(limit=20))
print("== cluster  ==", cluster_articles(hours=72))
print("== ranking  ==", run_ranking())
events = get_recent_events(hours=72, limit=5)
print("== recent events ==", len(events))
for ev in events[:5]:
    print(f"  [{ev['event_id']}] score={ev['base_score']} {ev['title'][:60]}")
