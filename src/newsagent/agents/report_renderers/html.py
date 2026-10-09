"""HTML renderer for AI news reports.

Uses only the Python standard library. All report content is escaped before
being embedded in HTML; no remote assets or network access are required.
"""
from __future__ import annotations

from html import escape
from urllib.parse import urlsplit

from ..report import ReportData


_CSS = r"""
:root { color-scheme: light; font-family: -apple-system, BlinkMacSystemFont,
  "Segoe UI", "Noto Sans CJK SC", "PingFang SC", "Microsoft YaHei", sans-serif;
  color: #202938; background: #f4f6f9; }
* { box-sizing: border-box; }
body { margin: 0; line-height: 1.7; }
main { max-width: 900px; margin: 0 auto; padding: 36px 24px 64px; }
header { border-bottom: 1px solid #d8dee8; margin-bottom: 28px; padding-bottom: 22px; }
h1 { line-height: 1.25; font-size: clamp(1.8rem, 4vw, 2.5rem); margin: 0 0 12px; }
.meta { color: #596579; font-size: .95rem; }
section { margin: 28px 0; }
h2 { font-size: 1.4rem; border-bottom: 1px solid #d8dee8; padding-bottom: 8px; }
article { background: #fff; border: 1px solid #e0e5ed; border-radius: 10px;
  padding: 18px 22px; margin: 16px 0; overflow-wrap: anywhere; }
article h3 { font-size: 1.12rem; margin: 0 0 10px; line-height: 1.45; }
article p { margin: 8px 0; }
.impact { color: #46536a; }
.score { color: #596579; font-size: .9rem; }
.empty { background: #fff; border-radius: 8px; padding: 20px; }
a { color: #175dcc; overflow-wrap: anywhere; }
.sources { display: flex; flex-wrap: wrap; gap: 8px 14px; margin-top: 12px; font-size: .92rem; }
footer { color: #707b8d; border-top: 1px solid #d8dee8; padding-top: 14px;
  margin-top: 42px; font-size: .85rem; }
@media (max-width: 600px) { main { padding: 22px 14px 40px; } article { padding: 15px; } }
@media print {
  :root { background: #fff; }
  body { font-size: 10.5pt; }
  main { max-width: none; padding: 0; }
  article { border: 1px solid #ccc; border-radius: 0; break-inside: auto; }
  article h3 { break-after: avoid; }
  h2 { break-after: avoid; }
  a { color: inherit; text-decoration: none; }
  footer { break-inside: avoid; }
  @page { size: A4; margin: 18mm 16mm 20mm; }
}
"""


def _safe_http_url(value: object) -> str | None:
    """Accept only absolute HTTP(S) URLs for clickable source links."""
    candidate = str(value or '').strip()
    if not candidate:
        return None
    try:
        parsed = urlsplit(candidate)
    except ValueError:
        return None
    if parsed.scheme.lower() not in {'http', 'https'} or not parsed.netloc:
        return None
    return candidate


def _event_card(event: dict) -> str:
    title = escape(str(event.get('title') or '（无标题）'))
    pieces = [f'<article><h3>{title}</h3>']
    summary = event.get('summary')
    if summary:
        pieces.append(f'<p>{escape(str(summary))}</p>')
    impact = event.get('why_it_matters')
    if impact:
        pieces.append(f'<p class="impact"><strong>影响：</strong>{escape(str(impact))}</p>')
    score = event.get('base_score')
    if score is not None:
        pieces.append(f'<p class="score">评分：{escape(str(score))}</p>')

    links: list[str] = []
    source_urls = event.get('source_urls') or ()
    if isinstance(source_urls, str):
        source_urls = (source_urls,)
    for value in source_urls:
        url = _safe_http_url(value)
        if url is not None:
            safe_url = escape(url, quote=True)
            links.append(
                f'<a href="{safe_url}" rel="noopener noreferrer">来源 {len(links) + 1}</a>'
            )
    if links:
        pieces.append(f'<nav class="sources" aria-label="文章来源">{"".join(links)}</nav>')

    pieces.append('</article>')
    return ''.join(pieces)


def render_html(data: ReportData) -> str:
    """Render a complete, standalone HTML5 report."""
    title = escape(data.title)
    generated = escape(data.generated_at.strftime('%Y-%m-%d %H:%M UTC'))
    parts = [
        '<!doctype html>',
        '<html lang="zh-CN">',
        '<head>',
        '<meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width, initial-scale=1">',
        f'<title>{title}</title>',
        f'<style>{_CSS}</style>',
        '</head>',
        '<body><main>',
        '<header>',
        f'<h1>{title}</h1>',
        f'<div class="meta">覆盖最近 {data.period_hours} 小时 · 共 {data.events_count} 个事件</div>',
        f'<div class="meta">生成时间：{generated}</div>',
        '</header>',
    ]

    if not data.events:
        parts.append('<p class="empty">暂无事件。请先运行 <code>news fetch</code> 与 <code>news pipeline</code>。</p>')
    else:
        if data.top_events:
            parts.append('<section><h2>🔥 头条</h2>')
            parts.extend(_event_card(event) for event in data.top_events)
            parts.append('</section>')
        if data.other_events:
            parts.append('<section><h2>📌 其他事件</h2>')
            parts.extend(_event_card(event) for event in data.other_events)
            parts.append('</section>')

    parts.extend([
        '<footer>由 AI News Agent 本地生成。本文件可离线阅读。</footer>',
        '</main></body></html>',
    ])
    return '\n'.join(parts)
