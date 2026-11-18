"""Thống kê làn D (Task 10): lượt lệnh điện thoại chỉ chốt khi đã đọc lại xong VÀ điện thoại báo kết quả thật;
mã lỗi AB; callback lặp; hỏi lại + huỷ trong cửa sổ; bị lệnh mới thay / bị ngắt; hết giờ; không lộ tên người thân.
"""

import json
import threading
import time

import pytest

import config
from handlers import action_flow
from lanes import phone
from models import clef
from pipeline import pending_action, usage_turn
from pipeline.reply import Reply
from pipeline.usage_tasks import tracker
from services import usage
from services.action_callbacks import action_callbacks
from tests.test_v2_api import world  # noqa: F401 (fixture)
from tests.test_v2_calls_routing import _err, _hello, _ok, _say, _starts, _wait, rig  # noqa: F401 (fixture)
from tests.test_v2_session import _hello as _hello_default, _recv_result_reply, _recv_until_end, _send_turn

DEV = "dev-usage-d"


@pytest.fixture
def events(monkeypatch):
    out: list[dict] = []
    lock = threading.Lock()
    real_emit = usage.emit

    def capture(etype, **fields):
        with lock:
            out.append({"type": etype, **{k: v for k, v in fields.items() if v is not None}})
        return real_emit(etype, **fields)

    monkeypatch.setattr(usage, "emit", capture)
    action_callbacks.reset()
    yield out
    action_callbacks.reset()
    pending_action.cancel(DEV, "test")


def _of(events, etype, **match):
    return [e for e in events if e["type"] == etype and all(e.get(k) == v for k, v in match.items())]


def _signals(events, attempt_id):
    return [(s["signal"], s.get("reason_code")) for s in _of(events, "attempt_signal", attempt_id=attempt_id)]


def _open(intent: str, **reply) -> str:
    """Một lượt đã route xong (giữ chỗ lúc nhả nút -> mở theo `Reply.intent`)."""
    attempt = usage_turn.begin(DEV, time.monotonic(), transport="ws")
    usage_turn.routed(attempt, Reply(intent=intent, lane="D", **reply))
    return attempt


def _played(ws, turn):
    ws.send_text(json.dumps({"t": "played", "turn": turn}))


def _sync(ws):
    ws.send_text('{"t":"ping"}')
    for _ in range(200):
        msg = ws.receive()
        if msg.get("text") and json.loads(msg["text"]).get("t") == "pong":
            return
    raise AssertionError("không thấy pong")


# ── 1. thành công: cần cả đọc lại (played) lẫn kết quả từ AB ────────────────────────────────

def test_call_success_needs_action_and_playback(rig, events):  # noqa: F811
    rig.replies["/call/start"] = _ok({"call_id": "c-1", "relative": {"display_name": "Mẹ"}})
    _say(rig, "gọi cho mẹ", "contact_call")
    with rig.client.websocket_connect("/v2/session") as ws:
        _hello(ws)
        _send_turn(ws, "G1")
        _recv_until_end(ws, "G1")
        assert _wait(lambda: _starts(rig, "/call/start"))  # cửa sổ huỷ đã qua, AB nhận lệnh
        assert _wait(lambda: _signals(events, _of(events, "attempt_started")[0]["attempt_id"]))
        assert _of(events, "attempt_finished") == []  # chưa có `played` của câu đọc lại
        _played(ws, "G1")
        _sync(ws)
        assert _wait(lambda: _of(events, "attempt_finished"))
    (started,) = _of(events, "attempt_started")
    assert started["feature"] == "call"
    (fin,) = _of(events, "attempt_finished")
    assert (fin["status"], fin.get("error_code"), fin["playback"]) == ("success", None, "confirmed")
    assert _signals(events, started["attempt_id"]) == [("cancel_window_open", None)]
    (task,) = _of(events, "task_finished")
    assert (task["feature"], task["status"], task["attempts"]) == ("call", "success", 1)


