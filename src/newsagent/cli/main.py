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
    from ..sources.scheduler import source_health
    from ..storage.database import connect, init_db
    conn = connect()
    init_db(conn)
    conn.close()
    _print({"sources": source_health(), "llm_providers": check_all()})
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


def cmd_semantic_cluster(args) -> int:
    from ..pipeline.semantic_cluster import semantic_cluster
    _print(semantic_cluster(hours=args.hours))
    return 0


def cmd_research(args) -> int:
    from ..agents.research import research_event, research_top_events
    if args.event_id:
        result = research_event(args.event_id, live=args.live)
        _print(result or {"error": "research failed or event not found"})
    else:
        _print(research_top_events(limit=args.limit, min_importance=args.min_importance, live=args.live))
    return 0


def cmd_report(args) -> int:
    from ..agents.report import ReportGenerationError, write_reports

    try:
        paths = write_reports(hours=args.hours, formats=args.formats)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    except ReportGenerationError as exc:
        _print({
            "generated": {fmt: str(path) for fmt, path in exc.generated.items()},
            "errors": exc.errors,
        })
        return 1

    # TASK-117 ~ TASK-123: only send when explicitly requested.
    wechat_status = None
    if getattr(args, "send_wechat", False):
        wechat_status = _send_generated_report(paths, getattr(args, "wechat_mode", None))

    if wechat_status is not None:
        _print({"reports": {fmt: str(p) for fmt, p in paths.items()}, "wechat": wechat_status})
        return 0 if wechat_status.get("status") == "success" else 1
    if len(paths) == 1:
        print(str(next(iter(paths.values()))))
    else:
        _print({fmt: str(path) for fmt, path in paths.items()})
    return 0


def _send_generated_report(paths: dict, mode_override: str | None) -> dict:
    """Send the just-generated report; build a bundle from explicit paths (TASK-120)."""
    from ..wechat.config import load_config, WeChatConfigError
    from ..wechat.credentials import load_recipient, is_bound
    from ..wechat.reports import ReportBundle
    from ..wechat.sender import send_report

    try:
        cfg = load_config()
        mode = cfg.resolve_mode(cli_mode=mode_override)
        cfg = cfg.__class__(**{**cfg.__dict__, "default_mode": mode})
    except WeChatConfigError as exc:
        return {"status": "failed", "errors": [str(exc)]}

    if not cfg.enabled or not is_bound():
        return {"status": "failed", "errors": ["wechat disabled or recipient not bound"]}

    # Pull the summary text from the report's manifest when available.
    summary = None
    manifest = paths.get("md")
    if manifest is not None:
        cand = manifest.with_suffix(".manifest.json")
        if cand.exists():
            try:
                import json as _json
                summary = _json.loads(cand.read_text(encoding="utf-8")).get("summary")
            except Exception:  # noqa: BLE001
                summary = None

    bundle = ReportBundle(
        report_id=paths.get("md", next(iter(paths.values()))).stem,
        summary=summary,
        full_text_path=paths.get("md"),
        pdf_path=paths.get("pdf"),
    )
    client = None
    try:
        client = _wechat_client()
        outcome = send_report(bundle, client, load_recipient(), cfg, source="report_command")
        return outcome.to_dict()
    except Exception as exc:  # noqa: BLE001 - auth/network
        return {"status": "failed", "errors": [str(exc)]}
    finally:
        if client is not None:
            client.close()


def _wechat_client():
    """Build the real iLink client from stored credentials."""
    from ..wechat.credentials import load_account
    from ..wechat.ilink import ILinkClient
    account = load_account()
    if not account:
        raise RuntimeError("not logged in; run `news wechat login` first")
    return ILinkClient(account)


def cmd_wechat_login(args) -> int:
    from ..wechat.client import WeChatError
    from ..wechat.login import qr_login
    try:
        creds = qr_login()
    except WeChatError as exc:
        print(f"登录失败：{exc}", file=sys.stderr)
        return 1
    except Exception as exc:  # noqa: BLE001 - network/QR failures
        print(f"登录失败：{exc}", file=sys.stderr)
        return 1
    if not creds:
        return 1
    print("下一步：用目标个人微信向 Bot 发一条消息，然后运行 `news wechat bind`。")
    return 0


def cmd_wechat_status(args) -> int:
    from ..wechat.config import load_config
    from ..wechat.credentials import redacted_status
    cfg = load_config()
    _print({
        **redacted_status(),
        "default_mode": cfg.default_mode,
        "enabled": cfg.enabled,
        "retry": {"max_retries": cfg.retry.max_retries, "interval_seconds": cfg.retry.interval_seconds},
        "schedule": {"daily_time": cfg.schedule.daily_time, "mode": cfg.schedule.mode, "action": cfg.schedule.action},
    })
    return 0


def cmd_wechat_bind(args) -> int:
    from ..wechat.client import WeChatError
    from ..wechat.login import bind_recipient
    try:
        recipient = bind_recipient()
    except WeChatError as exc:
        print(f"绑定失败：{exc}", file=sys.stderr)
        return 1
    except Exception as exc:  # noqa: BLE001 - network failures
        print(f"绑定失败：{exc}", file=sys.stderr)
        return 1
    return 0 if recipient else 1


