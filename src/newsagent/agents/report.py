"""Automated daily / weekly briefing and multi-format output.

Builds one deterministic report data snapshot from ranked events, then
renders it as Markdown, HTML, and optionally PDF. Rendering never fetches news
or calls an LLM.
"""
from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..config import PROJECT_ROOT
from ..logging_setup import get_logger
from ..storage.database import connect

log = get_logger('agents.report')

CATEGORY_LABELS = {
    'official': '官方发布',
    'research': '研究论文',
    'media': '媒体报道',
    'newsletter': 'Newsletter',
    'community': '社区讨论',
}
SUPPORTED_FORMATS = ('md', 'html', 'pdf')


@dataclass(frozen=True)
class ReportData:
    """A consistent snapshot shared by every report renderer."""

    title: str
    generated_at: datetime
    period_hours: int
    top_events: tuple[dict[str, Any], ...]
    other_events: tuple[dict[str, Any], ...]

    @property
    def events(self) -> tuple[dict[str, Any], ...]:
        return self.top_events + self.other_events

    @property
    def events_count(self) -> int:
        return len(self.top_events) + len(self.other_events)


def _section(title: str, events: list[dict]) -> str:
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


def build_report_data(hours: int = 24, top_n: int = 20) -> ReportData:
    """Query the report once and return the data shared by all renderers."""
    conn = connect()
    try:
        rows = [
            dict(row)
            for row in conn.execute(
                '''SELECT e.*,
                          (SELECT group_concat(url, char(10))
                           FROM (
                               SELECT DISTINCT a.url AS url
                               FROM event_articles AS ea
                               JOIN articles AS a ON a.id = ea.article_id
                               WHERE ea.event_id = e.event_id
                                 AND a.url IS NOT NULL AND trim(a.url) <> ''
                               ORDER BY a.url
                           )) AS source_urls
                   FROM events AS e
                   WHERE COALESCE(e.published_at, e.first_seen_at) >= datetime('now', ?)
                   ORDER BY e.base_score DESC LIMIT ?''',
                (f'-{hours} hours', top_n),
            )
        ]
        for row in rows:
            raw_urls = row.get('source_urls') or ''
            row['source_urls'] = tuple(url.strip() for url in raw_urls.splitlines() if url.strip())
    finally:
        conn.close()

    generated_at = datetime.now(timezone.utc)
    label = '日报' if hours <= 24 else '周报'
    title = f'AI {label} · {generated_at:%Y-%m-%d}'
    return ReportData(
        title=title,
        generated_at=generated_at,
        period_hours=hours,
        top_events=tuple(rows[:5]),
        other_events=tuple(rows[5:]),
    )


def render_markdown(data: ReportData) -> str:
    """Render report data as Markdown, preserving the existing report layout."""
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

    lines.append(_section('📌 其他事件', list(data.other_events)))
    return '\n'.join(line for line in lines if line is not None)


def build_report(hours: int = 24, top_n: int = 20) -> str:
    """Return the Markdown report string (backward-compatible API)."""
    return render_markdown(build_report_data(hours=hours, top_n=top_n))


def _filename_prefix(data: ReportData) -> str:
    return 'daily' if data.period_hours <= 24 else 'weekly'


def _normalize_formats(formats: str | Iterable[str]) -> tuple[str, ...]:
    if isinstance(formats, str):
        candidates = formats.split(',')
    else:
        candidates = list(formats)
    normalized: list[str] = []
    for candidate in candidates:
        fmt = candidate.strip().lower()
        if not fmt:
            continue
        if fmt not in SUPPORTED_FORMATS:
            raise ValueError(
                f'Unsupported report format {candidate!r}. '
                f'Supported formats: {", ".join(SUPPORTED_FORMATS)}'
            )
        if fmt not in normalized:
            normalized.append(fmt)
    if not normalized:
        raise ValueError(
            f'At least one report format is required: {", ".join(SUPPORTED_FORMATS)}'
        )
    return tuple(normalized)