# ── 2. mã lỗi AB + chọn người (hỏi lại) ghép một tác vụ; không lộ tên người thân ─────────────

def test_ab_error_code_recorded_and_choice_merges_without_names(rig, events):  # noqa: F811
    rig.replies["/call/start"] = _err(409, "MULTIPLE_CONTACTS_FOUND", candidates=["Mẹ Lan", "Mẹ Hoa"])
    _say(rig, "gọi cho mẹ", "contact_call")
    with rig.client.websocket_connect("/v2/session") as ws:
        _hello(ws)
        _send_turn(ws, "M1")
        _recv_until_end(ws, "M1")
        _played(ws, "M1")
        _recv_result_reply(ws)  # "Bạn muốn gọi ai: ...?" (lượt result-* không tạo lượt mới)
        assert _wait(lambda: _of(events, "attempt_finished"))
        assert _of(events, "task_finished") == []  # tác vụ còn mở chờ người dùng chọn
        rig.replies["/call/start"] = _ok({"call_id": "c-2", "relative": {"display_name": "Mẹ Hoa"}})
        rec, _, _ = rig.world
        rec.text = "ME HOA"
        _send_turn(ws, "M2")
        _recv_until_end(ws, "M2")
        _played(ws, "M2")
        assert _wait(lambda: _of(events, "task_finished"))
    first, second = _of(events, "attempt_started")
    assert first["task_id"] == second["task_id"] and first["feature"] == second["feature"] == "call"
    fin = {f["attempt_id"]: f for f in _of(events, "attempt_finished")}
    assert (fin[first["attempt_id"]]["status"], fin[first["attempt_id"]]["error_code"]) == (
        "failed", "MULTIPLE_CONTACTS_FOUND")
    assert ("reprompt", "choose_contact") in _signals(events, first["attempt_id"])
    assert fin[second["attempt_id"]]["status"] == "success"
    (task,) = _of(events, "task_finished")
    assert (task["status"], task["attempts"]) == ("success", 2)
    dump = json.dumps(events, ensure_ascii=False).lower()
    for private in ("mẹ", "hoa", "lan", "me hoa"):
        assert private not in dump


def test_phone_error_code_from_result_keeps_task_open_for_say_again(monkeypatch, events):
    monkeypatch.setattr(action_flow, "call_service_api", lambda ep, p: {"data": {"request_id": "req-song"}})
    monkeypatch.setattr(action_flow, "get_request_status", lambda rid: {
        "request_state": "failed", "error": {"code": "SONG_NOT_FOUND"}})
    attempt = _open("music_play", awaits_action=True)
    out = "".join(action_flow.run_action("music_play", "api/v1/service/music/play", {}, "ack", "fail"))
    assert out
    usage_turn.played(attempt, None, {})
    (fin,) = _of(events, "attempt_finished")
    assert (fin["status"], fin["error_code"], fin["request_id"]) == ("failed", "SONG_NOT_FOUND", "req-song")
    assert ("reprompt", "missing_slot") in _signals(events, attempt)
    assert _of(events, "task_finished") == []  # người dùng sẽ nói lại tên bài


# ── 3. callback lặp (và poll) chỉ tính một lần ──────────────────────────────────────────────

def test_duplicate_callback_single_result(monkeypatch, events):
    monkeypatch.setattr(action_flow, "call_service_api", lambda ep, p: {"data": {"request_id": "req-dup"}})
    monkeypatch.setattr(action_flow, "get_request_status", lambda rid: {"request_state": "processing"})
    monkeypatch.setattr(config, "VISIONCARE_FAST_ACTION_SECONDS", 5.0)
    attempt = _open("music_stop")
    from pipeline import device_ctx

    worker = device_ctx.run_in_thread(
        lambda: list(action_flow.run_action("music_stop", "api/v1/service/music/stop", {}, "ack", "fail")))
    assert _wait(lambda: tracker.by_request("req-dup") == attempt)
    assert action_callbacks.resolve("req-dup", {"request_state": "succeeded", "result": {}})
    action_callbacks.resolve("req-dup", {"request_state": "failed", "error": {"code": "X"}})  # lặp: bỏ
    worker.join(5)
    usage_turn.played(attempt, None, {})
    (fin,) = _of(events, "attempt_finished")
    assert (fin["status"], fin.get("error_code")) == ("success", None)


