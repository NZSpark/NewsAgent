"""WeChat delivery tests (TASK-143 ~ TASK-162, unit + mock-integration).

Real iLink protocol tests (TASK-163 ~ TASK-172) are NOT included: protocol is
blocked (doc/wechat_phase0_survey.md). These tests use a mock client.
"""
from __future__ import annotations

import json

import pytest

from newsagent.wechat.client import (
    PermanentSendError,
    RecoverableSendError,
    SendResult,
    UncertainSendError,
    number_chunks,
    split_text,
)
from newsagent.wechat.config import (
    RetryConfig,
    ScheduleConfig,
    WeChatConfig,
    WeChatConfigError,
    load_config,
    parse_config,
)
from newsagent.wechat.credentials import (
    CredentialError,
    clear_account,
    clear_recipient,
    is_bound,
    load_account,
    load_recipient,
    redacted_status,
    save_account,
    save_recipient,
)
from newsagent.wechat.reports import ReportBundle, ReportInputError, resolve_report
from newsagent.wechat.sender import send_report


@pytest.fixture()
def wechat_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("NEWSAGENT_WECHAT_DIR", str(tmp_path))
    return tmp_path


# --------------------------- config (TASK-143) --------------------------- #

def test_default_config():
    cfg = WeChatConfig()
    # Design revision 2026-10-10: only the summary text is sent.
    assert cfg.default_mode == "summary"
    assert cfg.retry.max_retries == 3
    assert cfg.retry.interval_seconds == 10


def test_invalid_mode_rejected():
    with pytest.raises(WeChatConfigError):
        WeChatConfig(default_mode="bogus")


def test_negative_retries_rejected():
    with pytest.raises(WeChatConfigError):
        RetryConfig(max_retries=-1)
    with pytest.raises(WeChatConfigError):
        RetryConfig(interval_seconds=-5)


def test_invalid_time_rejected():
    with pytest.raises(WeChatConfigError):
        ScheduleConfig(daily_time="25:00")
    with pytest.raises(WeChatConfigError):
        ScheduleConfig(daily_time="8am")


def test_mode_precedence():
    cfg = WeChatConfig(default_mode="summary")
    assert cfg.resolve_mode(cli_mode="summary") == "summary"
    assert cfg.resolve_mode(schedule_mode="summary") == "summary"
    assert cfg.resolve_mode() == "summary"


def test_removed_modes_rejected():
    # full / summary_pdf were removed by the design revision.
    with pytest.raises(WeChatConfigError):
        WeChatConfig(default_mode="summary_pdf")
    with pytest.raises(WeChatConfigError):
        WeChatConfig(default_mode="full")
    with pytest.raises(WeChatConfigError):
        WeChatConfig().resolve_mode(cli_mode="full")


def test_schedule_mode_inherits_default():
    cfg = WeChatConfig(default_mode="summary")
    assert cfg.resolve_mode(schedule_mode=None) == "summary"


def test_parse_config_section():
    cfg = parse_config({"wechat": {"default_mode": "summary", "retry": {"max_retries": 5}}})
    assert cfg.default_mode == "summary"
    assert cfg.retry.max_retries == 5


def test_load_missing_config_yields_defaults(wechat_dir):
    cfg = load_config(wechat_dir / "nonexistent.json")
    assert cfg.default_mode == "summary"


def test_load_corrupt_config_raises(wechat_dir):
    path = wechat_dir / "config.json"
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(WeChatConfigError):
        load_config(path)


# ------------------------ credentials (TASK-144) ------------------------ #

def test_save_load_account(wechat_dir):
    save_account({"token": "SECRET", "bot": "b"})
    assert load_account()["token"] == "SECRET"
    clear_account()
    assert load_account() == {}


def test_save_recipient_requires_id(wechat_dir):
    with pytest.raises(CredentialError):
        save_recipient({"ctx": "x"})


def test_bind_and_clear(wechat_dir):
    save_recipient({"id": "wxid_1", "ctx": "c"})
    assert is_bound()
    clear_recipient()
    assert not is_bound()


def test_corrupt_state_not_silently_overwritten(wechat_dir):
    (wechat_dir / "account.json").write_text("broken", encoding="utf-8")
    with pytest.raises(CredentialError):
        load_account()


def test_redacted_status_hides_ids(wechat_dir):
    save_account({"token": "SECRET"})
    save_recipient({"id": "wxid_secret", "ctx": "c"})
    status = redacted_status()
    assert status["logged_in"] and status["bound"]
    assert "wxid_secret" not in json.dumps(status)
    assert "SECRET" not in json.dumps(status)


