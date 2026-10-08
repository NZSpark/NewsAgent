"""Pipeline run orchestration (TASK-013) + fetch/normalize/dedup flow."""
from __future__ import annotations

import httpx

from ..config import get_config
from ..fetch import build_client, fetch
from ..logging_setup import FetchError, ParseError, get_logger
from ..sources.models import Source
from ..sources.registry import parse_sites_md
from ..storage.database import connect, init_db
from ..storage.repository import ArticleRepository, PipelineRunRepository, SourceRepository
from .dedup import is_duplicate
from .normalize import normalize

log = get_logger("pipeline.runner")


def load_sources() -> list[Source]:
    cfg = get_config()
    return parse_sites_md(cfg.sites_md)


def run_fetch(
    only_source_ids: set[str] | None = None,
    limit_sources: int | None = None,
) -> dict:
    """Fetch all enabled sources, normalize, dedup, store. Failures are isolated."""
    cfg = get_config()
    conn = connect()
    init_db(conn)

    source_repo = SourceRepository(conn)
    article_repo = ArticleRepository(conn)
    run_repo = PipelineRunRepository(conn)

    sources = [s for s in load_sources() if s.enabled]
    if only_source_ids:
        sources = [s for s in sources if s.id in only_source_ids]
    if limit_sources:
        sources = sources[:limit_sources]

    run = run_repo.start()
    run.sources_total = len(sources)

    client = build_client()
    try:
        for source in sources:
            source_repo.upsert(source)
            try:
                candidates = fetch(source, client=client)
            except (FetchError, ParseError) as exc:
                log.warning("source %s failed: %s", source.name, exc)
                source_repo.mark_error(source.id)
                run.errors.append(f"{source.id}: {exc}")
                continue
            except Exception as exc:  # noqa: BLE001 - never let one source kill the run
                log.warning("source %s unexpected error: %s", source.name, exc)
                source_repo.mark_error(source.id)
                run.errors.append(f"{source.id}: {exc}")
                continue

            new_count = 0
            latest_published: str | None = None
            for candidate in candidates:
                run.articles_seen += 1
                try:
                    article = normalize(candidate)
                except Exception as exc:  # noqa: BLE001
                    log.debug("normalize failed for %s: %s", candidate.url, exc)
                    continue
                if latest_published is None and article.published_at:
                    latest_published = article.published_at
                if is_duplicate(article, article_repo):
                    continue
                if article_repo.insert(article):
                    new_count += 1
                    run.articles_new += 1

            source_repo.mark_success(source.id, latest_published)
            run.sources_success += 1
            log.info("%s: %d candidates, %d new", source.name, len(candidates), new_count)
    finally:
        client.close()

    run.status = "success" if not run.errors else "partial"
    run_repo.finish(run)
    conn.close()

    result = {
        "run_id": run.run_id,
        "sources_total": run.sources_total,
        "sources_success": run.sources_success,
        "articles_seen": run.articles_seen,
        "articles_new": run.articles_new,
        "errors": run.errors,
    }
    log.info("pipeline run done: %s", result)
    return result
