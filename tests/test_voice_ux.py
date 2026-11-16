"""UX giọng nói (2026-10-22): lệnh dừng bị STT rớt động từ, tiếng xác nhận bật cảnh báo, không báo bừa thành công.

Nguồn: test người dùng giả lập — "Tắt cảnh báo" nghe thành "Cảnh báo" rồi BẬT lại cảnh báo đang bật; "Phát nhạc
bolero" bị đọc lại "Đang mở bùi lê rù." khi điện thoại chưa nhận lệnh; "Vặn nhỏ nhạc lại" khi mất kết nối nói
"Không thể thay đổi âm lượng".
"""
import asyncio
import threading

import pytest

import config
from handlers import action_flow, music, result_speech
from lanes import phone
from pipeline import cues, final_router, pending_action
from pipeline.intent_early import Decision
from services.action_callbacks import action_callbacks
from services.visioncare_client import VisionCareAPIError

DEV = "dev-voice-ux"


@pytest.fixture(autouse=True)
def _clean():
    for table in (phone._CONFIRM, phone._PENDING, phone._CHOICE, phone._OFFER, phone._BODY):
        table.clear()
    pending_action.cancel(DEV, "test")
    yield
    for table in (phone._CONFIRM, phone._PENDING, phone._CHOICE, phone._OFFER, phone._BODY):
        table.clear()


class _Prewarmer:
    class _Session:
        token = "tok"

    def session(self, mode):
        return self._Session()


def _route(text, intent, state=None):
    ctx = final_router.TurnContext(DEV, "t", _Prewarmer(), None)
    ctx.device_state = state or {}
    d = Decision(intent, source="model", backend="gemma", confidence=0.99)
    return asyncio.run(final_router.route(ctx, d, text))


# ── a. "Cảnh báo" trơ khi cảnh báo đang bật ───────────────────────────────────

@pytest.mark.parametrize("text", ["Cảnh báo", "cảnh báo.", "Cảnh báo vật cản", "Cảnh báo bất cản", "Cảnh báo đi ạ"])
def test_bare_alert_word_while_alerting_asks_instead_of_rearming(text):
    reply = _route(text, "alert_on", {"alerting": True})
    assert list(reply.speech) == ["Bạn muốn tắt cảnh báo phải không?"]
    assert reply.live is None and not reply.cues
    assert phone.has_pending(DEV)


def test_bare_alert_word_while_off_turns_it_on_with_a_cue():
    reply = _route("Cảnh báo", "alert_on", {"alerting": False})
    assert reply.live == "nav" and reply.cues == ["nav_alert_on"]


@pytest.mark.parametrize("text", ["Bật cảnh báo vật cản", "Bất cảnh báo vật cản", "Mở cảnh báo"])
def test_explicit_alert_on_is_never_second_guessed(text):
    reply = _route(text, "alert_on", {"alerting": True})
    assert reply.live == "nav" and reply.cues == ["nav_alert_on"]


def test_yes_after_the_question_turns_the_alert_off(monkeypatch):
    closed = []
    monkeypatch.setattr(final_router.sessions, "close_device", lambda dev, mode=None: closed.append((dev, mode)))
    _route("Cảnh báo", "alert_on", {"alerting": True})
    reply = _route("Đúng", "alert_off", {"alerting": True})
    assert reply.cues == ["nav_alert_off"] and reply.lane == "C"
    assert closed == [(DEV, "nav")]


def test_no_after_the_question_keeps_the_alert(monkeypatch):
    closed = []
    monkeypatch.setattr(final_router.sessions, "close_device", lambda dev, mode=None: closed.append(dev))
    _route("Cảnh báo", "alert_on", {"alerting": True})
    reply = _route("Không", "alert_on", {"alerting": True})
    assert list(reply.speech) == [final_router.KEEP_MESSAGE] and not closed


def test_bare_nav_and_music_words_ask_to_stop_when_running():
    nav = _route("Dẫn đường", "nav_start", {"navigating": True})
    assert list(nav.speech) == ["Bạn muốn dừng dẫn đường phải không?"]
    music_q = _route("Nhạc", "music_play", {"music_playing": True})
    assert list(music_q.speech) == ["Bạn muốn tắt nhạc phải không?"]


def test_yes_to_stop_nav_runs_nav_stop(monkeypatch):
    spoken = []
    monkeypatch.setattr(phone, "speech", lambda intent, text, params: iter(spoken.append(intent) or ["ok"]))
    _route("Dẫn đường", "nav_start", {"navigating": True})
    reply = _route("Ừ", "nav_start", {"navigating": True})
    assert reply.intent == "nav_stop" and list(reply.speech) == ["ok"] and spoken == ["nav_stop"]


def test_stop_questions_are_prerendered_phrases():
    for line in phone.STOP_QUESTIONS.values():
        assert line in final_router.STATIC_SPEECH
    assert final_router.KEEP_MESSAGE in final_router.STATIC_SPEECH


