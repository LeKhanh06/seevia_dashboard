"""Kênh SIM (CONTACT_CHANNEL=sim): gọi/nhắn qua danh bạ + SIM điện thoại; kênh internet giữ nguyên."""

import pytest

import config
from handlers import phone as phone_handlers, result_speech
from lanes import phone
from live import messages
from models import llm
from pipeline import final_router, tts
from tests.test_v2_api import _gemma, world  # noqa: F401 (fixture)
from tests.test_v2_calls_routing import (  # noqa: F401 (fixture rig)
    DEV, _err, _hello, _ok, _say, _starts, _wait, rig,
)
from tests.test_v2_session import _recv_result_reply, _recv_until_end, _send_turn


@pytest.fixture
def srig(rig, monkeypatch):  # noqa: F811
    """`rig` ở kênh SIM, handler thật, TTS ghi lại câu đọc, Gemma trích tham số theo `rig.extract`."""
    monkeypatch.setattr(config, "CONTACT_CHANNEL", "sim")
    monkeypatch.setattr(config, "VISIONCARE_FAST_ACTION_SECONDS", 0.1)
    monkeypatch.setitem(phone.HANDLERS, "message_send", phone_handlers.handle_message_send)
    monkeypatch.setitem(phone.HANDLERS, "message_read", phone_handlers.handle_message_read)
    phone._BODY.clear()
    messages.reset()
    spoken: list[str] = []
    extract: dict = {"contact_name": "em iu", "text": ""}

    def record(text):
        spoken.append(text)
        yield b"\x10\x00" * 8000

    async def fake_json(prompt, schema, max_tokens=120):
        return dict(extract)

    monkeypatch.setattr(tts, "synthesize_stream", record)
    monkeypatch.setattr(llm, "generate_json", fake_json)
    rig.spoken, rig.extract = spoken, extract
    return rig


def _said(rig, needle):
    # Câu lỗi hai vế ("Có nhiều người trùng tên. Bạn nói rõ…") được TTS tách thành hai dòng: so trên cả lời đã nói.
    return needle in " ".join(rig.spoken)


def _finish(rig, path_suffix, state="succeeded", result=None, error=None):
    rig.replies[path_suffix] = _ok({"request_id": "r-1", "operation": "x", "request_state": "processing"})
    rig.replies["/requests/r-1"] = _ok({"request_id": "r-1", "request_state": state, "result": result, "error": error})


def _turn(rig, text, intent, until=None, turn="T1", pushed=True):
    """Một lượt nói; `until` chờ NGAY TRONG phiên (TestClient huỷ phiên khi thoát `with`, việc nền chết theo)."""
    _say(rig, text, intent)
    with rig.client.websocket_connect("/v2/session") as ws:
        _hello(ws)
        _send_turn(ws, turn)
        _recv_until_end(ws, turn)
        if until is not None:
            assert _wait(until, timeout=10)
            if pushed:
                _recv_result_reply(ws)  # để phiên rảnh trước khi thoát `with`


def test_internet_channel_still_blocks_without_own_token(monkeypatch):
    """(a) Kênh internet: thiếu phiên/token riêng thì chặn TRƯỚC khi hỏi tên / đọc lại, đúng như cũ."""
    import asyncio
    from types import SimpleNamespace

    monkeypatch.setattr(config, "CONTACT_CHANNEL", "internet")
    from live import call_bridge

    monkeypatch.setattr(final_router, "_call_blocker", lambda ctx: call_bridge.NEED_UPDATE)
    d = SimpleNamespace(intent="contact_call")
    reply = asyncio.run(final_router._lane_d(SimpleNamespace(device="d"), d, "gọi cho em iu", {"contact_name": "em iu"}))
    assert reply.speech == [call_bridge.NEED_UPDATE]
    d = SimpleNamespace(intent="message_send")
    reply = asyncio.run(final_router._lane_d(SimpleNamespace(device="d"), d, "x", {"contact_name": "a", "text": "b"}))
    assert reply.speech == [phone_handlers.MSG_NEED_UPDATE]  # nhắn tin nói "nhắn tin" thay vì "gọi điện"


