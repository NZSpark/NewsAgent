"""Offline tests for multi-format report rendering and output orchestration."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from newsagent.agents.report import (
    ReportData,
    ReportGenerationError,
    _normalize_formats,
    build_report,
    render_markdown,
    write_reports,
)
from newsagent.agents.report_renderers.html import render_html


@pytest.fixture()
def report_data() -> ReportData:
    return ReportData(
        title="AI 日报 · 2026-10-10",
        generated_at=datetime(2026, 10, 10, 0, 0, tzinfo=timezone.utc),
        period_hours=24,
        top_events=(
            {
                "event_id": "e1",
                "title": "模型发布 <重要> & 更新",
                "summary": "包含中文摘要。",
                "why_it_matters": "影响开发者。",
                "base_score": 0.95,
            },
        ),
        other_events=(
            {
                "event_id": "e2",
                "title": "Research event",
                "summary": "Research summary",
                "why_it_matters": None,
                "base_score": 0.5,
            },
        ),
    )


def test_normalize_formats_and_deduplicate():
    assert _normalize_formats("MD,html,md, PDF ") == ("md", "html", "pdf")
    assert _normalize_formats(["HTML", "pdf"]) == ("html", "pdf")


@pytest.mark.parametrize("formats", ["", ", ,", [], [" "]])
def test_normalize_formats_rejects_empty(formats):
    with pytest.raises(ValueError, match="At least one"):
        _normalize_formats(formats)


def test_normalize_formats_rejects_unknown_format():
    with pytest.raises(ValueError, match="Unsupported report format"):
        _normalize_formats("md,docx")


def test_markdown_renderer_preserves_report_content(report_data):
    rendered = render_markdown(report_data)
    assert "AI 日报 · 2026-10-10" in rendered
    assert "覆盖最近 24 小时，共 2 个事件。" in rendered
    assert "头条" in rendered
    assert "模型发布 <重要> & 更新" in rendered
    assert "影响：影响开发者。" in rendered
    assert "Research event" in rendered


def test_html_renderer_is_standalone_and_escapes_content(report_data):
    rendered = render_html(report_data)
    assert rendered.startswith("<!doctype html>")
    assert '<html lang="zh-CN">' in rendered
    assert '<meta charset="utf-8">' in rendered
    assert "@media print" in rendered
    assert "模型发布 &lt;重要&gt; &amp; 更新" in rendered
    assert "模型发布 <重要> & 更新" not in rendered
    assert "</script>" not in rendered
    assert "Research event" in rendered


def test_html_renderer_handles_empty_report():
    data = ReportData(
        title="AI 日报 · 2026-10-10",
        generated_at=datetime(2026, 10, 10, tzinfo=timezone.utc),
        period_hours=24,
        top_events=(),
        other_events=(),
    )
    rendered = render_html(data)
    assert "暂无事件" in rendered
    assert "共 0 个事件" in rendered


def test_write_reports_generates_multiple_formats_from_one_snapshot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, report_data: ReportData
):
    import newsagent.agents.report_renderers.html as html_renderer
    import newsagent.agents.report_renderers.markdown as md_renderer
    import newsagent.agents.report_renderers.pdf as pdf_renderer
    import newsagent.agents.report as report_module

    calls = {"data": 0, "md": 0, "html": 0, "pdf": 0}

    def fake_data(hours=24, top_n=20):
        calls["data"] += 1
        assert hours == 24
        return report_data

    def fake_markdown(data):
        calls["md"] += 1
        assert data is report_data
        return "# offline markdown"

    def fake_html(data):
        calls["html"] += 1
        assert data is report_data
        return "<!doctype html><html></html>"

    def fake_pdf(content, path):
        calls["pdf"] += 1
        assert content.startswith("<!doctype html>")
        Path(path).write_bytes(b"%PDF-1.7\nmock\n")
        return Path(path)

    monkeypatch.setattr(report_module, "build_report_data", fake_data)
    monkeypatch.setattr(md_renderer, "render_markdown", fake_markdown)
    monkeypatch.setattr(html_renderer, "render_html", fake_html)
    monkeypatch.setattr(pdf_renderer, "write_pdf", fake_pdf)

    paths = write_reports(hours=24, formats="md,html,pdf", out_dir=tmp_path)

    assert set(paths) == {"md", "html", "pdf"}
    assert paths["md"].name == "daily_2026-10-10.md"
    assert paths["html"].name == "daily_2026-10-10.html"
    assert paths["pdf"].name == "daily_2026-10-10.pdf"
    assert paths["md"].read_text(encoding="utf-8") == "# offline markdown"
    assert paths["html"].read_text(encoding="utf-8").startswith("<!doctype html>")
    assert paths["pdf"].read_bytes().startswith(b"%PDF-")
    assert calls == {"data": 1, "md": 1, "html": 1, "pdf": 1}


def test_write_reports_continues_when_one_format_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, report_data: ReportData
):
    import newsagent.agents.report as report_module
    import newsagent.agents.report_renderers.markdown as md_renderer
    import newsagent.agents.report_renderers.pdf as pdf_renderer

    monkeypatch.setattr(report_module, "build_report_data", lambda **kwargs: report_data)
    monkeypatch.setattr(md_renderer, "render_markdown", lambda data: "# success")

    def fail_pdf(content, path):
        raise RuntimeError("mock PDF failure")

    monkeypatch.setattr(pdf_renderer, "write_pdf", fail_pdf)

    with pytest.raises(ReportGenerationError) as caught:
        write_reports(hours=24, formats="md,pdf", out_dir=tmp_path)

    error = caught.value
    assert "md" in error.generated
    assert error.generated["md"].read_text(encoding="utf-8") == "# success"
    assert error.errors == {"pdf": "mock PDF failure"}


def test_build_report_remains_markdown_compatible(monkeypatch, report_data):
    import newsagent.agents.report as report_module

    monkeypatch.setattr(report_module, "build_report_data", lambda **kwargs: report_data)
    rendered = build_report(hours=24)
    assert isinstance(rendered, str)
    assert "AI 日报" in rendered
    assert "头条" in rendered


def test_html_renderer_links_only_absolute_http_sources(report_data):
    event = dict(
        report_data.top_events[0],
        source_urls=(
            "https://example.com/article?a=1&b=2",
            "javascript:alert(1)",
            "file:///etc/passwd",
            "//example.org/article",
        ),
    )
    data = ReportData(
        title=report_data.title,
        generated_at=report_data.generated_at,
        period_hours=report_data.period_hours,
        top_events=(event,),
        other_events=(),
    )

    rendered = render_html(data)

    assert 'href="https://example.com/article?a=1&amp;b=2"' in rendered
    assert 'href="javascript:alert(1)"' not in rendered
    assert 'href="file:///etc/passwd"' not in rendered
    assert 'href="//example.org/article"' not in rendered


def test_build_report_data_collects_deduplicated_source_urls(monkeypatch):
    import sqlite3
    import newsagent.agents.report as report_module

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE events (
            event_id TEXT PRIMARY KEY, title TEXT NOT NULL,
            first_seen_at TEXT, published_at TEXT, base_score REAL,
            summary TEXT, why_it_matters TEXT
        );
        CREATE TABLE articles (id TEXT PRIMARY KEY, url TEXT);
        CREATE TABLE event_articles (event_id TEXT, article_id TEXT);
        INSERT INTO events VALUES (
            'e1', 'Recent event', datetime('now'), datetime('now'),
            0.9, 'Summary', 'Impact'
        );
        INSERT INTO articles VALUES ('a1', 'https://b.example/article');
        INSERT INTO articles VALUES ('a2', 'https://a.example/article');
        INSERT INTO articles VALUES ('a3', 'https://b.example/article');
        INSERT INTO event_articles VALUES ('e1', 'a1');
        INSERT INTO event_articles VALUES ('e1', 'a2');
        INSERT INTO event_articles VALUES ('e1', 'a3');
        """
    )
    monkeypatch.setattr(report_module, "connect", lambda: conn)

    data = report_module.build_report_data(hours=24)

    assert data.events_count == 1
    assert data.top_events[0]["source_urls"] == (
        "https://a.example/article",
        "https://b.example/article",
    )