# ── b. bật cảnh báo vật cản có tiếng xác nhận ────────────────────────────────

def test_alert_on_reply_carries_the_confirmation_cue():
    reply = _route("Bật cảnh báo vật cản", "alert_on", {})
    assert reply.cues == ["nav_alert_on"] and reply.live == "nav"
    # cue nằm trong flash kính; kính cũ không có clip thì server đọc thay bằng chữ này
    assert cues.fallback_text("nav_alert_on") == "Đã bật cảnh báo."


# ── c. không báo bừa thành công ──────────────────────────────────────────────

def test_music_readback_does_not_claim_the_phone_is_opening_it():
    line = phone.readback("music_play", {"song": "nhạc bolero"})
    assert line == "Mở nhạc bolero."
    assert "Đang mở" not in line and "Đã" not in line
    assert phone.readback("music_play", {}) == "Mở nhạc."


def _stub_phone(monkeypatch, request_id="req-ux", call=None):
    monkeypatch.setattr(action_flow, "call_service_api", call or (lambda e, p: {"data": {"request_id": request_id}}))
    monkeypatch.setattr(action_flow, "get_request_status", lambda r: {"request_state": "processing"})
    monkeypatch.setattr(config, "VISIONCARE_RESULT_POLL_SECONDS", 0.02)


def test_unreachable_app_server_is_said_as_such_not_as_the_actions_own_failure(monkeypatch):
    def down(endpoint, payload):
        raise VisionCareAPIError("Lỗi kết nối VisionCare API: refused", connection=True)

    _stub_phone(monkeypatch, call=down)
    out = list(music.handle_volume(b"", "", {"direction": "down"}))
    assert out == [action_flow.NO_PHONE_CONNECTION]
    out = list(music.handle_play(b"", "", {"song": "Nơi này có anh - Sơn Tùng"}))
    assert out == [action_flow.NO_PHONE_CONNECTION]


def test_business_error_from_app_server_keeps_the_action_specific_failure(monkeypatch):
    def refuse(endpoint, payload):
        raise VisionCareAPIError("422", status_code=422)

    _stub_phone(monkeypatch, call=refuse)
    out = list(music.handle_volume(b"", "", {"direction": "down"}))
    assert out == [music._VOLUME_FAILURE]


def test_late_phone_result_is_spoken_after_the_still_working_line(monkeypatch):
    """Quá mốc SLO điện thoại mới báo về: trước đây người dùng nghe 'sẽ cập nhật khi có kết quả' rồi im mãi."""
    action_callbacks.reset()
    _stub_phone(monkeypatch, request_id="req-late")
    monkeypatch.setattr(config, "VISIONCARE_FAST_ACTION_SECONDS", 0.05)
    monkeypatch.setattr(config, "VISIONCARE_RESULT_TIMEOUT_SECONDS", 5.0)
    threading.Timer(0.3, lambda: action_callbacks.resolve(
        "req-late", {"request_state": "succeeded", "result": {"playback_state": "stopped"}})).start()

    out = list(music.handle_stop(b"", "", {}))

    assert out[0].strip() == music._STOP_ACK
    assert action_flow._STILL_WORKING in out[1]
    assert out[-1] == "Đã dừng phát nhạc."


def test_late_phone_failure_is_spoken(monkeypatch):
    action_callbacks.reset()
    _stub_phone(monkeypatch, request_id="req-late2")
    monkeypatch.setattr(config, "VISIONCARE_FAST_ACTION_SECONDS", 0.05)
    monkeypatch.setattr(config, "VISIONCARE_RESULT_TIMEOUT_SECONDS", 5.0)
    threading.Timer(0.3, lambda: action_callbacks.resolve(
        "req-late2", {"request_state": "failed", "error": {"code": "DELIVERY_FAILED"}})).start()

    out = list(music.handle_stop(b"", "", {}))

    assert out[-1] == result_speech._ERROR_SPEECH["DELIVERY_FAILED"]


def test_phone_never_answering_ends_with_the_timeout_line(monkeypatch):
    action_callbacks.reset()
    _stub_phone(monkeypatch, request_id="req-never")
    monkeypatch.setattr(config, "VISIONCARE_FAST_ACTION_SECONDS", 0.05)
    monkeypatch.setattr(config, "VISIONCARE_RESULT_TIMEOUT_SECONDS", 0.3)

    out = list(music.handle_stop(b"", "", {}))

    assert out[-1] == result_speech._TIMEOUT_MESSAGE
    assert "thông báo" not in out[-1]  # người nghe qua kính không xem được thông báo


@pytest.mark.parametrize("text", ["tắt cảnh báo", "tất cảnh báo vật cản", "tắc cảnh báo", "hủy cảnh báo", "thôi cảnh báo đi"])
def test_stop_alert_keyword_rule_survives_stt_near_misses(text):
    from pipeline.intent_early import keyword_rule
    assert keyword_rule(text)[0] == "alert_off"
