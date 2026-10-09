"""Offline CLI tests for report format selection and error handling."""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from newsagent.agents.report import ReportGenerationError
from newsagent.cli.main import build_parser, cmd_report


def test_report_cli_defaults_to_markdown():
    args = build_parser().parse_args(["report"])

    assert args.hours == 24
    assert args.formats == "md"
    assert args.func is cmd_report


def test_report_cli_accepts_multiple_formats():
    args = build_parser().parse_args(
        ["report", "--hours", "168", "--formats", "MD,html,pdf"]
    )

    assert args.hours == 168
    assert args.formats == "MD,html,pdf"


def test_cmd_report_prints_single_path(
    tmp_path: Path, monkeypatch, capsys
):
    import newsagent.agents.report as report_module

    path = tmp_path / "daily_2026-10-10.md"
    calls = []

    def fake_write_reports(*, hours, formats):
        calls.append((hours, formats))
        return {"md": path}

    monkeypatch.setattr(report_module, "write_reports", fake_write_reports)
    result = cmd_report(SimpleNamespace(hours=24, formats="md"))

    assert result == 0
    assert calls == [(24, "md")]
    assert capsys.readouterr().out == f"{path}\n"


def test_cmd_report_prints_json_for_multiple_paths(
    tmp_path: Path, monkeypatch, capsys
):
    import newsagent.agents.report as report_module

    paths = {
        "md": tmp_path / "daily_2026-10-10.md",
        "html": tmp_path / "daily_2026-10-10.html",
    }
    monkeypatch.setattr(
        report_module, "write_reports", lambda **kwargs: paths
    )

    result = cmd_report(SimpleNamespace(hours=24, formats="md,html"))

    assert result == 0
    assert json.loads(capsys.readouterr().out) == {
        fmt: str(path) for fmt, path in paths.items()
    }


def test_cmd_report_returns_usage_error_for_invalid_format(
    monkeypatch, capsys
):
    import newsagent.agents.report as report_module

    def reject_formats(**kwargs):
        raise ValueError("Unsupported report format 'docx'. Supported formats: md, html, pdf")

    monkeypatch.setattr(report_module, "write_reports", reject_formats)
    result = cmd_report(SimpleNamespace(hours=24, formats="docx"))
    captured = capsys.readouterr()

    assert result == 2
    assert "Unsupported report format" in captured.err
    assert captured.out == ""


def test_cmd_report_reports_partial_generation_failure(
    tmp_path: Path, monkeypatch, capsys
):
    import newsagent.agents.report as report_module

    generated = {"md": tmp_path / "daily_2026-10-10.md"}
    failure = ReportGenerationError(generated, {"pdf": "mock PDF failure"})

    def fail_one_format(**kwargs):
        raise failure

    monkeypatch.setattr(report_module, "write_reports", fail_one_format)
    result = cmd_report(SimpleNamespace(hours=24, formats="md,pdf"))

    assert result == 1
    assert json.loads(capsys.readouterr().out) == {
        "generated": {"md": str(generated["md"])},
        "errors": {"pdf": "mock PDF failure"},
    }
