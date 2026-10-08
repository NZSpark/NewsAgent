"""Smoke test the P7 features with a stubbed LLM."""
from __future__ import annotations

import sys

sys.path.insert(0, "src")

from newsagent.llm import client as llm_client


def fake_call(provider, messages, model):
    system = messages[0]["content"].lower()
    user = messages[-1]["content"]
    if "decide whether" in system:
        related = "ai" in user.lower() or "model" in user.lower() or "gemini" in user.lower()
        return '{"is_ai_related": %s, "topics": ["LLM"], "entities": ["Google"], "confidence": 0.9}' % ("true" if related else "false"), {}
    if "summary" in system:
        return '{"summary": "AI 摘要内容", "importance": 0.75, "confidence": 0.85}', {}
    if "group" in system:
        return '{"groups": []}', {}
    if "research planner" in system:
        return '{"questions": ["问题一", "问题二"]}', {}
    if "analyst" in system:
        return '{"report": "研究报告正文", "key_findings": ["发现一"], "open_questions": ["待解一"]}', {}
    return '{"answer": "答案", "sources": []}', {}


llm_client._call_once = fake_call  # type: ignore[assignment]

from newsagent.agents.report import build_report
from newsagent.agents.research import research_top_events
from newsagent.pipeline.semantic_cluster import semantic_cluster
from newsagent.storage.database import connect, init_db
from newsagent.storage.vector import index_events, semantic_search

conn = connect()
init_db(conn)
print("== semantic_cluster ==", semantic_cluster(hours=72, conn=conn))
print("== index ==", index_events(conn))
hits = semantic_search(conn, "Google AI")
print("== semantic_search hits ==", len(hits), hits[0]["title"][:40] if hits else "-")
print("== research ==", len(research_top_events(limit=2, min_importance=0.0)))
report = build_report(hours=72)
print("== report chars ==", len(report))
print(report[:300])
conn.close()
