"""Send service: three modes + retry + status (TASK-071 ~ TASK-106)."""
from __future__ import annotations

import hashlib
import json
import os
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from ..logging_setup import get_logger
from .client import (
    PermanentSendError,
    RecoverableSendError,
    SendResult,
    UncertainSendError,
    WeChatClient,
)
from .config import WeChatConfig, config_dir
from .reports import ReportBundle

log = get_logger("wechat.sender")

STATE_FILE = "delivery-state.json"  # TASK-099..TASK-104

# A summary is sent as a single message. Cap it below iLink's ~2048 chunk
# limit so it never splits; longer summaries are truncated with an ellipsis.
MAX_SUMMARY_CHARS = 1800


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
    status: str = "pending"  # success | partial | failed | uncertain
    errors: list[str] = field(default_factory=list)
    idempotency_key: str | None = None
    completed_parts: list[str] = field(default_factory=list)
    in_flight_part: str | None = None
    uncertain_parts: list[str] = field(default_factory=list)
    duplicate_skipped: bool = False

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
            "idempotency_key": self.idempotency_key,
            "completed_parts": self.completed_parts,
            "in_flight_part": self.in_flight_part,
            "uncertain_parts": self.uncertain_parts,
            "duplicate_skipped": self.duplicate_skipped,
        }


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# Only sources explicitly identified as unattended jobs use automatic resend
# protection. Manual invocations remain an intentional way to resend a report.
_AUTOMATED_SOURCES = frozenset({"schedule", "scheduled", "scheduler", "cron", "launchd"})