def cmd_wechat_logout(args) -> int:
    from ..wechat.credentials import clear_account, clear_recipient
    clear_account()
    clear_recipient()
    print("已清除本地微信凭据与绑定状态（报告文件未删除）。")
    return 0


def cmd_wechat_send(args) -> int:
    from pathlib import Path
    from ..wechat.config import load_config, WeChatConfigError
    from ..wechat.credentials import load_recipient, is_bound
    from ..wechat.reports import resolve_report, ReportInputError
    from ..wechat.sender import send_report

    try:
        cfg = load_config()
        mode = cfg.resolve_mode(cli_mode=args.mode)
        cfg = cfg.__class__(**{**cfg.__dict__, "default_mode": mode})
    except WeChatConfigError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    if not cfg.enabled:
        print("wechat 发送已禁用（config.wechat.enabled=false）", file=sys.stderr)
        return 2
    if not is_bound():
        print("收件人未绑定，请先运行 `news wechat bind`。", file=sys.stderr)
        return 1

    try:
        bundle = resolve_report(mode, file=Path(args.file) if args.file else None)
    except ReportInputError as exc:
        print(f"报告输入错误：{exc}", file=sys.stderr)
        return 1

    recipient = load_recipient()
    client = None
    try:
        client = _wechat_client()
        outcome = send_report(bundle, client, recipient, cfg, source="manual")
    except Exception as exc:  # noqa: BLE001 - auth/network/input
        print(f"发送失败：{exc}", file=sys.stderr)
        return 1
    finally:
        if client is not None:
            client.close()
    _print(outcome.to_dict())
    return 0 if outcome.status == "success" else 1


def cmd_index(args) -> int:
    from ..storage.database import connect, init_db
    from ..storage.vector import index_articles, index_events
    conn = connect()
    init_db(conn)
    n_events = index_events(conn, limit=args.limit)
    n_articles = index_articles(conn, limit=args.limit)
    conn.close()
    _print({"events_indexed": n_events, "articles_indexed": n_articles})
    return 0


def cmd_semantic_search(args) -> int:
    from ..storage.database import connect, init_db
    from ..storage.vector import search_articles, semantic_search
    conn = connect()
    init_db(conn)
    if args.articles:
        results = search_articles(conn, args.query, limit=args.limit)
    else:
        results = semantic_search(conn, args.query, limit=args.limit)
    conn.close()
    _print(results)
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

    sp = sub.add_parser("semantic-cluster", help="LLM merge of rule clusters (TASK-026)")
    sp.add_argument("--hours", type=int, default=48)
    sp.set_defaults(func=cmd_semantic_cluster)

    sp = sub.add_parser("research", help="active research on an event (TASK-042)")
    sp.add_argument("event_id", nargs="?", help="omit to research top events")
    sp.add_argument("--limit", type=int, default=3)
    sp.add_argument("--min-importance", type=float, default=0.6)
    sp.add_argument("--live", action="store_true", help="allow live fetch fallback")
    sp.set_defaults(func=cmd_research)

    sp = sub.add_parser("report", help="generate daily/weekly briefing (TASK-043)")
    sp.add_argument("--hours", type=int, default=24)
    sp.add_argument(
        "--formats",
        default="md",
        help="comma-separated output formats: md,html,pdf (default: md)",
    )
    sp.add_argument("--send-wechat", action="store_true", help="send the generated report via WeChat")
    sp.add_argument("--wechat-mode", choices=["summary", "full", "summary_pdf"], default=None)
    sp.set_defaults(func=cmd_report)

    # WeChat subcommands (doc/wechat_tasks.md phase 6)
    wp = sub.add_parser("wechat", help="WeChat report delivery")
    wsub = wp.add_subparsers(dest="wechat_command", required=True)
    wsub.add_parser("login", help="QR login").set_defaults(func=cmd_wechat_login)
    wsub.add_parser("status", help="show login/binding status").set_defaults(func=cmd_wechat_status)
    wsub.add_parser("bind", help="bind recipient").set_defaults(func=cmd_wechat_bind)
    wsub.add_parser("logout", help="clear local credentials").set_defaults(func=cmd_wechat_logout)
    wsp = wsub.add_parser("send", help="send a report")
    wsp.add_argument("--mode", choices=["summary", "full", "summary_pdf"], default=None)
    wsp.add_argument("--file", default=None, help="report file (PDF for summary_pdf)")
    wsp.set_defaults(func=cmd_wechat_send)

    sp = sub.add_parser("index", help="build event embeddings (TASK-044)")
    sp.add_argument("--limit", type=int, default=500)
    sp.set_defaults(func=cmd_index)

    sp = sub.add_parser("semantic-search", help="vector search over events/articles (TASK-044)")
    sp.add_argument("query")
    sp.add_argument("--limit", type=int, default=20)
    sp.add_argument("--articles", action="store_true", help="search articles instead of events")
    sp.set_defaults(func=cmd_semantic_search)

    return p


def main(argv: list[str] | None = None) -> int:
    setup_logging()
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
