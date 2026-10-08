"""Source Registry (TASK-004): parse doc/sites.md into Source objects."""
from __future__ import annotations

import re
from pathlib import Path

from ..logging_setup import get_logger
from .models import Source

log = get_logger("sources.registry")

# Match markdown table rows: | Name | [url](url) | note |
# Also tolerate bare URLs without markdown link syntax.
_ROW_RE = re.compile(r"^\s*\|(.+)\|\s*$")
_URL_RE = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")
_BARE_URL_RE = re.compile(r"(https?://\S+)")
_SEP_RE = re.compile(r"^\s*\|?\s*:?-{2,}:?\s*\|")

# Map markdown section titles (## ...) to our category vocabulary.
_SECTION_CATEGORY = [
    ("综合科技媒体", "media"),
    ("ai 垂直媒体", "newsletter"),
    ("newsletter", "newsletter"),
    ("官方博客", "official"),
    ("学术论文", "research"),
    ("社区与聚合", "community"),
    ("中文来源", "media"),
    ("即时新闻", "media"),
    ("商业媒体", "media"),
]


def _split_row(row: str) -> list[str]:
    inner = row.strip().strip("|")
    return [cell.strip() for cell in inner.split("|")]


def _extract_url(cell: str) -> str | None:
    m = _URL_RE.search(cell)
    if m:
        return m.group(2).strip()
    m = _BARE_URL_RE.search(cell)
    if m:
        return m.group(1).strip()
    if cell.startswith("http"):
        return cell.strip()
    return None


def _category_from_section(section: str) -> str:
    low = section.lower()
    for keyword, category in _SECTION_CATEGORY:
        if keyword in low:
            return category
    return ""


def parse_sites_md(path: Path) -> list[Source]:
    """Parse doc/sites.md. A bad row is skipped, never fatal."""
    if not path.exists():
        log.warning("sites.md not found at %s", path)
        return []

    text = path.read_text(encoding="utf-8")
    sources: list[Source] = []
    seen_ids: set[str] = set()
    section = ""

    for raw_line in text.splitlines():
        stripped = raw_line.strip()
        if stripped.startswith("#"):
            section = stripped.lstrip("#").strip()
            continue
        if not _ROW_RE.match(raw_line) or _SEP_RE.match(raw_line):
            continue

        try:
            cells = _split_row(raw_line)
            if len(cells) < 2:
                continue
            name = cells[0].strip()
            url = _extract_url(cells[1]) or _extract_url("".join(cells))
            if not url or not name:
                continue
            if name.lower() in {"网站", "机构", "provider"}:
                continue

            source_id = Source.make_id(name)
            if source_id in seen_ids:
                continue
            seen_ids.add(source_id)

            category = _category_from_section(section) or Source.infer_category(name, section)
            priority = {"official": 9, "research": 7, "media": 6, "newsletter": 5, "community": 4}.get(category, 5)

            sources.append(
                Source(
                    id=source_id,
                    name=name,
                    url=url,
                    category=category,
                    priority=priority,
                    parser_type=Source.infer_parser(url),
                )
            )
        except Exception as exc:  # noqa: BLE001 - isolate bad rows
            log.debug("skipping malformed row %r: %s", raw_line, exc)

    log.info("parsed %d sources from %s", len(sources), path.name)
    return sources


def get_source(sources: list[Source], source_id: str) -> Source | None:
    return next((s for s in sources if s.id == source_id), None)
