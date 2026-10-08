"""CLI (TASK-037): news fetch | sources | recent | search | event | health | pipeline | ask"""
from __future__ import annotations

import argparse
import json
import sys

from ..logging_setup import setup_logging


def _print(obj) -> None:
    print(json.dumps(obj, ensure_ascii=False, indent=2, default=str))


def cmd_fetch(args) -> int:
    from ..pipeline.runner import run_fetch
    ids = set(args.source) if args.source else None
    result = run_fetch(only_source_ids=ids, limit_sources=args.limit)
    _print(result)
    return 0


def cmd_sources(args) -> int:
    from ..storage.database import connect, init_db
    from ..storage.repository import SourceRepository
    conn = connect()
    init_db(conn)
    rows = [dict(r) for r in SourceRepository(conn).list_all()]
    conn.close()
    _print(rows)
    return 0


def cmd_recent(args) -> int:
    from ..storage.search import get_recent_events
    _print(get_recent_events(hours=args.hours, limit=args.limit))
    return 0


def cmd_search(args) -> int:
    from ..storage.search import search_local_events
    _print(search_local_events(args.query, limit=args.limit))
    return 0


def cmd_event(args) -> int:
    from ..storage.search import get_event
    event = get_event(args.event_id)
    if event is None:
        print(f"event not found: {args.event_id}", file=sys.stderr)
        return 1
    _print(event)
    return 0


def cmd_health(args) -> int:
    from ..llm.health import check_all
    from ..storage.database import connect, init_db
    from ..storage.repository import SourceRepository
    conn = connect()
    init_db(conn)
    source_health = [dict(r) for r in SourceRepository(conn).health()]
    conn.close()
    _print({"sources": source_health, "llm_providers": check_all()})
    return 0


def cmd_pipeline(args) -> int:
    from ..pipeline.classify import run_classify
    from ..pipeline.cluster import cluster_articles
    from ..pipeline.ranking import run_ranking
    result = {
        "classify": run_classify(limit=args.limit),
        "cluster": cluster_articles(hours=args.hours),
    }
    result["ranking"] = run_ranking()
    _print(result)
    return 0


def cmd_ask(args) -> int:
    from ..agents.query import answer
    _print(answer(args.question))
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="news", description="AI News Agent CLI")
    sub = p.add_subparsers(dest="command", required=True)

    sp = sub.add_parser("fetch", help="fetch sources into SQLite")
    sp.add_argument("--source", action="append", help="only these source ids")
    sp.add_argument("--limit", type=int, help="limit number of sources")
    sp.set_defaults(func=cmd_fetch)

    sp = sub.add_parser("sources", help="list sources and status")
    sp.set_defaults(func=cmd_sources)

    sp = sub.add_parser("recent", help="recent events")
    sp.add_argument("--hours", type=int, default=24)
    sp.add_argument("--limit", type=int, default=50)
    sp.set_defaults(func=cmd_recent)

    sp = sub.add_parser("search", help="search events")
    sp.add_argument("query")
    sp.add_argument("--limit", type=int, default=50)
    sp.set_defaults(func=cmd_search)

    sp = sub.add_parser("event", help="show one event")
    sp.add_argument("event_id")
    sp.set_defaults(func=cmd_event)

    sp = sub.add_parser("health", help="source + LLM health")
    sp.set_defaults(func=cmd_health)

    sp = sub.add_parser("pipeline", help="classify + cluster + rank")
    sp.add_argument("--limit", type=int, default=30)
    sp.add_argument("--hours", type=int, default=48)
    sp.set_defaults(func=cmd_pipeline)

    sp = sub.add_parser("ask", help="ask a question against local events")
    sp.add_argument("question")
    sp.set_defaults(func=cmd_ask)

    return p


def main(argv: list[str] | None = None) -> int:
    setup_logging()
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
