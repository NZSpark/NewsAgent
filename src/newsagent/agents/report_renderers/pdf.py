"""Optional PDF renderer using WeasyPrint.

WeasyPrint and its platform-specific system libraries are optional. This module
imports the renderer lazily so Markdown and HTML remain usable without it.
"""
from __future__ import annotations

from pathlib import Path


def write_pdf(html_content: str, path: Path) -> Path:
    """Render a standalone HTML report to a PDF file.

    Raises a clear RuntimeError when WeasyPrint cannot be imported or the
    underlying renderer cannot initialize. No network requests are needed by
    the generated report template.
    """
    try:
        from weasyprint import HTML
    except Exception as exc:  # includes missing Python packages and system-library load errors
        raise RuntimeError(
            'PDF output requires WeasyPrint and its system dependencies. '
            'Install the project PDF extra with '
            '`python -m pip install -e ".[pdf]"` and follow the platform '
            'installation instructions for WeasyPrint.'
        ) from exc

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        HTML(string=html_content).write_pdf(target=str(path))
    except Exception as exc:
        raise RuntimeError(f'PDF rendering failed: {exc}') from exc

    if not path.is_file() or path.stat().st_size == 0:
        raise RuntimeError('PDF renderer completed without producing a non-empty file')
    return path
