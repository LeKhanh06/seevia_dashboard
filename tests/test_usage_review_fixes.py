"""Sửa sau review cuối (2026-11-19): giá trị sự kiện phải nằm trong hợp đồng AB."""

import json
import os
import time

import pytest

import config
from pipeline import usage_turn
from pipeline.reply import Reply
from pipeline.usage_tasks import tracker
from services import usage

AB_PLAYBACK = {"confirmed", "unconfirmed"}  # ab-thongke schemas/usage.py AttemptFinished.playback


@pytest.fixture
def events(monkeypatch):
    out: list[dict] = []
    real_emit = usage.emit

    def capture(etype, **fields):
        out.append({"type": etype, **{k: v for k, v in fields.items() if v is not None}})
        return real_emit(etype, **fields)

    monkeypatch.setattr(usage, "emit", capture)
    tracker.reset()
    usage_turn.reset_for_tests()
    yield out
    tracker.reset()


@pytest.mark.parametrize("cut", ["button", "played_aborted"])
def test_cut_readback_while_phone_runs_keeps_playback_in_contract(events, cut):
    attempt = usage_turn.begin("dev-cut", time.monotonic(), transport="ws")
    usage_turn.routed(attempt, Reply(intent="contact_call", lane="D", awaits_action=True))
    usage_turn.audio_sent(attempt, time.monotonic(), 1.0)
    if cut == "button":
        usage_turn.interrupted(attempt)
    else:
        usage_turn.played(attempt, time.monotonic(), {"aborted": True})
    usage_turn.phone_result(attempt, {"request_state": "succeeded"})
    (fin,) = [e for e in events if e["type"] == "attempt_finished"]
    assert fin["status"] == "success"
    assert fin.get("playback", "confirmed") in AB_PLAYBACK


def test_turn_firmware_is_validated_like_hello(events):
    attempt = usage_turn.begin("dev-fw", time.monotonic(), transport="ws", glasses={"fw": "bad fw!"})
    usage_turn.routed(attempt, Reply(intent="chat", lane="A"))
    (start,) = [e for e in events if e["type"] == "attempt_started"]
    assert "firmware_version" not in start
    attempt = usage_turn.begin("dev-fw", time.monotonic(), transport="ws", glasses={"fw": "1.4.2"})
    usage_turn.routed(attempt, Reply(intent="chat", lane="A"))
    assert [e for e in events if e["type"] == "attempt_started"][-1]["firmware_version"] == "1.4.2"



# ── minor để sau của review cuối (2026-11-21) ────────────────────────────────────────────────


def test_server_shutdown_closes_open_sessions(monkeypatch):
    """Handler tắt server đóng mọi phiên thống kê còn mở với lý do `shutdown`, rồi mới gửi nốt."""
    import app as app_module
    from pipeline import usage_sessions
    from services import usage

    order: list[str] = []
    monkeypatch.setattr(usage, "emit", lambda etype, **f: order.append(f"{etype}:{f.get('end_reason')}") or "e")
    monkeypatch.setattr(usage, "stop_sender", lambda *a, **k: order.append("stop"))
    usage_sessions.reset_for_tests()
    usage_sessions.open_session("connection", "dev-shutdown")
    order.clear()
    app_module.stop_usage_sender()
    assert order == ["session_ended:shutdown", "stop"]
    assert usage_sessions.current("dev-shutdown", "connection") is None


def test_disabled_keeps_nothing_in_memory(monkeypatch):
    """USAGE_ENABLED=0: bộ ghép và phiên không giữ lượt/phiên nào (trước: đầy RAM mà không bao giờ hết hạn)."""
    from pipeline import usage_sessions
    from pipeline.usage_tasks import Tracker

    monkeypatch.setattr(config, "USAGE_ENABLED", False)
    t = Tracker()
    attempt_id, _ = t.start("g", "ocr", now=0.0)
    assert not t.active(attempt_id)
    assert t._attempts == {} and t._tasks == {}  # noqa: SLF001
    assert usage_sessions.open_session("connection", "dev-off") is None


def test_old_spool_pruned_even_unsent(monkeypatch):
    """Token trống / AB tắt lâu: file spool quá `USAGE_SPOOL_MAX_DAYS` bị xoá, file gần đây giữ."""
    from datetime import UTC, datetime, timedelta

    from services import usage

    monkeypatch.setattr(config, "USAGE_SPOOL_MAX_DAYS", 14)
    now = datetime(2026, 11, 21, tzinfo=UTC)
    spool = usage._spool_dir()  # noqa: SLF001
    for days in (30, 15, 13, 0):
        name = f"spool-{now - timedelta(days=days):%Y%m%d}.jsonl"
        with open(os.path.join(spool, name), "w", encoding="utf-8") as fh:
            fh.write("{}\n")
    assert usage._prune_old(now) == 2  # noqa: SLF001
    assert usage._spool_files() == ["spool-20261108.jsonl", "spool-20261121.jsonl"]  # noqa: SLF001


def test_batch_capped_by_bytes(monkeypatch):
    """Lô dừng trước khi vượt `USAGE_BATCH_BYTES` (AB trả 413 cho thân > 2 MiB); phần còn lại sang lô sau."""
    from services import usage

    monkeypatch.setattr(config, "USAGE_BATCH_BYTES", 1000)
    monkeypatch.setattr(config, "USAGE_API_TOKEN", "test-only-token")
    batches: list[list[dict]] = []
    monkeypatch.setattr(usage, "_post", lambda b: batches.append(list(b)) or 200)
    for i in range(20):
        usage.emit("session_heartbeat", session_id=f"s{i:03d}", kind="connection", device_id="dev-bytes")
    assert usage.flush_now() == 20
    assert len(batches) > 1
    for batch in batches:
        assert sum(len(json.dumps(e, ensure_ascii=False).encode()) + 1 for e in batch) <= 1000
    assert [e["session_id"] for b in batches for e in b] == [f"s{i:03d}" for i in range(20)]


def test_component_version_computed_at_sender_start(monkeypatch):
    """`git rev-parse` chạy lúc bật thread nền (khởi động), không ở lượt đầu trên event loop."""
    from services import usage

    monkeypatch.setattr(config, "USAGE_ENABLED", True)
    monkeypatch.setattr(usage, "_version", None)
    monkeypatch.setattr(usage, "_loop", lambda: None)
    usage.start_sender()
    try:
        assert usage._version is not None  # noqa: SLF001
    finally:
        usage.stop_sender(timeout=1.0)
