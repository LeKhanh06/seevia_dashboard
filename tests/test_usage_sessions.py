"""Thống kê phiên (Task 12): kết nối kính (`fw` + chẩn đoán từ `hello`, bị thay mã 4000), chế độ cảnh báo (chỉ đếm cảnh
báo thật sự phát), OCR live (`align_ms`), nhạc / dẫn đường theo trạng thái điện thoại, heartbeat 60 s."""

import asyncio
import json
import time

import pytest
from fastapi.testclient import TestClient

from app import app
from handlers import action_flow
from lanes import nav_live
from live import hazard
from live.session import LiveSession
from pipeline import device_ctx, usage_sessions
from pipeline.usage_tasks import tracker
from services import usage
from tests.test_usage_ocr_flow import _jpeg as _scene_jpeg
from tests.test_usage_ocr_flow import _space, _turn_ws
from tests.test_v2_api import world  # noqa: F401 (fixture)
from tests.test_v2_session import _hello
from tests.test_v2_units import _FakeNavWS, _fake_speech
from tests.test_v2_units import _jpeg as _frame_jpeg

client = TestClient(app)


@pytest.fixture
def events(monkeypatch):
    out: list[dict] = []
    real_emit = usage.emit

    def capture(etype, **fields):
        out.append({"type": etype, **{k: v for k, v in fields.items() if v is not None}})
        return real_emit(etype, **fields)

    monkeypatch.setattr(usage, "emit", capture)
    usage_sessions.reset_for_tests()
    action_flow.reset_state()
    yield out
    usage_sessions.reset_for_tests()
    action_flow.reset_state()
    tracker.reset()


def _of(events, etype, **match):
    return [e for e in events if e["type"] == etype and all(e.get(k) == v for k, v in match.items())]