# ── 4. hỏi lại -> "có" -> huỷ trong cửa sổ bằng nút bấm; "có/không" lạc đề không là lượt ──────

def test_reprompt_then_cancel_in_window(world, events):  # noqa: F811
    rec, labels, _ = world
    rec.text = "GỌI CHO MẸ"
    labels["gọi cho mẹ"] = clef.ClefResult("contact_call", 0.9)  # Clef: không đủ tin -> hỏi lại
    from fastapi.testclient import TestClient

    from app import app

    with TestClient(app).websocket_connect("/v2/session") as ws:
        _hello_default(ws)
        ws.receive_text()  # hello_ok
        _send_turn(ws, "R1")
        _recv_until_end(ws, "R1")
        _played(ws, "R1")
        rec.text = "CÓ"
        _send_turn(ws, "R2")
        _recv_until_end(ws, "R2")  # "Đang gọi cho mẹ." — cửa sổ huỷ đang mở
        rec.text = ""
        _send_turn(ws, "R3")  # bấm nút, không nói: huỷ lệnh chờ
        _recv_until_end(ws, "R3")
        _sync(ws)
        assert _wait(lambda: _of(events, "task_finished"))
    first, second = _of(events, "attempt_started")  # R3 (nhấn chạm, không lời) không là lượt
    assert first["task_id"] == second["task_id"]
    assert _signals(events, first["attempt_id"]) == [("reprompt", "confirm")]
    assert _signals(events, second["attempt_id"]) == [("cancel_window_open", None), ("cancelled_in_window", None)]
    fin = {f["attempt_id"]: f for f in _of(events, "attempt_finished")}
    assert fin[first["attempt_id"]]["status"] == "success"
    assert (fin[second["attempt_id"]]["status"], fin[second["attempt_id"]].get("error_code")) == ("cancelled", None)
    (task,) = _of(events, "task_finished")
    assert (task["status"], task["attempts"]) == ("cancelled", 2)


def test_answer_no_cancels_task(world, events):  # noqa: F811
    rec, labels, _ = world
    rec.text = "GỌI CHO MẸ"
    labels["gọi cho mẹ"] = clef.ClefResult("contact_call", 0.9)
    from fastapi.testclient import TestClient

    from app import app

    with TestClient(app).websocket_connect("/v2/session") as ws:
        _hello_default(ws)
        ws.receive_text()
        for turn, text in (("N1", "GỌI CHO MẸ"), ("N2", "KHÔNG")):
            rec.text = text
            _send_turn(ws, turn)
            _recv_until_end(ws, turn)
            _played(ws, turn)
        _sync(ws)
    assert len({e["task_id"] for e in _of(events, "attempt_started")}) == 1
    (task,) = _of(events, "task_finished")
    assert (task["feature"], task["status"], task["attempts"]) == ("call", "cancelled", 2)


def test_bare_yes_without_question_is_not_a_use(world, events):  # noqa: F811
    rec, _, _ = world
    phone._CONFIRM.clear()
    rec.text = "CÓ"
    from fastapi.testclient import TestClient

    from app import app

    with TestClient(app).websocket_connect("/v2/session") as ws:
        _hello_default(ws)
        ws.receive_text()
        _send_turn(ws, "Y1")
        _recv_until_end(ws, "Y1")
        _played(ws, "Y1")
        _sync(ws)
    assert _of(events, "attempt_started") == [] and _of(events, "attempt_signal") == []


# ── 5. bị lệnh mới thay / bị ngắt: huỷ nhưng không phải người dùng huỷ ──────────────────────

