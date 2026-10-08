"""Simple blocking scheduler loop (TASK-038).

Run: python -m newsagent.cli.schedule
Uses per-source crawl_interval; sleeps between ticks.
"""
from __future__ import annotations

import signal
import time

from ..logging_setup import get_logger, setup_logging
from ..sources.scheduler import run_due_sources

log = get_logger("cli.schedule")
TICK_SECONDS = 300
_running = True


def _stop(signum, frame):  # noqa: ANN001
    global _running
    log.info("scheduler stopping (signal %s)", signum)
    _running = False


def main() -> int:
    setup_logging()
    signal.signal(signal.SIGINT, _stop)
    signal.signal(signal.SIGTERM, _stop)
    log.info("scheduler started (tick=%ds)", TICK_SECONDS)
    while _running:
        try:
            result = run_due_sources()
            log.info("tick done: %s", result)
        except Exception as exc:  # noqa: BLE001 - scheduler must survive
            log.warning("tick failed: %s", exc)
        for _ in range(TICK_SECONDS):
            if not _running:
                break
            time.sleep(1)
    log.info("scheduler stopped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
