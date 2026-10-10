"""Report discovery, pairing and validation (TASK-049 ~ TASK-065).

The WeChat sender only consumes already-generated files. It never calls the
report generator, the LLM, or the PDF renderer (TASK-063).
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from ..config import PROJECT_ROOT
from ..logging_setup import NewsAgentError, get_logger

log = get_logger("wechat.reports")


class ReportInputError(NewsAgentError):
    """Report selection/pairing/validation failed (no retry)."""


@dataclass(frozen=True)
class ReportBundle:
    """The summary to send for one report (TASK-049, revised 2026-10-10).

    `full_text_path` / `pdf_path` are retained for backward compatibility with
    callers, but only `summary` is used now that PDF and full-text sending were
    removed.
    """

    report_id: str
    summary: str | None
    full_text_path: Path | None = None
    pdf_path: Path | None = None
    manifest_path: Path | None = None


def output_dir() -> Path:
    return PROJECT_ROOT / 'output'


def _load_manifest(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError) as exc:
        raise ReportInputError(f'cannot read report manifest {path}: {exc}') from exc
    if not isinstance(data, dict):
        raise ReportInputError(f'manifest {path} must be a JSON object')
    return data


def _manifests(directory: Path) -> list[tuple[float, Path, dict]]:
    items: list[tuple[float, Path, dict]] = []
    for path in directory.glob('*.manifest.json'):
        try:
            data = _load_manifest(path)
        except ReportInputError:
            continue
        items.append((path.stat().st_mtime, path, data))
    return items


def latest_manifest(directory: Path | None = None) -> tuple[Path, dict]:
    """TASK-051/TASK-052: pick the newest report; refuse to guess when absent."""
    directory = directory or output_dir()
    items = _manifests(directory)
    if not items:
        raise ReportInputError(
            f'no report manifest found in {directory}. Run `news report` first.'
        )
    items.sort(key=lambda t: t[0], reverse=True)
    _, path, data = items[0]
    return path, data


def _bundle_from_manifest(path: Path, data: dict) -> ReportBundle:
    return ReportBundle(
        report_id=str(data.get('report_id', path.stem)),
        summary=data.get('summary'),
        manifest_path=path,
    )


def _check_readable(path: Path, what: str) -> None:
    if not path.exists():
        raise ReportInputError(f'{what} missing: {path}')
    if not path.is_file():
        raise ReportInputError(f'{what} is not a file: {path}')
    try:
        with path.open('rb'):
            pass
    except OSError as exc:
        raise ReportInputError(f'{what} not readable: {path}: {exc}') from exc


def resolve_report(
    mode: str = 'summary',
    file: Path | None = None,
    directory: Path | None = None,
) -> ReportBundle:
    """Resolve the summary text to send (TASK-053 ~ TASK-059, revised).

    Design revision (2026-10-10): only `summary` mode remains. PDF attachments
    and full-text (multi-message) sending were removed because they read poorly
    in WeChat.
    """
    directory = directory or output_dir()
    if mode != 'summary':
        raise ReportInputError(
            f"unsupported send mode {mode!r}; only 'summary' is supported "
            "(PDF/full modes were removed)"
        )

    if file is not None:
        # A manually-specified summary file must be plain text.
        _check_readable(file, 'summary file')
        text = file.read_text(encoding='utf-8', errors='replace').strip()
        if not text:
            raise ReportInputError(f'summary file is empty: {file}')
        return ReportBundle(report_id=file.stem, summary=text, full_text_path=None, pdf_path=None)

    _, data = latest_manifest(directory)
    summary = (data.get('summary') or '').strip()
    if not summary:
        raise ReportInputError('latest report has no summary text')
    return _bundle_from_manifest(_, data)
