"""QR login + inbound polling / recipient binding (TASK-033 ~ TASK-047)."""
from __future__ import annotations

import time
from pathlib import Path

from ..logging_setup import get_logger
from .client import NotBoundError
from .credentials import config_dir, save_account, save_recipient
from .ilink import ILINK_BASE_URL, ILinkClient

log = get_logger("wechat.login")


def _print_qr(url: str) -> None:
    """Render the liteapp URL as an ASCII QR; fall back to printing the URL."""
    if url:
        print(url)
    try:
        import qrcode

        qr = qrcode.QRCode()
        qr.add_data(url)
        qr.make(fit=True)
        qr.print_ascii(invert=True)
    except Exception as exc:  # noqa: BLE001
        print(f"（终端二维码渲染失败: {exc}，请直接打开上面的二维码链接）")


def qr_login(*, bot_type: str = "3", timeout_seconds: int = 480) -> dict | None:
    """Fetch a QR, poll until confirmed, persist credentials (TASK-033~036)."""
    session = ILinkClient.fetch_qr(bot_type)
    if not session.qrcode:
        log.error("QR response missing qrcode")
        return None
    print("\n请使用微信扫描以下二维码：")
    _print_qr(session.qrcode_url or session.qrcode)

    deadline = time.monotonic() + timeout_seconds
    current_base_url = ILINK_BASE_URL
    refresh_count = 0
    while time.monotonic() < deadline:
        try:
            status_resp = ILinkClient.poll_qr_status(session.qrcode, base_url=current_base_url)
        except Exception as exc:  # noqa: BLE001
            log.warning("QR poll error: %s", exc)
            time.sleep(1)
            continue
        status = str(status_resp.get("status") or "wait")
        if status == "wait":
            print(".", end="", flush=True)
        elif status == "scaned":
            print("\n已扫码，请在微信里确认...")
        elif status == "scaned_but_redirect" and status_resp.get("redirect_host"):
            current_base_url = f"https://{status_resp['redirect_host']}"
        elif status == "expired":
            refresh_count += 1
            if refresh_count > 3:
                print("\n二维码多次过期，请重新执行登录。")
                return None
            print(f"\n二维码已过期，正在刷新... ({refresh_count}/3)")
            session = ILinkClient.fetch_qr(bot_type)
            _print_qr(session.qrcode_url or session.qrcode)
        elif status == "confirmed":
            account_id = str(status_resp.get("ilink_bot_id") or "")
            token = str(status_resp.get("bot_token") or "")
            if not account_id or not token:
                log.error("QR confirmed but credential payload incomplete")
                return None
            creds = {
                "account_id": account_id,
                "token": token,
                "base_url": str(status_resp.get("baseurl") or ILINK_BASE_URL),
                "user_id": str(status_resp.get("ilink_user_id") or ""),
            }
            save_account(creds)
            print(f"\n微信连接成功，account_id={account_id[:8]}…")
            return creds
        time.sleep(1)
    print("\n微信登录超时。")
    return None


def bind_recipient(*, timeout_seconds: int = 300) -> dict | None:
    """Poll getupdates until the target user messages the bot; save recipient (TASK-039~044)."""
    from .credentials import load_account

    account = load_account()
    if not account:
        raise NotBoundError("not logged in; run `news wechat login` first")
    client = ILinkClient(account)
    sync_path = config_dir() / "sync.json"
    sync_buf = ""
    try:
        print("请用目标个人微信向该 Bot 主动发送一条消息（等待入站消息）...")
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            resp = client.get_updates(sync_buf)
            if resp.get("get_updates_buf"):
                sync_buf = str(resp["get_updates_buf"])
                sync_path.write_text(f'{{"get_updates_buf": {sync_buf!r}}}', encoding="utf-8")
            for msg in resp.get("msgs") or []:
                sender = str(msg.get("from_user_id") or "").strip()
                if not sender or sender == client.account_id:
                    continue
                context_token = str(msg.get("context_token") or "").strip()
                recipient = {"id": sender, "context_token": context_token, "account_id": client.account_id}
                save_recipient(recipient)
                print(f"\n已绑定收件人：{sender[:8]}…")
                return recipient
    finally:
        client.close()
    print("\n未收到入站消息，绑定超时。")
    return None
