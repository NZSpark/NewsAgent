"""Send service: three modes + retry + status (TASK-071 ~ TASK-106)."""
from __future__ import annotations

import json
import os
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from ..logging_setup import get_logger
from .client import (
    PermanentSendError,
    RecoverableSendError,
    SendResult,
    WeChatClient,
    number_chunks,
    split_text,
)
from .config import WeChatConfig, config_dir
from .reports import ReportBundle

log = get_logger("wechat.sender")

STATE_FILE = "delivery-state.json"  # TASK-099..TASK-104


@dataclass
class DeliveryOutcome:
    """TASK-100/TASK-101: per-part status for one send."""

    execution_id: str
    source: str
    report_id: str
    mode: str
    started_at: str
    finished_at: str = ""
    attempts: int = 0
    summary_ok: bool = False
    full_ok: bool = False
    pdf_ok: bool = False
    status: str = "pending"  # success | partial | failed
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "execution_id": self.execution_id,
            "source": self.source,
            "report_id": self.report_id,
            "mode": self.mode,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "attempts": self.attempts,
            "summary_ok": self.summary_ok,
            "full_ok": self.full_ok,
            "pdf_ok": self.pdf_ok,
            "status": self.status,
            "errors": self.errors,
        }


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _with_retry(fn, retry, outcome: DeliveryOutcome):
    """TASK-093 ~ TASK-098: retry only recoverable errors."""
    attempts = 0
    last_exc: Exception | None = None
    while attempts <= retry.max_retries:
        attempts += 1
        try:
            result = fn()
            outcome.attempts = attempts
            return result
        except PermanentSendError:
            outcome.attempts = attempts
            raise
        except RecoverableSendError as exc:
            last_exc = exc
            if attempts > retry.max_retries:
                break
            log.warning("recoverable send error (attempt %d): %s", attempts, exc)
            time.sleep(retry.interval_seconds)
    outcome.attempts = attempts
    assert last_exc is not None
    raise last_exc


def _send_summary(client: WeChatClient, recipient: dict, bundle: ReportBundle, retry, outcome: DeliveryOutcome) -> None:
    text = (bundle.summary or "").strip()
    if not text:
        raise PermanentSendError("summary is empty; aborting (TASK-073)")

    def _do() -> SendResult:
        result = client.send_text(recipient, text)
        if not result.ok:
            raise RecoverableSendError(result.detail or "send_text returned not ok")
        return result

    _with_retry(_do, retry, outcome)
    outcome.summary_ok = True


def _send_full(client: WeChatClient, recipient: dict, bundle: ReportBundle, cfg: WeChatConfig, retry, outcome: DeliveryOutcome) -> None:
    path = bundle.full_text_path
    if path is None:
        raise PermanentSendError("no full report text available")
    text = path.read_text(encoding="utf-8", errors="replace")
    if not text.strip():
        raise PermanentSendError(f"full report is empty: {path}")
    chunks = split_text(text) if cfg.split_long_text else [text]
    chunks = number_chunks(chunks, title=bundle.report_id)
    for idx, chunk in enumerate(chunks):

        def _do(c=chunk) -> SendResult:
            result = client.send_text(recipient, c)
            if not result.ok:
                raise RecoverableSendError(result.detail or "send_text returned not ok")
            return result

        _with_retry(_do, retry, outcome)
    outcome.full_ok = True


def _send_summary_pdf(client: WeChatClient, recipient: dict, bundle: ReportBundle, retry, outcome: DeliveryOutcome) -> None:
    """TASK-079 ~ TASK-083: summary first, then PDF; track partial success."""
    if bundle.pdf_path is None:
        raise PermanentSendError("no PDF to send")
    _send_summary(client, recipient, bundle, retry, outcome)
    # If the summary succeeded but the PDF fails, keep partial success.
    def _do() -> SendResult:
        result = client.send_file(recipient, bundle.pdf_path, caption=bundle.report_id)
        if not result.ok:
            raise RecoverableSendError(result.detail or "send_file returned not ok")
        return result

    _with_retry(_do, retry, outcome)
    outcome.pdf_ok = True


def send_report(
    bundle: ReportBundle,
    client: WeChatClient,
    recipient: dict,
    cfg: WeChatConfig,
    source: str = "manual",
) -> DeliveryOutcome:
    """Dispatch to the right mode. Never generates reports/summaries/PDFs."""
    outcome = DeliveryOutcome(
        execution_id=uuid.uuid4().hex[:12],
        source=source,
        report_id=bundle.report_id,
        mode=cfg.default_mode,
        started_at=_now(),
    )
    mode = cfg.default_mode
    try:
        if mode == "summary":
            _send_summary(client, recipient, bundle, cfg.retry, outcome)
        elif mode == "full":
            _send_full(client, recipient, bundle, cfg, cfg.retry, outcome)
        elif mode == "summary_pdf":
            _send_summary_pdf(client, recipient, bundle, cfg.retry, outcome)
        else:
            raise PermanentSendError(f"unknown mode {mode!r}")
        outcome.status = "success"
    except PermanentSendError as exc:
        outcome.errors.append(str(exc))
        outcome.status = "partial" if (outcome.summary_ok and mode == "summary_pdf") else "failed"
    except RecoverableSendError as exc:
        outcome.errors.append(str(exc))
        outcome.status = "partial" if (outcome.summary_ok and mode == "summary_pdf") else "failed"
    finally:
        outcome.finished_at = _now()
        _record_state(outcome)
        log.info(
            "wechat send done: id=%s source=%s mode=%s status=%s summary=%s pdf=%s",
            outcome.execution_id, source, mode, outcome.status,
            outcome.summary_ok, outcome.pdf_ok,
        )
    return outcome


def _record_state(outcome: DeliveryOutcome) -> None:
    """TASK-099 ~ TASK-104: append a non-sensitive delivery record."""
    try:
        d = config_dir()
        d.mkdir(parents=True, exist_ok=True)
        path = d / STATE_FILE
        records: list[dict] = []
        if path.exists():
            try:
                records = json.loads(path.read_text(encoding="utf-8"))
                if not isinstance(records, list):
                    records = []
            except (OSError, json.JSONDecodeError):
                # TASK-106: don't crash on state corruption, but don't claim success either.
                log.warning("delivery-state.json corrupt; starting a fresh record list")
                records = []
        records.append(outcome.to_dict())
        records = records[-500:]
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")
        if os.name == "posix":
            os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except OSError as exc:
        log.warning("could not record delivery state: %s", exc)
