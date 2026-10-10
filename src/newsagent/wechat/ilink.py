"""Real iLink Bot protocol client (TASK-009 ~ TASK-018, TASK-033 ~ TASK-047, TASK-066 ~ TASK-082).

A synchronous ``httpx`` port of the essential iLink flows from the Hermes
Weixin adapter (MIT, Copyright (c) 2025 Nous Research):
https://github.com/NousResearch/hermes-agent/blob/dce1e9b37581dd62e480a9064dc04a709c2940d3/gateway/platforms/weixin.py

Only the parts NewsAgent needs are ported: QR login, getupdates long-poll,
context_token handling, sendmessage (text), and encrypted media upload.
Hermes' async/aiohttp stack, agent dispatch and group handling are dropped.

NOTE: protocol behaviour is derived from the reference source, NOT yet verified
against a real account end-to-end. See doc/wechat_phase0_survey.md.
"""
from __future__ import annotations

import base64
import hashlib
import json
import secrets
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx

from ..logging_setup import get_logger
from .client import (
    AuthExpiredError,
    NotBoundError,
    PermanentSendError,
    RecoverableSendError,
    SendResult,
)

log = get_logger("wechat.ilink")

# --- Protocol constants (mirrors Hermes gateway/platforms/weixin.py) --- #
ILINK_BASE_URL = "https://ilinkai.weixin.qq.com"
WEIXIN_CDN_BASE_URL = "https://novac2c.cdn.weixin.qq.com/c2c"
ILINK_APP_ID = "bot"
CHANNEL_VERSION = "2.2.0"
ILINK_APP_CLIENT_VERSION = (2 << 16) | (2 << 8) | 0

EP_GET_UPDATES = "ilink/bot/getupdates"
EP_SEND_MESSAGE = "ilink/bot/sendmessage"
EP_GET_UPLOAD_URL = "ilink/bot/getuploadurl"
EP_GET_BOT_QR = "ilink/bot/get_bot_qrcode"
EP_GET_QR_STATUS = "ilink/bot/get_qrcode_status"

LONG_POLL_TIMEOUT_MS = 35_000
API_TIMEOUT_MS = 15_000
QR_TIMEOUT_MS = 35_000

SESSION_EXPIRED_ERRCODE = -14
RATE_LIMIT_ERRCODE = -2

MEDIA_IMAGE, MEDIA_VIDEO, MEDIA_FILE, MEDIA_VOICE = 1, 2, 3, 4
ITEM_TEXT, ITEM_IMAGE, ITEM_VOICE, ITEM_FILE, ITEM_VIDEO = 1, 2, 3, 4, 5
MSG_TYPE_BOT, MSG_STATE_FINISH = 2, 2

# iLink chunks text at ~2048 chars; Hermes splits at 1800 to stay under.
MAX_TEXT_LEN = 1800


@dataclass
class QrSession:
    qrcode: str
    qrcode_url: str


@dataclass
class LoginCredentials:
    account_id: str
    token: str
    base_url: str
    user_id: str = ""

    def to_dict(self) -> dict:
        return {
            "account_id": self.account_id,
            "token": self.token,
            "base_url": self.base_url,
            "user_id": self.user_id,
        }


