"""Automated daily / weekly briefing (TASK-043).

Builds a Markdown report from ranked events. Deterministic rendering; the LLM
only contributes event summaries already stored in the DB.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from ..config import PROJECT_ROOT
from ..logging_setup import get_logger
from ..storage.database import connect

log = get_logger("agents.report")

CATEGORY_LABELS = {
    "official": "官方发布",
    "research": "研究论文",
    "media": "媒体报道",
    "newsletter": "Newsletter",
    "community": "社区讨论",
}


def _section(title: str, events: list[dict]) -> str:
    if not events:
        return ""
    lines = [f"## {title}\n"]
    for ev in events:
        lines.append(f"### {ev['title']}")
        if ev.get("summary"):
            lines.append(ev["summary"])
        if ev.get("why_it_matters"):
            lines.append(f"- 影响：{ev['why_it_matters']}")
        score = ev.get("base_score")
        if score is not None:
            lines.append(f"- 评分：{score}")
        lines.append("")
    return "\n".join(lines)


def build_report(hours: int = 24, top_n: int = 20) -> str:
    conn = connect()
    rows = [
        dict(r)
        for r in conn.execute(
            """SELECT * FROM events
               WHERE COALESCE(published_at, first_seen_at) >= datetime('now', ?)
               ORDER BY base_score DESC LIMIT ?""",
            (f"-{hours} hours", top_n),
        )
    ]
    conn.close()

    now = datetime.now(timezone.utc)
    label = "日报" if hours <= 24 else "周报"
    lines = [f"# AI {label} · {now:%Y-%m-%d}", "", f"> 覆盖最近 {hours} 小时，共 {len(rows)} 个事件。", ""]

    if not rows:
        lines.append("_暂无事件。请先运行 `news fetch` 与 `news pipeline`。_")
        return "\n".join(lines)

    # Top events
    lines.append("## 🔥 头条\n")
    for ev in rows[:5]:
        lines.append(f"### {ev['title']}")
        if ev.get("summary"):
            lines.append(ev["summary"])
        if ev.get("why_it_matters"):
            lines.append(f"- 影响：{ev['why_it_matters']}")
        lines.append("")

    # Group remaining by source category heuristics (via linked articles)
    rest = rows[5:]
    lines.append(_section("📌 其他事件", rest))

    return "\n".join(line for line in lines if line is not None)


def write_report(hours: int = 24, out_dir: Path | None = None) -> Path:
    out_dir = out_dir or (PROJECT_ROOT / "output")
    out_dir.mkdir(parents=True, exist_ok=True)
    content = build_report(hours=hours)
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    suffix = "daily" if hours <= 24 else "weekly"
    path = out_dir / f"{suffix}_{stamp}.md"
    path.write_text(content, encoding="utf-8")
    log.info("report written to %s", path)
    return path