def _file_digest(path: Path | None) -> str:
    if path is None:
        return ""
    if not path.is_file():
        return f"missing:{path.name}"
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _idempotency_key(bundle: ReportBundle, mode: str, recipient: dict) -> str:
    """Stable content identity for a recipient/report pair; not a server key."""
    recipient_id = str(recipient.get("id") or recipient.get("user_id") or recipient.get("to_user_id") or "")
    identity: dict[str, str] = {
        "mode": mode,
        "report_id": bundle.report_id,
        # Do not persist personal IDs in plaintext in delivery-state.json.
        "recipient_sha256": hashlib.sha256(recipient_id.encode("utf-8")).hexdigest(),
    }
    identity["summary"] = hashlib.sha256((bundle.summary or "").encode("utf-8")).hexdigest()
    serialized = json.dumps(identity, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _read_state_records() -> list[dict]:
    path = config_dir() / STATE_FILE
    if not path.exists():
        return []
    try:
        records = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PermanentSendError(
            f"delivery state is unreadable; refusing automatic resend protection bypass: {path}"
        ) from exc
    if not isinstance(records, list) or any(not isinstance(row, dict) for row in records):
        raise PermanentSendError(f"delivery state is not a valid record list: {path}")
    return records


def _find_previous_delivery(key: str) -> dict | None:
    for row in reversed(_read_state_records()):
        if row.get("idempotency_key") == key and row.get("source") in _AUTOMATED_SOURCES:
            return row
    return None


@contextmanager
def _delivery_lock():
    """Cross-process lock for check-send-record, preventing concurrent resends."""
    directory = config_dir()
    directory.mkdir(parents=True, exist_ok=True)
    lock_path = directory / "delivery.lock"
    with lock_path.open("a+") as handle:
        if os.name == "posix":
            import fcntl
            os.chmod(lock_path, 0o600)
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        elif os.name == "nt":
            import msvcrt
            handle.seek(0, os.SEEK_END)
            if handle.tell() == 0:
                handle.write("0")
                handle.flush()
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
            try:
                yield
            finally:
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            raise PermanentSendError("cross-process delivery locking is unsupported on this platform")


def _with_retry(fn, retry, outcome: DeliveryOutcome):
    """Retry only failures known to be safe to repeat.

    UncertainSendError is deliberately not caught here: the server may already
    have accepted the message, so retrying could deliver a duplicate.
    """
    attempts = 0
    last_exc: Exception | None = None
    while attempts <= retry.max_retries:
        attempts += 1
        try:
            result = fn()
            outcome.attempts += attempts
            return result
        except (PermanentSendError, UncertainSendError):
            outcome.attempts += attempts
            raise
        except RecoverableSendError as exc:
            last_exc = exc
            if attempts > retry.max_retries:
                break
            log.warning("recoverable send error (attempt %d): %s", attempts, exc)
            time.sleep(retry.interval_seconds)
    outcome.attempts += attempts
    assert last_exc is not None
    raise last_exc


def _send_part(part: str, fn, retry, outcome: DeliveryOutcome) -> SendResult | None:
    """Checkpoint each logical message before and after its network request."""
    if part in outcome.completed_parts:
        return None

    outcome.in_flight_part = part
    _record_state(outcome, strict=True)
    try:
        result = _with_retry(fn, retry, outcome)
    except UncertainSendError:
        if part not in outcome.uncertain_parts:
            outcome.uncertain_parts.append(part)
        outcome.in_flight_part = None
        outcome.status = "uncertain"
        _record_state(outcome, strict=True)
        raise
    except (PermanentSendError, RecoverableSendError):
        # These exception types promise that no remote acceptance is suspected.
        outcome.in_flight_part = None
        _record_state(outcome, strict=True)
        raise

    if part not in outcome.completed_parts:
        outcome.completed_parts.append(part)
    outcome.in_flight_part = None
    _record_state(outcome, strict=True)
    return result


def _send_summary(client: WeChatClient, recipient: dict, bundle: ReportBundle, retry, outcome: DeliveryOutcome) -> None:
    """Send the summary as ONE message. Never split (design revision 2026-10-10).

    iLink chunks text at ~2048 chars; the summary is well under that, and
    multi-message splitting is a poor WeChat reading experience. If the summary
    somehow exceeds the limit, send the first chunk only and note the truncation
    rather than splitting into several messages.
    """
    text = (bundle.summary or "").strip()
    if not text:
        raise PermanentSendError("summary is empty; aborting (TASK-073)")
    if len(text) > MAX_SUMMARY_CHARS:
        log.warning("summary is %d chars; truncating to %d", len(text), MAX_SUMMARY_CHARS)
        # Reserve one char for the ellipsis so the result never exceeds the limit.
        text = text[: MAX_SUMMARY_CHARS - 1].rstrip() + "…"

    def _do() -> SendResult:
        result = client.send_text(recipient, text)
        if not result.ok:
            raise RecoverableSendError(result.detail or "send_text returned not ok")
        return result

    _send_part("summary", _do, retry, outcome)
    outcome.summary_ok = True


def _send_report_unlocked(
    bundle: ReportBundle,
    client: WeChatClient,
    recipient: dict,
    cfg: WeChatConfig,
    source: str = "manual",
) -> DeliveryOutcome:
    """Dispatch a report with checkpointed progress and conservative resend protection.

    Manual sends are always allowed. Unattended sources suppress already-successful
    deliveries and resume known partial progress. If a previous process stopped
    with a request in flight, the outcome is uncertain and automatic resend is
    refused because iLink does not document an idempotency guarantee.
    """
    mode = cfg.default_mode
    outcome = DeliveryOutcome(
        execution_id=uuid.uuid4().hex[:12],
        source=source,
        report_id=bundle.report_id,
        mode=mode,
        started_at=_now(),
        idempotency_key=_idempotency_key(bundle, mode, recipient),
    )
    try:
        if source in _AUTOMATED_SOURCES:
            previous = _find_previous_delivery(outcome.idempotency_key)
            if previous:
                if previous.get("status") == "success":
                    outcome.status = "success"
                    outcome.duplicate_skipped = True
                    outcome.completed_parts = list(previous.get("completed_parts") or [])
                    outcome.summary_ok = bool(previous.get("summary_ok")) or "summary" in outcome.completed_parts
                    outcome.pdf_ok = bool(previous.get("pdf_ok")) or "pdf" in outcome.completed_parts
                    outcome.full_ok = bool(previous.get("full_ok"))
                elif previous.get("status") == "uncertain" or previous.get("in_flight_part") or previous.get("uncertain_parts"):
                    outcome.status = "uncertain"
                    outcome.uncertain_parts = list(previous.get("uncertain_parts") or [])
                    in_flight = previous.get("in_flight_part")
                    if in_flight and in_flight not in outcome.uncertain_parts:
                        outcome.uncertain_parts.append(in_flight)
                    outcome.errors.append(
                        "Previous delivery may have reached WeChat but was not confirmed; "
                        "automatic resend was blocked to prevent duplicates. Check the chat "
                        "and delivery-state.json before a deliberate manual resend."
                    )
                else:
                    outcome.completed_parts = list(previous.get("completed_parts") or [])
                    outcome.summary_ok = "summary" in outcome.completed_parts or bool(previous.get("summary_ok"))
                    outcome.pdf_ok = "pdf" in outcome.completed_parts or bool(previous.get("pdf_ok"))
                    outcome.full_ok = bool(previous.get("full_ok"))

        if outcome.status == "pending":
            if mode == "summary":
                _send_summary(client, recipient, bundle, cfg.retry, outcome)
            else:
                raise PermanentSendError(f"unknown mode {mode!r}")
            outcome.status = "success"
    except UncertainSendError as exc:
        if outcome.in_flight_part and outcome.in_flight_part not in outcome.uncertain_parts:
            outcome.uncertain_parts.append(outcome.in_flight_part)
        outcome.in_flight_part = None
        outcome.errors.append(str(exc))
        outcome.status = "uncertain"
    except PermanentSendError as exc:
        outcome.errors.append(str(exc))
        outcome.status = "failed"
    except RecoverableSendError as exc:
        outcome.errors.append(str(exc))
        outcome.status = "failed"
    finally:
        outcome.finished_at = _now()
        # Checkpoint writes before each request fail closed. Final logging must
        # not mask the delivery outcome if the state file itself is corrupt.
        _record_state(outcome, strict=False)
        log.info(
            "wechat send done: id=%s source=%s mode=%s status=%s summary=%s pdf=%s duplicate_skipped=%s",
            outcome.execution_id, source, mode, outcome.status,
            outcome.summary_ok, outcome.pdf_ok, outcome.duplicate_skipped,
        )
    return outcome


def send_report(
    bundle: ReportBundle,
    client: WeChatClient,
    recipient: dict,
    cfg: WeChatConfig,
    source: str = "manual",
) -> DeliveryOutcome:
    """Serialize all deliveries so automated dedupe and checkpoints are race-safe."""
    with _delivery_lock():
        return _send_report_unlocked(bundle, client, recipient, cfg, source=source)


def _record_state(outcome: DeliveryOutcome, *, strict: bool = False) -> None:
    """Append delivery progress; strict checkpoints fail closed on corrupt state."""
    try:
        d = config_dir()
        d.mkdir(parents=True, exist_ok=True)
        path = d / STATE_FILE
        if strict:
            records = _read_state_records()
        else:
            records: list[dict] = []
            if path.exists():
                try:
                    candidate = json.loads(path.read_text(encoding="utf-8"))
                    if isinstance(candidate, list) and all(isinstance(row, dict) for row in candidate):
                        records = candidate
                    else:
                        log.warning("delivery-state.json has an invalid shape; preserving it and refusing to overwrite")
                        return
                except (OSError, json.JSONDecodeError):
                    log.warning("delivery-state.json is corrupt; preserving it rather than erasing delivery history")
                    return
        records.append(outcome.to_dict())
        records = records[-500:]
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")
        if os.name == "posix":
            os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except (OSError, PermanentSendError) as exc:
        if strict:
            if isinstance(exc, PermanentSendError):
                raise
            raise PermanentSendError(f"cannot safely persist delivery state: {exc}") from exc
        log.warning("could not record delivery state: %s", exc)
