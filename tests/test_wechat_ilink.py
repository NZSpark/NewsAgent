"""Protocol-level tests for the real iLink client (TASK-151 ~ TASK-162).

Uses httpx.MockTransport so no real WeChat account is needed. These verify the
request/response wiring and error classification against the Hermes-derived
protocol, not live server behaviour.
"""
from __future__ import annotations

import base64
import json

import httpx
import pytest

from newsagent.wechat.client import (
    AuthExpiredError,
    NotBoundError,
    PermanentSendError,
    RecoverableSendError,
)
from newsagent.wechat.ilink import (
    ILinkClient,
    _aes128_ecb_encrypt,
    _is_session_expired,
    _is_stale_session_ret,
    _pkcs7_pad,
)

ACCOUNT = {"account_id": "bot123", "token": "tok", "base_url": "https://ilink.test"}


def _client(handler, **kw) -> ILinkClient:
    return ILinkClient(ACCOUNT, transport=httpx.MockTransport(handler), **kw)


def test_send_text_success():
    captured = {}

    def handler(req: httpx.Request) -> httpx.Response:
        captured["url"] = str(req.url)
        captured["auth"] = req.headers.get("Authorization")
        captured["body"] = json.loads(req.content)
        return httpx.Response(200, json={"ret": 0, "errcode": 0})

    client = _client(handler)
    result = client.send_text({"id": "user1", "context_token": "ctx"}, "hello")
    assert result.ok
    assert captured["url"].endswith("/ilink/bot/sendmessage")
    assert captured["auth"] == "Bearer tok"
    msg = captured["body"]["msg"]
    assert msg["to_user_id"] == "user1"
    assert msg["context_token"] == "ctx"
    assert msg["message_type"] == 2
    assert msg["item_list"][0]["text_item"]["text"] == "hello"


def test_send_text_requires_recipient():
    client = _client(lambda r: httpx.Response(200, json={"ret": 0}))
    with pytest.raises(NotBoundError):
        client.send_text({}, "hi")


def test_send_empty_text_rejected():
    client = _client(lambda r: httpx.Response(200, json={"ret": 0}))
    with pytest.raises(PermanentSendError):
        client.send_text({"id": "u"}, "   ")


def test_session_expired_raises_auth_error():
    def handler(req):
        return httpx.Response(200, json={"ret": -14, "errmsg": "session expired"})

    client = _client(handler)
    with pytest.raises(AuthExpiredError):
        client.send_text({"id": "u"}, "hi")


def test_rate_limit_is_recoverable():
    def handler(req):
        return httpx.Response(200, json={"ret": -2, "errmsg": "rate limited"})

    client = _client(handler)
    with pytest.raises(RecoverableSendError):
        client.send_text({"id": "u"}, "hi")


def test_stale_session_not_ready():
    def handler(req):
        return httpx.Response(200, json={"ret": -2, "errmsg": "prepare failed"})

    client = _client(handler)
    with pytest.raises(NotBoundError):
        client.send_text({"id": "u"}, "hi")


def test_unknown_response_never_success():
    def handler(req):
        return httpx.Response(200, json={"ret": 99, "errcode": 7, "errmsg": "weird"})

    client = _client(handler)
    with pytest.raises(PermanentSendError):
        client.send_text({"id": "u"}, "hi")


def test_http_5xx_recoverable():
    def handler(req):
        return httpx.Response(503, text="busy")

    client = _client(handler)
    with pytest.raises(RecoverableSendError):
        client.send_text({"id": "u"}, "hi")


def test_get_updates_returns_msgs():
    def handler(req):
        return httpx.Response(200, json={"ret": 0, "msgs": [{"from_user_id": "peer"}], "get_updates_buf": "next"})

    client = _client(handler)
    resp = client.get_updates("")
    assert resp["msgs"][0]["from_user_id"] == "peer"
    assert resp["get_updates_buf"] == "next"


def test_get_updates_timeout_is_not_error():
    def handler(req):
        raise httpx.ReadTimeout("timeout", request=req)

    client = _client(handler)
    resp = client.get_updates("buf")
    assert resp["ret"] == 0
    assert resp["get_updates_buf"] == "buf"


def test_send_file_upload_flow(tmp_path):
    pdf = tmp_path / "r.pdf"
    pdf.write_bytes(b"%PDF-1.4 hello world")
    calls = {"upload": 0, "send": 0}

    def handler(req: httpx.Request) -> httpx.Response:
        url = str(req.url)
        if url.endswith("/getuploadurl"):
            return httpx.Response(200, json={"ret": 0, "upload_full_url": "https://cdn.test/upload"})
        if url == "https://cdn.test/upload":
            calls["upload"] += 1
            return httpx.Response(200, headers={"x-encrypted-param": "ENCPARAM"})
        if url.endswith("/sendmessage"):
            calls["send"] += 1
            body = json.loads(req.content)
            items = body["msg"]["item_list"]
            if "file_item" in items[-1]:
                item = items[-1]["file_item"]
                assert item["media"]["encrypt_query_param"] == "ENCPARAM"
                assert item["file_name"] == "r.pdf"
            else:
                assert items[0]["text_item"]["text"] == "cap"
            return httpx.Response(200, json={"ret": 0, "errcode": 0})
        return httpx.Response(404)

    client = _client(handler)
    result = client.send_file({"id": "u"}, pdf, caption="cap")
    assert result.ok
    assert calls["upload"] == 1
    assert calls["send"] == 2  # caption + file


# ---- AES helpers ---- #

def test_aes_roundtrip_length():
    key = b"0" * 16
    ct = _aes128_ecb_encrypt(b"hello", key)
    assert len(ct) % 16 == 0
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

    dec = Cipher(algorithms.AES(key), modes.ECB()).decryptor()
    out = dec.update(ct) + dec.finalize()
    assert out.startswith(b"hello")


def test_pkcs7_pad():
    assert _pkcs7_pad(b"1234567890123456") == b"1234567890123456" + bytes([16]) * 16
    assert _pkcs7_pad(b"abc") == b"abc" + bytes([13]) * 13


def test_is_session_expired():
    assert _is_session_expired({"errmsg": "prepare failed"}, -2, -2)
    assert _is_session_expired({}, -14, 0)
    assert not _is_session_expired({"errmsg": "rate limited"}, -2, -2)


def test_is_stale_session_ret():
    assert _is_stale_session_ret(-2, None, "unknown error")
    assert not _is_stale_session_ret(-2, None, "rate limited")