def test_build_report_data_orders_events_and_splits_top_five(monkeypatch):
    import sqlite3
    import newsagent.agents.report as report_module

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE events (
            event_id TEXT PRIMARY KEY, title TEXT NOT NULL,
            first_seen_at TEXT, published_at TEXT, base_score REAL,
            summary TEXT, why_it_matters TEXT
        );
        CREATE TABLE articles (id TEXT PRIMARY KEY, url TEXT);
        CREATE TABLE event_articles (event_id TEXT, article_id TEXT);
        """
    )
    conn.executemany(
        "INSERT INTO events VALUES (?, ?, datetime('now'), datetime('now'), ?, '', '')",
        [(f"e{i}", f"Event {i}", i / 10) for i in range(6)],
    )
    monkeypatch.setattr(report_module, "connect", lambda: conn)

    data = report_module.build_report_data(hours=24, top_n=10)

    assert data.events_count == 6
    assert [event["event_id"] for event in data.top_events] == ["e5", "e4", "e3", "e2", "e1"]
    assert [event["event_id"] for event in data.other_events] == ["e0"]


def test_write_reports_uses_weekly_prefix_and_creates_output_directory(
    tmp_path: Path, monkeypatch, report_data: ReportData
):
    import newsagent.agents.report as report_module

    weekly_data = ReportData(
        title="AI 周报 · 2026-10-10",
        generated_at=report_data.generated_at,
        period_hours=168,
        top_events=report_data.top_events,
        other_events=report_data.other_events,
    )
    monkeypatch.setattr(report_module, "build_report_data", lambda **kwargs: weekly_data)
    output_dir = tmp_path / "nested" / "reports"

    paths = write_reports(hours=168, formats="md", out_dir=output_dir)

    assert paths["md"].name == "weekly_2026-10-10.md"
    assert paths["md"].is_file()


def test_pdf_renderer_reports_missing_optional_dependency(monkeypatch, tmp_path: Path):
    import sys
    from newsagent.agents.report_renderers.pdf import write_pdf

    monkeypatch.setitem(sys.modules, "weasyprint", None)
    output = tmp_path / "report.pdf"

    with pytest.raises(RuntimeError, match="Install the project PDF extra"):
        write_pdf("<!doctype html><html></html>", output)

    assert not output.exists()
