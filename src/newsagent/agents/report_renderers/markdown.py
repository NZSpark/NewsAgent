"""Markdown renderer for AI news reports."""
from __future__ import annotations

from ..report import ReportData


def _section(title: str, events: tuple[dict, ...]) -> str:
    if not events:
        return ''
    lines = [f'## {title}\n']
    for ev in events:
        lines.append(f"### {ev['title']}")
        if ev.get('summary'):
            lines.append(ev['summary'])
        if ev.get('why_it_matters'):
            lines.append(f"- 影响：{ev['why_it_matters']}")
        score = ev.get('base_score')
        if score is not None:
            lines.append(f'- 评分：{score}')
        lines.append('')
    return '\n'.join(lines)


def render_markdown(data: ReportData) -> str:
    """Render a ReportData snapshot as Markdown."""
    lines = [
        f'# {data.title}',
        '',
        f'> 覆盖最近 {data.period_hours} 小时，共 {data.events_count} 个事件。',
        '',
    ]
    if not data.events:
        lines.append('_暂无事件。请先运行 `news fetch` 与 `news pipeline`。_')
        return '\n'.join(lines)

    lines.append('## 🔥 头条\n')
    for ev in data.top_events:
        lines.append(f"### {ev['title']}")
        if ev.get('summary'):
            lines.append(ev['summary'])
        if ev.get('why_it_matters'):
            lines.append(f"- 影响：{ev['why_it_matters']}")
        lines.append('')

    lines.append(_section('📌 其他事件', data.other_events))
    return '\n'.join(line for line in lines if line is not None)