@pytest.mark.skipif(__import__("os").name != "posix", reason="POSIX perms")
def test_file_permissions(wechat_dir):
    import stat

    save_account({"token": "x"})
    mode = stat.S_IMODE((wechat_dir / "account.json").stat().st_mode)
    assert mode == 0o600


# -------------------- report resolution (TASK-145/146/147) -------------------- #

def _write_report_set(tmp_path, *, with_pdf=True, with_summary=True):
    md = tmp_path / "daily_2026-10-10.md"
    md.write_text("# Report\n\nbody", encoding="utf-8")
    pdf = tmp_path / "daily_2026-10-10.pdf"
    if with_pdf:
        pdf.write_bytes(b"%PDF-1.4\n...")
    files = {"md": str(md)}
    if with_pdf:
        files["pdf"] = str(pdf)
    manifest = {
        "report_id": "daily-20261010T000000Z",
        "files": files,
        "summary": "SUMMARY" if with_summary else "",
    }
    (tmp_path / "daily_2026-10-10.manifest.json").write_text(
        json.dumps(manifest), encoding="utf-8"
    )
    return tmp_path


def test_resolve_summary(tmp_path):
    d = _write_report_set(tmp_path)
    b = resolve_report("summary", directory=d)
    assert b.summary == "SUMMARY"


def test_summary_missing_raises(tmp_path):
    d = _write_report_set(tmp_path, with_summary=False)
    with pytest.raises(ReportInputError):
        resolve_report("summary", directory=d)


def test_no_manifest_raises(tmp_path):
    with pytest.raises(ReportInputError):
        resolve_report("summary", directory=tmp_path)


def test_removed_modes_rejected_in_resolver(tmp_path):
    d = _write_report_set(tmp_path)
    with pytest.raises(ReportInputError):
        resolve_report("full", directory=d)
    with pytest.raises(ReportInputError):
        resolve_report("summary_pdf", directory=d)


# --------------------- sending (TASK-147/148/149/150) --------------------- #

class _MockClient:
    def __init__(self, text_fail_times=0, file_fail_times=0):
        self.sent: list[tuple[str, str]] = []
        self.text_fail_times = text_fail_times
        self.file_fail_times = file_fail_times

    def send_text(self, recipient, text):
        if self.text_fail_times > 0:
            self.text_fail_times -= 1
            raise RecoverableSendError("temporary")
        self.sent.append(("text", text))
        return SendResult(ok=True, message_id="m")

    def send_file(self, recipient, path, caption=""):
        if self.file_fail_times > 0:
            self.file_fail_times -= 1
            raise RecoverableSendError("temporary")
        self.sent.append(("file", str(path)))
        return SendResult(ok=True, message_id="m")


def test_send_summary(wechat_dir):
    client = _MockClient()
    bundle = ReportBundle("R", "SUMMARY", None, None)
    out = send_report(bundle, client, {"id": "x"}, WeChatConfig(default_mode="summary"))
    assert out.status == "success" and out.summary_ok


def test_send_summary_empty_fails(wechat_dir):
    bundle = ReportBundle("R", "   ", None, None)
    out = send_report(bundle, _MockClient(), {"id": "x"}, WeChatConfig(default_mode="summary"))
    assert out.status == "failed"


def test_summary_is_sent_as_single_message(wechat_dir):
    # The summary is never split into multiple messages (design revision).
    long_summary = "中" * 4000
    bundle = ReportBundle("R", long_summary, None, None)
    client = _MockClient()
    out = send_report(bundle, client, {"id": "x"}, WeChatConfig(default_mode="summary"))
    assert out.status == "success" and out.summary_ok
    assert len(client.sent) == 1  # exactly one message
    sent_text = client.sent[0][1]
    assert len(sent_text) <= 1800
    assert sent_text.endswith("…")


def test_uncertain_send_is_not_retried(wechat_dir):
    class UncertainClient:
        calls = 0

        def send_text(self, recipient, text):
            self.calls += 1
            raise UncertainSendError("connection lost after request was submitted")

        def send_file(self, recipient, path, caption=""):
            raise AssertionError("file send should not be reached")

    client = UncertainClient()
    cfg = WeChatConfig(default_mode="summary", retry=RetryConfig(max_retries=3, interval_seconds=0))
    out = send_report(ReportBundle("R", "SUMMARY", None, None), client, {"id": "x"}, cfg)
    assert out.status == "uncertain"
    assert client.calls == 1
    assert out.uncertain_parts == ["summary"]


def test_automated_success_is_not_resent(wechat_dir):
    client = _MockClient()
    bundle = ReportBundle("R", "SUMMARY", None, None)
    cfg = WeChatConfig(default_mode="summary", retry=RetryConfig(max_retries=0, interval_seconds=0))
    first = send_report(bundle, client, {"id": "x"}, cfg, source="cron")
    second = send_report(bundle, client, {"id": "x"}, cfg, source="cron")
    assert first.status == "success"
    assert second.status == "success" and second.duplicate_skipped
    assert len(client.sent) == 1


