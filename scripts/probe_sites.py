"""Probe every source in doc/sites.md and report which actually return articles.

Uses the real fetch pipeline (parser_type decides RSS vs HTML) with a bounded
per-site timeout. Prints a table of source -> status (ok/empty/error) plus the
number of articles fetched, so we can prune dead sites from doc/sites.md.

Usage:
    .venv/bin/python scripts/probe_sites.py [--limit N] [--timeout SEC]
"""
from __future__ import annotations

import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from newsagent.config import get_config
from newsagent.fetch import fetch
from newsagent.logging_setup import setup_logging
from newsagent.sources.registry import parse_sites_md


def probe(source) -> dict:
    t0 = time.time()
    try:
        articles = fetch(source)
        return {
            "id": source.id,
            "name": source.name,
            "parser": source.parser_type,
            "status": "ok" if articles else "empty",
            "count": len(articles),
            "ms": int((time.time() - t0) * 1000),
        }
    except Exception as exc:  # noqa: BLE001
        return {
            "id": source.id,
            "name": source.name,
            "parser": source.parser_type,
            "status": "error",
            "count": 0,
            "ms": int((time.time() - t0) * 1000),
            "error": f"{type(exc).__name__}: {str(exc)[:80]}",
        }


def main() -> int:
    setup_logging()
    args = sys.argv[1:]
    limit = None
    if "--limit" in args:
        limit = int(args[args.index("--limit") + 1])

    sources = parse_sites_md(get_config().sites_md)
    if limit:
        sources = sources[:limit]
    print(f"probing {len(sources)} sources...\n")

    results: list[dict] = []
    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = {pool.submit(probe, s): s for s in sources}
        for fut in as_completed(futures):
            r = fut.result()
            results.append(r)
            flag = {"ok": "OK   ", "empty": "EMPTY", "error": "ERR  "}[r["status"]]
            extra = f"  {r.get('error', '')}" if r.get("error") else ""
            print(f"  {flag} {r['count']:>3}  {r['ms']:>6}ms  [{r['parser']:>4}] {r['name']}{extra}")

    order = {"ok": 0, "empty": 1, "error": 2}
    results.sort(key=lambda r: (order[r["status"]], r["name"].lower()))
    ok = [r for r in results if r["status"] == "ok"]
    empty = [r for r in results if r["status"] == "empty"]
    err = [r for r in results if r["status"] == "error"]

    print(f"\n=== SUMMARY: ok={len(ok)} empty={len(empty)} error={len(err)} / {len(results)} ===\n")
    print("OK (returned articles):")
    for r in ok:
        print(f"  {r['count']:>3}  {r['name']}")
    print("\nEMPTY (fetched but 0 articles):")
    for r in empty:
        print(f"        {r['name']}")
    print("\nERROR:")
    for r in err:
        print(f"        {r['name']}  {r.get('error','')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