def test_sim_channel_skips_blocker(srig, monkeypatch):
    """(b) Kênh SIM: "gọi cho em iu" không bị chặn (blocker thật vẫn bật, kính chưa có phiên) và gọi contact/call."""
    monkeypatch.setattr(final_router, "_call_blocker", lambda ctx: "CHẶN")
    _finish(srig, "/contact/call", result={"contact_name": "em iu", "call_state": "calling"})
    _turn(srig, "gọi cho em iu", "contact_call", until=lambda: _starts(srig, "/contact/call"))
    assert not _said(srig, "CHẶN")
    assert _starts(srig, "/contact/call")[0]["name"] == "em iu"
    assert not _starts(srig, "/call/start")  # không đi đường AB


def test_sim_alias_em_yeu(srig):
    _finish(srig, "/contact/call", result={"contact_name": "em iu"})
    srig.extract.update(contact_name="em yêu")
    _turn(srig, "gọi cho em yêu", "contact_call", until=lambda: _starts(srig, "/contact/call"))
    assert _starts(srig, "/contact/call")[0]["name"] == "em iu"


def test_sim_message_readback_then_posts_sms(srig):
    """(c) "nhắn cho em iu là con về": đọc lại trước, contact/sms chỉ đi sau cửa sổ huỷ, đúng name+text, rồi "Đã gửi."."""
    _finish(srig, "/contact/sms", result={"contact_name": "em iu", "sms_sent": True})
    srig.extract.update(contact_name="em iu", text="con về")
    _turn(srig, "nhắn cho em iu là con về", "message_send", until=lambda: _said(srig, "Đã gửi."))
    assert _said(srig, "Nhắn cho em iu: con về.")
    assert srig.spoken.index("Nhắn cho em iu: con về.") < srig.spoken.index("Đã gửi.")  # đọc lại trước, gửi sau
    body = _starts(srig, "/contact/sms")[0]
    assert body["name"] == "em iu" and body["text"] == "con về"
    assert not _starts(srig, "/messages/send")


@pytest.mark.parametrize("state,error,expected", [
    ("failed", {"code": "CONTACT_NOT_FOUND"}, "Không có em iu trong danh bạ."),
    ("failed", {"code": "MULTIPLE_CONTACTS_FOUND"}, result_speech._ERROR_SPEECH["MULTIPLE_CONTACTS_FOUND"]),
    ("failed", {"code": "SMS_PERMISSION_DENIED"}, result_speech._ERROR_SPEECH["SMS_PERMISSION_DENIED"]),
    ("failed", {"code": "SMS_SEND_FAILED"}, result_speech._ERROR_SPEECH["SMS_SEND_FAILED"]),
])
def test_sim_errors_speak_short_sentences(srig, state, error, expected):
    """(d) Mã lỗi của app -> đúng câu ngắn."""
    _finish(srig, "/contact/sms", state=state, error=error)
    srig.extract.update(contact_name="em iu", text="con về")
    _turn(srig, "nhắn cho em iu là con về", "message_send", until=lambda: _said(srig, expected))


def test_sim_message_read_short_sentence(srig):
    """(e) Đọc tin nhắn ở kênh SIM: câu ngắn, không hỏi AB."""
    _turn(srig, "đọc tin nhắn", "message_read", until=lambda: _said(srig, phone_handlers.SIM_NO_READ), pushed=False)
    assert not any(p.endswith("/messages/unread") for p, _ in srig.ab if "unread" in p and p != "/api/v1/service/messages/unread")


def test_new_sentences_are_prebuilt():
    """(f) Câu mới có trong STATIC_SPEECH (dựng sẵn audio)."""
    assert phone_handlers.SIM_NO_READ in phone_handlers.STATIC_SPEECH
    assert "Đã gửi." in phone_handlers.STATIC_SPEECH and "Đã gửi." in result_speech.STATIC_SPEECH
    assert result_speech._ERROR_SPEECH["SMS_SEND_FAILED"] in result_speech.STATIC_SPEECH