def _wait(cond, timeout=3.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(0.02)
    return cond()


# ── kết nối ──────────────────────────────────────────────────────────────────────────────────

def test_connection_session_open_close(events):
    with client.websocket_connect("/v2/session") as ws:
        _hello(ws, device="dev-conn", fw="1.4.2", uptime_ms=123456, reset_reason="POWERON", wifi_drops=2,
               last_wifi_reason=8, rssi=-61)
        assert json.loads(ws.receive_text())["t"] == "hello_ok"
        (started,) = _of(events, "session_started", kind="connection")
        assert started["device_id"] == "dev-conn" and started["firmware_version"] == "1.4.2"
        assert started["device_info"] == {"uptime_ms": 123456, "reset_reason": "POWERON", "wifi_drops": 2,
                                          "last_wifi_reason": 8, "rssi": -61}
    assert _wait(lambda: _of(events, "session_ended", kind="connection"))
    (ended,) = _of(events, "session_ended", kind="connection")
    assert (ended["session_id"], ended["end_reason"]) == (started["session_id"], "closed")


def test_device_info_drops_bad_values(events):
    with client.websocket_connect("/v2/session") as ws:
        _hello(ws, device="dev-bad", fw="x y", uptime_ms=-1, reset_reason="bad reason!", wifi_drops="3",
               last_wifi_reason=99999, rssi=10)
        ws.receive_text()
        (started,) = _of(events, "session_started", kind="connection")
        assert "device_info" not in started and "firmware_version" not in started


def test_replaced_session_ends_old_with_reason(world, events):  # noqa: F811
    _space(world)
    with client.websocket_connect("/v2/session") as old:
        _hello(old, device="dev-repl")
        old.receive_text()
        _turn_ws(old, "A", _scene_jpeg(dark=False), played=False)  # kính chưa kịp báo played
        (attempt,) = _of(events, "attempt_started")
        with client.websocket_connect("/v2/session") as new:
            _hello(new, device="dev-repl")
            assert json.loads(new.receive_text())["t"] == "hello_ok"
            assert _wait(lambda: _of(events, "session_ended", kind="connection"))
            first, second = _of(events, "session_started", kind="connection")
            (ended,) = _of(events, "session_ended", kind="connection")
            assert (ended["session_id"], ended["end_reason"]) == (first["session_id"], "replaced")
            assert second["session_id"] != first["session_id"]
    # Lượt của phiên cũ còn chờ `played`: chốt `unconfirmed` theo hạn, không bị coi là bị cắt.
    assert _of(events, "attempt_finished", attempt_id=attempt["attempt_id"]) == []
    tracker.expire(time.monotonic() + 3600)
    (fin,) = _of(events, "attempt_finished", attempt_id=attempt["attempt_id"])
    assert (fin["status"], fin["playback"]) == ("unconfirmed", "unconfirmed")


def test_shutdown_closes_open_sessions(events):
    sid = usage_sessions.open_session("connection", "dev-shut")
    usage_sessions.open_session("alert_mode", "dev-shut")
    usage_sessions.close_all("shutdown")
    ended = _of(events, "session_ended")
    assert {e["kind"] for e in ended} == {"connection", "alert_mode"}
    assert all(e["end_reason"] == "shutdown" for e in ended)
    usage_sessions.close_session(sid, "closed")  # gọi lại: bỏ qua
    assert len(_of(events, "session_ended")) == 2


# ── heartbeat ────────────────────────────────────────────────────────────────────────────────

def test_heartbeat_emitted(events):
    t0 = time.monotonic()
    sid = usage_sessions.open_session("alert_mode", "dev-hb", now=t0)
    usage_sessions.heartbeat_all(t0 + 59)
    assert _of(events, "session_heartbeat") == []
    usage_sessions.heartbeat_all(t0 + 60)
    usage_sessions.heartbeat_all(t0 + 61)
    usage_sessions.heartbeat_all(t0 + 120)
    beats = _of(events, "session_heartbeat", session_id=sid)
    assert len(beats) == 2 and beats[0]["kind"] == "alert_mode" and beats[0]["device_id"] == "dev-hb"
    usage_sessions.close_session(sid, "user_off")
    usage_sessions.heartbeat_all(t0 + 500)
    assert len(_of(events, "session_heartbeat")) == 2


# ── chế độ cảnh báo ──────────────────────────────────────────────────────────────────────────

def test_alert_only_when_spoken(monkeypatch, events):
    class FakeYolo:
        def detect(self, bgr):
            return [hazard.Detection("motorbike", "vehicle", 0.9, (0.35, 0.2, 0.75, 0.95))]

    monkeypatch.setattr(hazard, "detector", lambda: FakeYolo())
    _fake_speech(monkeypatch)
    sid = usage_sessions.open_session("alert_mode", "dev-nav")

    async def go():
        ws, session = _FakeNavWS(), LiveSession("tok", "dev-nav", "nav")
        task = asyncio.create_task(nav_live.run(ws, session))
        for _ in range(4):  # cùng một xe máy mỗi khung: chống làm phiền chỉ cho phát lần đầu
            await ws.inbox.put({"type": "websocket.receive", "bytes": bytes([1]) + _frame_jpeg(320, 240)})
            await asyncio.sleep(0.15)
        session.closed.set()
        await ws.inbox.put({"type": "websocket.disconnect", "code": 1000})
        await asyncio.gather(task, return_exceptions=True)
        return ws.sent

    sent = asyncio.run(go())
    spoken = [json.loads(v) for k, v in sent if k == "t" and '"hazard"' in v]
    alerts = _of(events, "alert_raised")
    assert len(spoken) >= 1 and len(alerts) == len(spoken)
    assert all(a["session_id"] == sid and a["level"] == s["level"] for a, s in zip(alerts, spoken))


def test_alert_without_open_mode_is_not_counted(monkeypatch, events):
    usage_sessions.alert_for("dev-none", 3)
    assert _of(events, "alert_raised") == []


def test_alert_mode_opens_once_and_user_off_closes(events):
    a = usage_sessions.open_session("alert_mode", "dev-a")
    b = usage_sessions.open_session("alert_mode", "dev-a")  # nav dựng lại sau OCR live: vẫn cùng phiên
    assert a == b and len(_of(events, "session_started", kind="alert_mode")) == 1
    usage_sessions.close_kind("dev-a", "alert_mode", "user_off")
    (ended,) = _of(events, "session_ended", kind="alert_mode")
    assert ended["end_reason"] == "user_off"
    assert usage_sessions.open_session("alert_mode", "dev-a") != a


# ── OCR live ─────────────────────────────────────────────────────────────────────────────────

def test_ocr_live_align_ms(events):
    sid = usage_sessions.open_session("ocr_live", "dev-ocr")
    usage_sessions.close_session(sid, "done", align_ms=1834)
    (ended,) = _of(events, "session_ended", kind="ocr_live")
    assert (ended["end_reason"], ended["align_ms"]) == ("done", 1834)


def test_ocr_live_run_opens_and_closes_session(monkeypatch, events):
    from tests.test_money_retry import _Box

    box = _Box(monkeypatch)
    box.session.params = {"mode": "read"}
    box.session.closed.set()  # đóng ngay: phiên OCR live bị huỷ trước khi đọc

    from lanes import ocr_live

    asyncio.run(ocr_live.run(box.ws, box.session))
    (started,) = _of(events, "session_started", kind="ocr_live")
    (ended,) = _of(events, "session_ended", kind="ocr_live")
    assert ended["session_id"] == started["session_id"] and ended["end_reason"] == "cancelled"


def test_money_retry_is_not_an_ocr_live_session(monkeypatch, events):
    from lanes import ocr_live
    from tests.test_money_retry import _Box

    box = _Box(monkeypatch)
    box.session.closed.set()
    asyncio.run(ocr_live.run(box.ws, box.session))
    assert _of(events, "session_started") == []


# ── nhạc / dẫn đường ─────────────────────────────────────────────────────────────────────────

def test_music_and_navigation_sessions_follow_phone_state(events):
    token = device_ctx.current.set("dev-media")
    try:
        ok = {"request_state": "succeeded", "result": {}}
        action_flow.note_result("music_play", {"request_state": "failed"})  # máy báo hỏng: nhạc không hề chạy
        assert _of(events, "session_started") == []
        action_flow.note_result("music_play", ok)
        action_flow.note_result("music_play", ok)  # đổi bài: vẫn một phiên nghe nhạc
        action_flow.note_result("navigation_start", {"request_state": "succeeded", "result": {"navigation_id": "n1"}})
        action_flow.note_result("navigation_stop", ok)
    finally:
        device_ctx.current.reset(token)
    (music,) = _of(events, "session_started", kind="music")
    (nav,) = _of(events, "session_started", kind="navigation")
    assert music["device_id"] == nav["device_id"] == "dev-media"
    ended = {e["kind"]: e["end_reason"] for e in _of(events, "session_ended")}
    assert ended == {"music": "replaced", "navigation": "user_off"}
    assert "n1" not in json.dumps(events)


def test_connection_close_ends_media_sessions_but_replace_keeps_them(events):
    usage_sessions.open_session("connection", "dev-m2")
    usage_sessions.open_session("music", "dev-m2")
    usage_sessions.connection_ended("dev-m2", "replaced")
    assert _of(events, "session_ended", kind="music") == []  # kính nối lại ngay: nhạc trên điện thoại vẫn chạy
    usage_sessions.connection_ended("dev-m2", "closed")
    (ended,) = _of(events, "session_ended", kind="music")
    assert ended["end_reason"] == "closed"
