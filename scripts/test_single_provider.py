"""Force ALL tasks onto one provider and run the full LLM pipeline (TASK verification).

Usage: python scripts/test_single_provider.py chatgpt-web
"""
from __future__ import annotations

import sys

sys.path.insert(0, "src")

PROVIDER = sys.argv[1] if len(sys.argv) > 1 else "chatgpt-web"

from newsagent.config import TASK_ROUTING

for _task in list(TASK_ROUTING):
    TASK_ROUTING[_task] = [PROVIDER]

from newsagent.llm.router import providers_for_task
print(f"== forcing provider: {PROVIDER} ==")
print("   classify route:", providers_for_task("classify_article"))
print("   query route   :", providers_for_task("user_query"))

from newsagent.pipeline.classify import run_classify
from newsagent.agents.query import answer
from newsagent.agents.report import build_report

print("== classify (3) ==", run_classify(limit=3))
print("== ask ==")
result = answer("过去48小时有什么AI新闻？")
print("   answer len:", len(result["answer"]), "| sources:", len(result["sources"]))
print("   preview:", result["answer"][:200].replace(chr(10), " "))
report = build_report(hours=48)
print("== report chars ==", len(report))
