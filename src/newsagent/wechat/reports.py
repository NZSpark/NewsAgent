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

MAX_PDF_BYTES = 50 * 1024 * 1024  # TASK-062


class ReportInputError(NewsAgentError):
    """Report selection/pairing/validation failed (no retry)."""


@dataclass(frozen=True)
class ReportBundle:
    """A consistent set of files for one report (TASK-049)."""

    report_id: str
    summary: str | None
    full_text_path: Path | None
    pdf_path: Path | None
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
    files = data.get('files') or {}
    pdf = Path(files['pdf']) if files.get('pdf') else None
    md = Path(files['md']) if files.get('md') else None
    return ReportBundle(
        report_id=str(data.get('report_id', path.stem)),
        summary=data.get('summary'),
        full_text_path=md,
        pdf_path=pdf,
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


def _validate_pdf(path: Path) -> None:
    """TASK-060/TASK-061/TASK-062: existence, magic bytes, size limit."""
    _check_readable(path, 'PDF')
    size = path.stat().st_size
    if size == 0:
        raise ReportInputError(f'PDF is empty: {path}')
    if size > MAX_PDF_BYTES:
        raise ReportInputError(
            f'PDF too large ({size} bytes > {MAX_PDF_BYTES}): {path}'
        )
    with path.open('rb') as fh:
        if fh.read(5) != b'%PDF-':
            raise ReportInputError(f'not a valid PDF (missing %PDF- header): {path}')


def resolve_report(
    mode: str,
    file: Path | None = None,
    directory: Path | None = None,
) -> ReportBundle:
    """Resolve inputs for a send mode (TASK-053 ~ TASK-059).

    - summary: uses the latest manifest's stored summary text.
    - full: needs the Markdown full text.
    - summary_pdf: needs PDF + a summary from the SAME report.
    """
    directory = directory or output_dir()

    if mode == 'summary':
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

    if mode == 'full':
        path = file
        bundle = None
        if path is None:
            _, data = latest_manifest(directory)
            bundle = _bundle_from_manifest(_, data)
            path = bundle.full_text_path
        if path is None:
            raise ReportInputError('no full-text report available')
        _check_readable(path, 'full report')
        return ReportBundle(
            report_id=(bundle.report_id if bundle else path.stem),
            summary=None,
            full_text_path=path,
            pdf_path=None,
        )

    if mode == 'summary_pdf':
        if file is not None:
            # TASK-054/TASK-057: --file is the PDF; locate the matching summary.
            _validate_pdf(file)
            manifest = _find_manifest_for_pdf(file, directory)
            if manifest is None:
                raise ReportInputError(
                    f'cannot find a report manifest matching {file.name}; '
                    'refusing to pair an arbitrary summary (TASK-058/TASK-059)'
                )
            _, data = manifest
            bundle = _bundle_from_manifest(_, data)
            if bundle.pdf_path != file and not _same_file(bundle.pdf_path, file):
                raise ReportInputError(
                    f'manifest {_} does not reference {file}' 
                )
        else:
            _, data = latest_manifest(directory)
            bundle = _bundle_from_manifest(_, data)
        if bundle.pdf_path is None:
            raise ReportInputError('latest report has no PDF (TASK-064: no fallback)')
        _validate_pdf(bundle.pdf_path)
        summary = (bundle.summary or '').strip()
        if not summary:
            # TASK-065: PDF present but summary missing => stop the whole send.
            raise ReportInputError(
                'PDF exists but its summary is missing; refusing to send PDF alone'
            )
        return bundle

    raise ReportInputError(f'unknown send mode: {mode!r}')


def _same_file(a: Path | None, b: Path) -> bool:
    if a is None:
        return False
    try:
        return a.resolve() == b.resolve()
    except OSError:
        return False


def _find_manifest_for_pdf(pdf: Path, directory: Path) -> tuple[Path, dict] | None:
    target = pdf.resolve()
    for _, path, data in _manifests(directory):
        files = data.get('files') or {}
        ref = files.get('pdf')
        if not ref:
            continue
        try:
            if Path(ref).resolve() == target:
                return path, data
        except OSError:
            continue
    return None