def _headers(token: str | None, body: str) -> dict[str, str]:
    uin = base64.b64encode(
        str(int.from_bytes(secrets.token_bytes(4), "big")).encode("utf-8")
    ).decode("ascii")
    headers = {
        "Content-Type": "application/json",
        "AuthorizationType": "ilink_bot_token",
        "Content-Length": str(len(body.encode("utf-8"))),
        "X-WECHAT-UIN": uin,
        "iLink-App-Id": ILINK_APP_ID,
        "iLink-App-ClientVersion": str(ILINK_APP_CLIENT_VERSION),
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _pkcs7_pad(data: bytes, block_size: int = 16) -> bytes:
    pad_len = block_size - (len(data) % block_size)
    return data + bytes([pad_len] * pad_len)


def _aes128_ecb_encrypt(plaintext: bytes, key: bytes) -> bytes:
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

    encryptor = Cipher(algorithms.AES(key), modes.ECB()).encryptor()
    return encryptor.update(_pkcs7_pad(plaintext)) + encryptor.finalize()


def _is_stale_session_ret(ret: Any, errcode: Any, errmsg: str | None) -> bool:
    return (ret == RATE_LIMIT_ERRCODE or errcode == RATE_LIMIT_ERRCODE) and (
        errmsg or ""
    ).lower() in {"unknown error", "prepare failed"}


def _is_session_expired(resp: dict, ret: Any, errcode: Any) -> bool:
    return SESSION_EXPIRED_ERRCODE in (ret, errcode) or _is_stale_session_ret(
        ret, errcode, resp.get("errmsg") or resp.get("msg")
    )


def _session_not_ready(ret: Any, errcode: Any, errmsg: Any) -> NotBoundError:
    return NotBoundError(
        f"iLink sendmessage session not ready: ret={ret} errcode={errcode} "
        f"errmsg={errmsg or 'unknown error'} — the user must send the bot a "
        "message first (or re-pair)."
    )


def _classify_http_error(exc: httpx.HTTPError) -> RecoverableSendError:
    return RecoverableSendError(f"iLink network error: {type(exc).__name__}: {exc}")


class ILinkClient:
    """Synchronous iLink Bot client implementing the WeChatClient protocol."""

    def __init__(
        self,
        account: dict,
        *,
        cdn_base_url: str = WEIXIN_CDN_BASE_URL,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.account_id = str(account.get("account_id") or "")
        self.token = str(account.get("token") or "")
        self.base_url = str(account.get("base_url") or ILINK_BASE_URL).rstrip("/")
        self.cdn_base_url = cdn_base_url.rstrip("/")
        if not self.token:
            raise PermanentSendError("iLink client requires a token; run `news wechat login`")
        kwargs: dict[str, Any] = {
            "timeout": httpx.Timeout(API_TIMEOUT_MS / 1000),
            "follow_redirects": True,
            "verify": _ca_bundle(),
        }
        if transport is not None:
            kwargs["transport"] = transport
        self._http = httpx.Client(**kwargs)

    # ---- low-level API ---- #

    def _post(self, endpoint: str, payload: dict, *, timeout_ms: int = API_TIMEOUT_MS) -> dict:
        body = json.dumps(
            {**payload, "base_info": {"channel_version": CHANNEL_VERSION}},
            ensure_ascii=False,
            separators=(",", ":"),
        )
        url = f"{self.base_url}/{endpoint}"
        try:
            resp = self._http.post(
                url, content=body, headers=_headers(self.token, body),
                timeout=httpx.Timeout(timeout_ms / 1000),
            )
        except httpx.HTTPError as exc:
            raise _classify_http_error(exc) from exc
        if resp.status_code >= 400:
            raise RecoverableSendError(f"iLink {endpoint} HTTP {resp.status_code}: {resp.text[:200]}")
        return resp.json()

    def _get(self, endpoint: str, *, base_url: str | None = None, timeout_ms: int = QR_TIMEOUT_MS) -> dict:
        base = base_url or ILINK_BASE_URL
        headers = {
            "iLink-App-Id": ILINK_APP_ID,
            "iLink-App-ClientVersion": str(ILINK_APP_CLIENT_VERSION),
        }
        try:
            resp = self._http.get(f"{base.rstrip('/')}/{endpoint}", headers=headers, timeout=httpx.Timeout(timeout_ms / 1000))
        except httpx.HTTPError as exc:
            raise _classify_http_error(exc) from exc
        if resp.status_code >= 400:
            raise RecoverableSendError(f"iLink {endpoint} HTTP {resp.status_code}: {resp.text[:200]}")
        return resp.json()

    # ---- login (TASK-033 ~ TASK-036) ---- #

    @staticmethod
    def fetch_qr(bot_type: str = "3") -> QrSession:
        with httpx.Client(timeout=httpx.Timeout(QR_TIMEOUT_MS / 1000), verify=_ca_bundle()) as http:
            resp = http.get(
                f"{ILINK_BASE_URL}/{EP_GET_BOT_QR}?bot_type={bot_type}",
                headers={"iLink-App-Id": ILINK_APP_ID, "iLink-App-ClientVersion": str(ILINK_APP_CLIENT_VERSION)},
            )
            resp.raise_for_status()
            data = resp.json()
        return QrSession(
            qrcode=str(data.get("qrcode") or ""),
            qrcode_url=str(data.get("qrcode_img_content") or ""),
        )

    @staticmethod
    def poll_qr_status(qrcode: str, *, base_url: str = ILINK_BASE_URL) -> dict:
        with httpx.Client(timeout=httpx.Timeout(QR_TIMEOUT_MS / 1000), verify=_ca_bundle()) as http:
            resp = http.get(
                f"{base_url.rstrip('/')}/{EP_GET_QR_STATUS}?qrcode={qrcode}",
                headers={"iLink-App-Id": ILINK_APP_ID, "iLink-App-ClientVersion": str(ILINK_APP_CLIENT_VERSION)},
            )
            resp.raise_for_status()
            return resp.json()

    # ---- getupdates (TASK-039 / TASK-040) ---- #

    def get_updates(self, sync_buf: str, *, timeout_ms: int = LONG_POLL_TIMEOUT_MS) -> dict:
        """Long-poll inbound messages. A long-poll timeout is NOT an error."""
        try:
            return self._post(EP_GET_UPDATES, {"get_updates_buf": sync_buf}, timeout_ms=timeout_ms)
        except RecoverableSendError as exc:
            if "Timeout" in str(exc) or "ReadTimeout" in str(exc):
                return {"ret": 0, "msgs": [], "get_updates_buf": sync_buf}
            raise

    # ---- sendmessage (TASK-067 / TASK-070) ---- #

    def _send_items(self, to: str, item_list: list[dict], context_token: str | None, client_id: str) -> dict:
        message: dict[str, Any] = {
            "from_user_id": "",
            "to_user_id": to,
            "client_id": client_id,
            "message_type": MSG_TYPE_BOT,
            "message_state": MSG_STATE_FINISH,
            "item_list": item_list,
        }
        if context_token:
            message["context_token"] = context_token
        return self._post(EP_SEND_MESSAGE, {"msg": message})

    def send_text(self, recipient: dict, text: str) -> SendResult:
        to = str(recipient.get("id") or "")
        if not to:
            raise NotBoundError("recipient id missing")
        if not text or not text.strip():
            raise PermanentSendError("cannot send empty text")
        context_token = recipient.get("context_token")
        client_id = f"newsagent-{secrets.token_hex(8)}"
        resp = self._send_items(to, [{"type": ITEM_TEXT, "text_item": {"text": text}}], context_token, client_id)
        return self._interpret_send(resp, client_id)

    def _interpret_send(self, resp: dict, client_id: str) -> SendResult:
        ret, errcode = resp.get("ret"), resp.get("errcode")
        if (ret in (0, None)) and (errcode in (0, None)):
            return SendResult(ok=True, message_id=client_id)
        errmsg = resp.get("errmsg") or resp.get("msg")
        if SESSION_EXPIRED_ERRCODE in (ret, errcode):
            raise AuthExpiredError(
                f"iLink session expired (ret={ret} errcode={errcode}); re-login with `news wechat login`"
            )
        if _is_stale_session_ret(ret, errcode, errmsg):
            raise _session_not_ready(ret, errcode, errmsg)
        if ret == RATE_LIMIT_ERRCODE or errcode == RATE_LIMIT_ERRCODE:
            raise RecoverableSendError(f"iLink rate limited: {errmsg or 'rate limited'}")
        # TASK-070: unknown response is never treated as success.
        raise PermanentSendError(f"iLink sendmessage error: ret={ret} errcode={errcode} errmsg={errmsg or 'unknown'}")

    # ---- media upload (TASK-068) ---- #

    def send_file(self, recipient: dict, path: Path, caption: str = "") -> SendResult:
        to = str(recipient.get("id") or "")
        if not to:
            raise NotBoundError("recipient id missing")
        try:
            from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes  # noqa: F401
        except ImportError as exc:
            raise PermanentSendError(
                "sending files needs `cryptography`; install with: pip install 'newsagent[wechat]'"
            ) from exc
        plaintext = path.read_bytes()
        media_type = MEDIA_FILE
        filekey = secrets.token_hex(16)
        aes_key = secrets.token_bytes(16)
        rawsize = len(plaintext)
        rawfilemd5 = hashlib.md5(plaintext).hexdigest()
        upload = self._post(
            EP_GET_UPLOAD_URL,
            {
                "filekey": filekey,
                "media_type": media_type,
                "to_user_id": to,
                "rawsize": rawsize,
                "rawfilemd5": rawfilemd5,
                "filesize": ((rawsize + 16) // 16) * 16,
                "no_need_thumb": True,
                "aeskey": aes_key.hex(),
            },
        )
        upload_param = str(upload.get("upload_param") or "")
        upload_url = str(upload.get("upload_full_url") or "") or (
            f"{self.cdn_base_url}/upload?encrypted_query_param={quote(upload_param, safe='')}&filekey={quote(filekey, safe='')}"
            if upload_param
            else ""
        )
        if not upload_url:
            raise RecoverableSendError(f"getUploadUrl returned no upload url: {upload}")
        ciphertext = _aes128_ecb_encrypt(plaintext, aes_key)
        try:
            up = self._http.post(upload_url, content=ciphertext, headers={"Content-Type": "application/octet-stream"}, timeout=httpx.Timeout(120))
        except httpx.HTTPError as exc:
            raise _classify_http_error(exc) from exc
        encrypted_param = up.headers.get("x-encrypted-param") if up.status_code == 200 else None
        if not encrypted_param:
            raise RecoverableSendError(f"CDN upload failed HTTP {up.status_code}: {up.text[:200]}")
        item = {
            "type": ITEM_FILE,
            "file_item": {
                "media": {
                    "encrypt_query_param": encrypted_param,
                    # iLink wants base64(hex string), not base64(raw bytes).
                    "aes_key": base64.b64encode(aes_key.hex().encode("ascii")).decode("ascii"),
                    "encrypt_type": 1,
                },
                "file_name": path.name,
                "len": str(rawsize),
            },
        }
        item_lists: list[list[dict]] = [[item]]
        if caption:
            item_lists.insert(0, [{"type": ITEM_TEXT, "text_item": {"text": caption}}])
        context_token = recipient.get("context_token")
        last = ""
        for item_list in item_lists:
            last = f"newsagent-{secrets.token_hex(8)}"
            resp = self._send_items(to, item_list, context_token, last)
            self._interpret_send(resp, last)
        return SendResult(ok=True, message_id=last)

    def close(self) -> None:
        self._http.close()


def _ca_bundle() -> Any:
    """Prefer certifi's CA bundle (Tencent endpoint fails some system stores)."""
    try:
        import certifi

        return certifi.where()
    except ImportError:
        return True