def test_superseded_not_counted_as_user_cancel(events):
    old = _open("contact_call", awaits_action=True)
    pending_action.schedule(DEV, None, lambda: None, "contact_call")
    new = _open("message_send", awaits_action=True)
    pending_action.schedule(DEV, None, lambda: None, "message_send")  # thay lệnh cũ (không qua lượt mới)
    (fin,) = _of(events, "attempt_finished", attempt_id=old)
    assert (fin["status"], fin["error_code"]) == ("cancelled", "superseded")
    assert _signals(events, old) == []
    pending_action.cancel(DEV, "kính ngắt")  # rớt kết nối trong cửa sổ huỷ
    (fin,) = _of(events, "attempt_finished", attempt_id=new)
    assert (fin["status"], fin["error_code"]) == ("cancelled", "interrupted")
    assert _signals(events, new) == [("interrupted", None)]


def test_user_press_in_window_is_cancelled_in_window(events):
    attempt = _open("nav_start", awaits_action=True)
    action = pending_action.schedule(DEV, None, lambda: None, "nav_start")
    assert action.arm(30.0)
    pending_action.begin_turn(DEV)  # bấm nút / nói lệnh khác
    (fin,) = _of(events, "attempt_finished", attempt_id=attempt)
    assert (fin["status"], fin.get("error_code")) == ("cancelled", None)
    assert _signals(events, attempt) == [("cancel_window_open", None), ("cancelled_in_window", None)]


# ── 6. hết giờ chờ điện thoại -> lỗi kỹ thuật RESULT_TIMEOUT ────────────────────────────────

def test_result_timeout_technical_error(monkeypatch, events):
    monkeypatch.setattr(action_flow, "call_service_api", lambda ep, p: {"data": {"request_id": "req-slow"}})
    monkeypatch.setattr(action_flow, "get_request_status", lambda rid: {"request_state": "processing"})
    monkeypatch.setattr(config, "VISIONCARE_FAST_ACTION_SECONDS", 0.01)
    monkeypatch.setattr(config, "VISIONCARE_RESULT_TIMEOUT_SECONDS", 0.05)
    monkeypatch.setattr(config, "VISIONCARE_RESULT_POLL_SECONDS", 0.01)
    attempt = _open("music_stop")
    list(action_flow.run_action("music_stop", "api/v1/service/music/stop", {}, "ack", "fail"))
    usage_turn.played(attempt, None, {})
    (fin,) = _of(events, "attempt_finished")
    assert (fin["status"], fin["error_code"]) == ("technical_error", "RESULT_TIMEOUT")
    action_callbacks.resolve("req-slow", {"request_state": "succeeded", "result": {}})  # muộn: không đổi
    assert len(_of(events, "attempt_finished")) == 1


def test_action_never_reported_expires_as_timeout(events):
    attempt = _open("ride_quote", awaits_action=True)  # lệnh không bao giờ chạy / không ai báo
    usage_turn.played(attempt, None, {})
    assert _of(events, "attempt_finished") == []
    tracker.expire(time.monotonic() + config.USAGE_ACTION_WAIT_S + 1)
    (fin,) = _of(events, "attempt_finished")
    assert (fin["status"], fin["error_code"]) == ("technical_error", "RESULT_TIMEOUT")


def test_turn_start_time_is_release_not_routing(events):
    released = time.monotonic() - 2.0  # route (STT + Gemma) mất 2 s
    attempt = usage_turn.begin(DEV, released, transport="ws")
    usage.signal("image_rejected", "dark")  # tín hiệu trong lúc route: giữ tới khi mở lượt
    assert _of(events, "attempt_signal") == []
    usage_turn.routed(attempt, Reply(intent="space", lane="A"))
    (started,) = _of(events, "attempt_started")
    lag = time.time() - __import__("datetime").datetime.fromisoformat(started["occurred_at"]).timestamp()
    assert 1.5 < lag < 3.0
    assert _signals(events, attempt) == [("image_rejected", "dark")]