def test_uncertain_automated_send_blocks_resend(wechat_dir):
    class UncertainClient:
        calls = 0

        def send_text(self, recipient, text):
            self.calls += 1
            raise UncertainSendError("timeout with uncertain server acceptance")

        def send_file(self, recipient, path, caption=""):
            raise AssertionError("file send should not be reached")

    bundle = ReportBundle("R", "SUMMARY", None, None)
    cfg = WeChatConfig(default_mode="summary", retry=RetryConfig(max_retries=0, interval_seconds=0))
    first = send_report(bundle, UncertainClient(), {"id": "x"}, cfg, source="cron")
    assert first.status == "uncertain"
    # A resumed automated run must not blindly resend an uncertain summary.
    second = send_report(bundle, _MockClient(), {"id": "x"}, cfg, source="cron")
    assert second.status == "uncertain"


def test_uncertain_automated_send_blocks_next_run(wechat_dir):
    class UncertainClient:
        calls = 0

        def send_text(self, recipient, text):
            self.calls += 1
            raise UncertainSendError("timeout with uncertain server acceptance")

        def send_file(self, recipient, path, caption=""):
            raise AssertionError("file send should not be reached")

    bundle = ReportBundle("R", "SUMMARY", None, None)
    cfg = WeChatConfig(default_mode="summary", retry=RetryConfig(max_retries=0, interval_seconds=0))
    first_client = UncertainClient()
    first = send_report(bundle, first_client, {"id": "x"}, cfg, source="scheduler")
    retry_client = _MockClient()
    second = send_report(bundle, retry_client, {"id": "x"}, cfg, source="scheduler")
    assert first.status == "uncertain"
    assert second.status == "uncertain"
    assert second.errors and "automatic resend was blocked" in second.errors[0]
    assert first_client.calls == 1
    assert retry_client.sent == []


def test_manual_send_can_deliberately_resend_same_report(wechat_dir):
    client = _MockClient()
    bundle = ReportBundle("R", "SUMMARY", None, None)
    cfg = WeChatConfig(default_mode="summary", retry=RetryConfig(max_retries=0, interval_seconds=0))
    first = send_report(bundle, client, {"id": "x"}, cfg, source="manual")
    second = send_report(bundle, client, {"id": "x"}, cfg, source="manual")
    assert first.status == second.status == "success"
    assert len(client.sent) == 2


def test_retry_recovers(wechat_dir):
    client = _MockClient(text_fail_times=2)
    bundle = ReportBundle("R", "SUMMARY", None, None)
    cfg = WeChatConfig(default_mode="summary", retry=RetryConfig(max_retries=3, interval_seconds=0))
    out = send_report(bundle, client, {"id": "x"}, cfg)
    assert out.status == "success"
    assert out.attempts == 3  # 2 failures + 1 success


def test_retry_exhausted_fails(wechat_dir):
    client = _MockClient(text_fail_times=99)
    bundle = ReportBundle("R", "SUMMARY", None, None)
    cfg = WeChatConfig(default_mode="summary", retry=RetryConfig(max_retries=2, interval_seconds=0))
    out = send_report(bundle, client, {"id": "x"}, cfg)
    assert out.status == "failed"
    assert out.attempts == 3  # initial + 2 retries


def test_permanent_error_not_retried(wechat_dir):
    class PermClient:
        def __init__(self):
            self.calls = 0

        def send_text(self, r, t):
            self.calls += 1
            raise PermanentSendError("bad input")

    client = PermClient()
    bundle = ReportBundle("R", "SUMMARY", None, None)
    cfg = WeChatConfig(default_mode="summary", retry=RetryConfig(max_retries=3, interval_seconds=0))
    out = send_report(bundle, client, {"id": "x"}, cfg)
    assert out.status == "failed"
    assert client.calls == 1  # no retries


# --------------------- splitting (TASK-148) --------------------- #

def test_split_preserves_unicode():
    text = "中" * 5000
    chunks = split_text(text, 2000)
    assert "".join(chunks) == text
    assert all(len(c) <= 2000 for c in chunks)


def test_split_empty():
    assert split_text("") == []
    assert split_text("   ") == []


def test_split_short_single():
    assert split_text("hello", 100) == ["hello"]


def test_number_chunks_order():
    out = number_chunks(["a", "b", "c"], title="R")
    assert out[0].startswith("R （1/3）")
    assert out[2].startswith("（3/3）")


def test_number_chunks_single_unchanged():
    assert number_chunks(["only"], title="R") == ["only"]
