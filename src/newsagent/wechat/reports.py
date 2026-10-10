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
    """One report resolved for the requested send format (TASK-049).

    Multi-format sending was restored 2026-10-11. `mode` names the artifact to
    deliver (`md` / `html` / `pdf`, default `md`):

    - `md`:   inline text in `summary` (the markdown report body)
    - `html`: file attachment at `file_path`
    - `pdf`:  file attachment at `file_path`

    `summary` also carries the manifest summary for reference/logging. The
    legacy `full_text_path` / `pdf_path` fields are kept for callers that still
    reference them.
    """

    report_id: str
    summary: str | None
    full_text_path: Path | None = None
    pdf_path: Path | None = None
    manifest_path: Path | None = None
    mode: str = "md"
    file_path: Path | None = None


# Report artifacts produced by the generator, mapped to their manifest key.
_MANIFEST_KEY = {"md": "md", "html": "html", "pdf": "pdf"}
_FILE_SUFFIX = {"html": ".html", "pdf": ".pdf"}


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


def _bundle_from_manifest(path: Path, data: dict, mode: str) -> ReportBundle:
    return ReportBundle(
        report_id=str(data.get('report_id', path.stem)),
        summary=data.get('summary'),
        manifest_path=path,
        mode=mode,
    )


def _manifest_file(data: dict, mode: str) -> Path | None:
    """Resolve the artifact path recorded in the manifest's `files` map."""
    files = data.get('files') or {}
    if not isinstance(files, dict):
        return None
    key = _MANIFEST_KEY.get(mode, mode)
    raw = files.get(key)
    return Path(raw) if raw else None


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
    mode: str = 'md',
    file: Path | None = None,
    directory: Path | None = None,
) -> ReportBundle:
    """Resolve the artifact to send for `mode` (TASK-053 ~ TASK-059, restored).

    Formats (default `md`):

    - `md`:   inline markdown text (split into chunks by the sender if long)
    - `html`: HTML file sent as an attachment
    - `pdf`:  PDF file sent as an attachment

    Without `--file`, the newest report manifest is used and its `files` map is
    consulted for the requested artifact. A missing artifact fails loudly; it
    is never silently downgraded to another format (TASK-064/TASK-065).
    """
    from .config import SEND_MODES  # local import avoids a config cycle

    directory = directory or output_dir()
    if mode not in SEND_MODES:
        raise ReportInputError(
            f"unsupported send mode {mode!r}; expected one of {SEND_MODES}"
        )

    if file is not None:
        _check_readable(file, f'{mode} file')
        if mode == 'md':
            text = file.read_text(encoding='utf-8', errors='replace').strip()
            if not text:
                raise ReportInputError(f'markdown file is empty: {file}')
            return ReportBundle(
                report_id=file.stem, summary=text, mode='md',
                full_text_path=file, file_path=file,
            )
        # html / pdf attachments: validate the extension matches the mode.
        expected = _FILE_SUFFIX[mode]
        if file.suffix.lower() != expected:
            raise ReportInputError(
                f'{mode} mode expects a {expected} file, got {file.name}'
            )
        return ReportBundle(
            report_id=file.stem, summary=None, mode=mode,
            pdf_path=file if mode == 'pdf' else None, file_path=file,
        )

    manifest_path, data = latest_manifest(directory)
    artifact = _manifest_file(data, mode)
    if artifact is None:
        raise ReportInputError(
            f"latest report has no '{mode}' artifact; regenerate with "
            f"`news report --formats {mode}`"
        )
    _check_readable(artifact, f'{mode} file')

    if mode == 'md':
        text = artifact.read_text(encoding='utf-8', errors='replace').strip()
        if not text:
            raise ReportInputError(f'markdown report is empty: {artifact}')
        bundle = _bundle_from_manifest(manifest_path, data, mode)
        return ReportBundle(
            report_id=bundle.report_id,
            summary=text,
            manifest_path=manifest_path,
            mode='md',
            full_text_path=artifact,
            file_path=artifact,
        )

    bundle = _bundle_from_manifest(manifest_path, data, mode)
    return ReportBundle(
        report_id=bundle.report_id,
        summary=bundle.summary,
        manifest_path=manifest_path,
        mode=mode,
        pdf_path=artifact if mode == 'pdf' else None,
        file_path=artifact,
    )
