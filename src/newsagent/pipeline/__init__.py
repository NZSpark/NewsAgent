"""Pipeline package."""
from .normalize import canonicalize_url, clean_html, normalize
from .runner import load_sources, run_fetch

__all__ = ["canonicalize_url", "clean_html", "normalize", "load_sources", "run_fetch"]
