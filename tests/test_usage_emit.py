"""services/usage.py: lọc trường, không chặn lượt, spool bền qua khởi động lại, gửi lô/lùi khi lỗi."""

import json
import os
import time
import uuid

import pytest

import config
from pipeline import device_ctx
from services import usage


@pytest.fixture
def sent(monkeypatch):
    """Thay `_post` bằng bản ghi lại lô; `codes` là mã trả lần lượt (hết thì 200)."""
    batches: list[list[dict]] = []
    codes: list[int] = []

    def fake_post(batch):
        batches.append([dict(e) for e in batch])
        return codes.pop(0) if codes else 200

    monkeypatch.setattr(usage, "_post", fake_post)
    monkeypatch.setattr(config, "USAGE_API_TOKEN", "test-only-token")
    return batches, codes


def _ids(batches):
    return [e["event_id"] for batch in batches for e in batch]


def test_emit_drops_unknown_fields(sent):
    batches, _ = sent
    with device_ctx.use("glasses-x"):
        usage.emit("attempt_started", attempt_id="a1", task_id="t1", feature="ocr", text="câu nói riêng tư")
    usage.flush_now()

    (event,) = batches[0]
    assert "text" not in event
    assert event["feature"] == "ocr"
    assert event["device_id"] == "glasses-x"
    assert event["occurred_at"].endswith("+00:00")
    assert "riêng tư" not in json.dumps(event, ensure_ascii=False)


def test_unknown_type_is_not_queued(sent):
    batches, _ = sent
    assert usage.emit("selfie_taken", attempt_id="a1") is None
    usage.flush_now()
    assert batches == []


def test_emit_never_blocks(monkeypatch):
    """1000 lần emit không đụng mạng/đĩa: tổng < 50 ms kể cả khi gửi treo."""
    monkeypatch.setattr(usage, "_post", lambda batch: time.sleep(2) or 200)
    started = time.perf_counter()
    for i in range(1000):
        usage.emit("session_heartbeat", session_id=f"s{i}", kind="connection")
    assert time.perf_counter() - started < 0.05


def test_spool_survives_restart_without_duplicates(sent, monkeypatch):
    batches, codes = sent
    monkeypatch.setattr(config, "USAGE_BATCH", 2)
    ids = [usage.emit("session_heartbeat", session_id=f"s{i}", kind="connection") for i in range(3)]
    codes.extend([200, 503])  # lô 1 vào, lô 2 lỗi -> giữ con trỏ sau lô 1
    assert usage.flush_now() == 2

    usage.reset_for_tests()  # "chết" và chạy lại: mất bộ nhớ, còn spool + con trỏ trên đĩa
    batches.clear()
    assert usage.flush_now() == 1
    assert _ids(batches) == [ids[2]]


def test_truncated_last_line_skipped(sent):
    batches, _ = sent
    usage.emit("session_heartbeat", session_id="s0", kind="connection")
    usage._drain_to_disk()
    (name,) = usage._spool_files()
    with open(os.path.join(config.USAGE_SPOOL_DIR, name), "ab") as fh:
        fh.write(b'{"event_id": "cut-in-the-mid')  # chết giữa lúc ghi
    later = usage.emit("session_heartbeat", session_id="s1", kind="connection")

    usage.flush_now()

    assert len(_ids(batches)) == 2
    assert _ids(batches)[-1] == later


def test_5xx_backoff_keeps_cursor(sent):
    batches, codes = sent
    first = usage.emit("session_heartbeat", session_id="s0", kind="connection")
    codes.append(503)
    assert usage.flush_now() == 0
    assert usage._read_cursor() == ("", 0)
    assert usage.flush_now() == 0  # đang lùi: không gửi lại ngay
    usage._backoff_until = 0.0
    assert usage.flush_now() == 1
    assert _ids(batches) == [first, first]  # gửi lại đúng event_id cũ


def test_no_token_spools_but_does_not_send(monkeypatch):
    calls = []
    monkeypatch.setattr(usage, "_post", lambda batch: calls.append(batch) or 200)
    monkeypatch.setattr(config, "USAGE_API_TOKEN", "")
    usage.emit("session_heartbeat", session_id="s0", kind="connection")
    assert usage.flush_now() == 0
    assert calls == []
    assert usage._spool_files()


def test_disabled_writes_nothing(monkeypatch):
    monkeypatch.setattr(config, "USAGE_ENABLED", False)
    assert usage.emit("session_heartbeat", session_id="s0", kind="connection") is None
    usage._drain_to_disk()
    assert usage._spool_files() == []


def test_signal_uses_current_attempt(sent):
    batches, _ = sent
    usage.signal("needs_web")  # không có lượt: bỏ qua
    token = usage.current_attempt.set("att-1")
    try:
        usage.signal("needs_web", "news")
    finally:
        usage.current_attempt.reset(token)
    usage.flush_now()
    (event,) = batches[0]
    assert (event["attempt_id"], event["signal"], event["reason_code"]) == ("att-1", "needs_web", "news")


def test_uuid7_is_version_7_and_time_ordered():
    a = usage.uuid7()
    time.sleep(0.002)
    b = usage.uuid7()
    assert uuid.UUID(a).version == 7
    assert a < b
