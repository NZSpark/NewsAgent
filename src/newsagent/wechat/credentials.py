"""Protected credential + recipient state storage (TASK-026 ~ TASK-032).

Sensitive files live under the WeChat config dir with POSIX 0700/0600 and
atomic replacement. Corrupt files raise instead of being silently overwritten.
"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from ..logging_setup import NewsAgentError, get_logger
from .config import config_dir

log = get_logger("wechat.credentials")

ACCOUNT_FILE = "account.json"   # TASK-027
RECIPIENT_FILE = "recipient.json"  # TASK-028


class CredentialError(NewsAgentError):
    """Credential/recipient state could not be read, written, or is corrupt."""


def _dir_mode() -> int:
    return 0o700


def _file_mode() -> int:
    return 0o600


def ensure_dir(base: Path | None = None) -> Path:
    """TASK-029: create the protected dir and set 0700 on POSIX."""
    d = base or config_dir()
    d.mkdir(parents=True, exist_ok=True)
    if os.name == "posix":
        try:
            os.chmod(d, _dir_mode())
        except OSError as exc:  # permission failure must be explicit
            raise CredentialError(f"cannot set permissions on {d}: {exc}") from exc
    return d


def _read_json(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise CredentialError(f"cannot read {path}: {exc}") from exc
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        # TASK-031: never silently overwrite a corrupt credential file.
        raise CredentialError(
            f"corrupt or incompatible state file {path}: {exc}. "
            "Back it up or re-login instead of overwriting."
        ) from exc
    if not isinstance(data, dict):
        raise CredentialError(f"state file {path} must contain a JSON object")
    return data


def _atomic_write(path: Path, data: dict) -> None:
    """TASK-030: write via temp file + atomic replace, 0600."""
    ensure_dir(path.parent)
    fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=".tmp-", suffix=".json")
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2)
        if os.name == "posix":
            os.chmod(tmp, _file_mode())
        os.replace(tmp, path)
    except OSError as exc:
        tmp.unlink(missing_ok=True)
        raise CredentialError(f"cannot write {path}: {exc}") from exc


# ----------------------------- account ----------------------------- #

def save_account(account: dict, base: Path | None = None) -> Path:
    """Persist Bot login credentials (TASK-027). Never log the content."""
    path = (base or config_dir()) / ACCOUNT_FILE
    _atomic_write(path, account)
    log.info("wechat account credentials saved")  # no token content
    return path


def load_account(base: Path | None = None) -> dict:
    return _read_json((base or config_dir()) / ACCOUNT_FILE)


def clear_account(base: Path | None = None) -> None:
    path = (base or config_dir()) / ACCOUNT_FILE
    path.unlink(missing_ok=True)


# ---------------------------- recipient ---------------------------- #

def save_recipient(recipient: dict, base: Path | None = None) -> Path:
    """Persist the single fixed recipient + required context (TASK-042/TASK-046)."""
    if not recipient.get("id"):
        raise CredentialError("recipient record requires an 'id' field")
    path = (base or config_dir()) / RECIPIENT_FILE
    _atomic_write(path, recipient)
    log.info("wechat recipient bound")
    return path


def load_recipient(base: Path | None = None) -> dict:
    return _read_json((base or config_dir()) / RECIPIENT_FILE)


def clear_recipient(base: Path | None = None) -> None:
    path = (base or config_dir()) / RECIPIENT_FILE
    path.unlink(missing_ok=True)


def is_bound(base: Path | None = None) -> bool:
    """TASK-045: whether a recipient + required context exist."""
    rec = load_recipient(base)
    return bool(rec.get("id"))


def redacted_status(base: Path | None = None) -> dict:
    """TASK-032 / TASK-048: non-sensitive summary for `wechat status`."""
    account = load_account(base)
    recipient = load_recipient(base)
    return {
        "logged_in": bool(account),
        "bound": bool(recipient.get("id")),
        # never expose raw ids/tokens
        "recipient_bound": bool(recipient.get("id")),
    }