class ReportGenerationError(RuntimeError):
    """Raised when one or more requested formats fail to render or write."""

    def __init__(self, generated: dict[str, Path], errors: dict[str, str]) -> None:
        self.generated = generated
        self.errors = errors
        details = '; '.join(f'{fmt}: {message}' for fmt, message in errors.items())
        super().__init__(f'One or more report formats failed: {details}')


def extract_summary(data: ReportData, max_events: int = 5) -> str:
    """Plain-text summary for WeChat `summary` mode (TASK-071/TASK-072).

    Derived from the report's lead events; never calls an LLM.
    """
    lines = [data.title, '']
    if not data.top_events:
        lines.append('_本期暂无事件。_')
        return '\n'.join(lines)
    for ev in data.top_events[:max_events]:
        lines.append(f"• {ev['title']}")
        summary = (ev.get('summary') or '').strip()
        if summary:
            lines.append(f"  {summary}")
        if ev.get('why_it_matters'):
            lines.append(f"  影响：{ev['why_it_matters']}")
    return '\n'.join(lines)


def _write_manifest(
    output_dir: Path,
    data: ReportData,
    generated: dict[str, Path],
) -> Path:
    """Write a report manifest so summary/full/pdf can be paired reliably.

    Responds to the TASK-005 gap: there was no report id or file manifest.
    """
    stamp = data.generated_at.strftime('%Y-%m-%d')
    prefix = _filename_prefix(data)
    stamp_full = data.generated_at.strftime('%Y%m%dT%H%M%SZ')
    report_id = f'{prefix}-{stamp_full}'
    manifest = {
        'report_id': report_id,
        'prefix': prefix,
        'date': stamp,
        'generated_at': data.generated_at.isoformat(),
        'period_hours': data.period_hours,
        'title': data.title,
        'files': {fmt: str(path) for fmt, path in generated.items()},
        'summary': extract_summary(data),
        'events_count': data.events_count,
    }
    path = output_dir / f'{prefix}_{stamp}.manifest.json'
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
    return path


def write_reports(
    hours: int = 24,
    formats: str | Iterable[str] = ('md',),
    out_dir: Path | None = None,
) -> dict[str, Path]:
    """Write selected formats from one data snapshot.

    A failed format does not prevent attempts for the remaining formats. When
    any requested output fails, ReportGenerationError exposes successful paths
    and per-format errors to the caller.
    """
    selected = _normalize_formats(formats)
    output_dir = out_dir or (PROJECT_ROOT / 'output')
    data = build_report_data(hours=hours)
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = data.generated_at.strftime('%Y-%m-%d')
    prefix = _filename_prefix(data)
    generated: dict[str, Path] = {}
    errors: dict[str, str] = {}
    html_content: str | None = None

    for fmt in selected:
        path = output_dir / f'{prefix}_{stamp}.{fmt}'
        try:
            if fmt == 'md':
                from .report_renderers.markdown import render_markdown as render
                path.write_text(render(data), encoding='utf-8')
            elif fmt == 'html':
                from .report_renderers.html import render_html
                if html_content is None:
                    html_content = render_html(data)
                path.write_text(html_content, encoding='utf-8')
            else:
                from .report_renderers.html import render_html
                from .report_renderers.pdf import write_pdf
                if html_content is None:
                    html_content = render_html(data)
                write_pdf(html_content, path)
            generated[fmt] = path
            log.info('report written: format=%s path=%s', fmt, path)
        except Exception as exc:  # isolate formats so other requested files can still be generated
            errors[fmt] = str(exc) or exc.__class__.__name__
            log.exception('report generation failed: format=%s path=%s', fmt, path)

    if errors:
        raise ReportGenerationError(generated, errors)
    try:
        _write_manifest(output_dir, data, generated)
    except Exception:  # manifest is an aid, not a hard requirement for md-only callers
        log.exception('failed to write report manifest')
    return generated


def write_report(hours: int = 24, out_dir: Path | None = None) -> Path:
    """Write only Markdown and return its path (backward-compatible API)."""
    return write_reports(hours=hours, formats=('md',), out_dir=out_dir)['md']
