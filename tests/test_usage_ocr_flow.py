"""Thống kê đi trọn một lượt qua `WS /v2/session` và `/v2/turn`: mở lượt, ảnh bị từ chối, phát xong, chụp lại."""

import json
import time

import cv2
import numpy as np
import pytest
from fastapi.testclient import TestClient

from app import app
from models import clef
from pipeline.usage_tasks import tracker
from services import usage
from tests.test_v2_api import _adpcm_chunks, _turn, world  # noqa: F401 (fixture)
from tests.test_v2_session import _hello, _recv_until_end

client = TestClient(app)


@pytest.fixture
def events(monkeypatch):
    out: list[dict] = []
    real_emit = usage.emit

    def capture(etype, **fields):
        out.append({"type": etype, **{k: v for k, v in fields.items() if v is not None}})
        return real_emit(etype, **fields)

    monkeypatch.setattr(usage, "emit", capture)
    return out


def _of(events, etype):
    return [e for e in events if e["type"] == etype]


def _jpeg(dark: bool) -> bytes:
    rng = np.random.default_rng(1)
    img = np.zeros((480, 640, 3), np.uint8) if dark else rng.integers(40, 220, (480, 640, 3), dtype=np.uint8)
    ok, buf = cv2.imencode(".jpg", img)
    assert ok
    return buf.tobytes()


def _space(world):  # noqa: F811
    rec, labels, _ = world
    rec.text = "MÔ TẢ XUNG QUANH"
    labels["mô tả xung quanh"] = clef.ClefResult("space", 0.99)


def _turn_ws(ws, turn, image: bytes, played=True):
    ws.send_text(json.dumps({"t": "turn_start", "turn": turn, "t_press_ms": 1000}))
    ws.send_bytes(b"\x01" + image)
    for chunk in _adpcm_chunks():
        ws.send_bytes(b"\x03" + chunk)
    ws.send_text(json.dumps({"t": "turn_end", "turn": turn, "t_release_ms": 2500}))
    _recv_until_end(ws, turn)
    if played:
        ws.send_text(json.dumps({"t": "played", "turn": turn, "first_byte_ms": 3000, "first_audio_ms": 3040}))


def _sync(ws):
    ws.send_text('{"t":"ping"}')
    assert json.loads(ws.receive_text()) == {"t": "pong"}


def test_rejected_then_success_is_one_task_with_two_attempts(world, events):  # noqa: F811
    _space(world)
    with client.websocket_connect("/v2/session") as ws:
        _hello(ws)
        ws.receive_text()  # hello_ok
        _turn_ws(ws, "t1", _jpeg(dark=True))
        _turn_ws(ws, "t2", _jpeg(dark=False))
        _sync(ws)

    started = _of(events, "attempt_started")
    assert [e["feature"] for e in started] == ["space", "space"]
    assert started[0]["task_id"] == started[1]["task_id"]
    assert started[0]["firmware_version"] == "t"  # `hello.fw` của kính
    signals = _of(events, "attempt_signal")
    assert [(s["signal"], s.get("reason_code")) for s in signals] == [("image_rejected", "dark")]
    finished = _of(events, "attempt_finished")
    assert [(f["status"], f.get("playback")) for f in finished] == [
        ("rejected_image", "confirmed"), ("success", "confirmed")]
    (task,) = _of(events, "task_finished")
    assert (task["status"], task["attempts"]) == ("success", 2)
    assert len(_of(events, "attempt_marks")) == 2  # tiếng đầu của mỗi lượt


def test_no_played_marks_unconfirmed(world, events):  # noqa: F811
    _space(world)
    with client.websocket_connect("/v2/session") as ws:
        _hello(ws)
        ws.receive_text()
        _turn_ws(ws, "t1", _jpeg(dark=False), played=False)
        _sync(ws)
    assert _of(events, "attempt_finished") == []
    tracker.expire(time.monotonic() + 3600)
    (fin,) = _of(events, "attempt_finished")
    assert (fin["status"], fin["playback"]) == ("unconfirmed", "unconfirmed")


def test_duplicate_played_ignored(world, events):  # noqa: F811
    _space(world)
    with client.websocket_connect("/v2/session") as ws:
        _hello(ws)
        ws.receive_text()
        _turn_ws(ws, "t1", _jpeg(dark=False))
        ws.send_text(json.dumps({"t": "played", "turn": "t1", "first_byte_ms": 1, "first_audio_ms": 2}))
        _sync(ws)
    assert len(_of(events, "attempt_finished")) == 1
    assert len(_of(events, "attempt_marks")) == 1


def test_press_cut_after_audio_is_completed_early(world, events):  # noqa: F811
    _space(world)
    with client.websocket_connect("/v2/session") as ws:
        _hello(ws)
        ws.receive_text()
        _turn_ws(ws, "t1", _jpeg(dark=False), played=False)  # đang đọc thì bấm tiếp
        _turn_ws(ws, "t2", _jpeg(dark=False))
        _sync(ws)
    finished = _of(events, "attempt_finished")
    assert [f["status"] for f in finished] == ["completed_early", "success"]


def test_http_turn_confirms_on_stream_end(world, events):  # noqa: F811
    rec, labels, _ = world
    rec.text = "KỂ CHUYỆN CƯỜI ĐI"
    labels["kể chuyện cười đi"] = clef.ClefResult("chat", 0.9)
    response = _turn("h1")
    assert response.status_code == 200 and response.content
    (started,) = _of(events, "attempt_started")
    assert (started["feature"], started["entry"]) == ("chat", "v2_turn")
    (fin,) = _of(events, "attempt_finished")
    assert (fin["status"], fin["playback"]) == ("success", "confirmed")


def test_events_contain_no_spoken_text(world, events):  # noqa: F811
    _space(world)
    with client.websocket_connect("/v2/session") as ws:
        _hello(ws)
        ws.receive_text()
        _turn_ws(ws, "t1", _jpeg(dark=False))
        _sync(ws)
    dump = json.dumps(events, ensure_ascii=False).lower()
    for private in ("mô tả xung quanh", "cái bàn", "cửa ra vào"):
        assert private not in dump
