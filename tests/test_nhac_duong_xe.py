"""Nhạc / dẫn đường / đặt xe: ít hỏi lại nhất (2026-10-22).

Nguồn: hội thoại giả lập 28 kịch bản với Gemma thật — "Nhạc to quá" bị hỏi "tăng hay giảm?", trả lời "Bạn muốn đi
đâu?" xong lại bị hỏi "... phải không?", "Không tìm thấy địa điểm, nói lại tên" rồi tên nói lại rơi sang trò chuyện.
"""
import asyncio
import threading

import pytest

from handlers import action_flow, result_speech
from lanes import phone
from pipeline import device_ctx, final_router, pending_action
from pipeline.intent_early import Decision

DEV = "dev-nhac-duong-xe"


@pytest.fixture(autouse=True)
def _clean():
    for table in (phone._CONFIRM, phone._PENDING, phone._CHOICE, phone._OFFER, phone._BODY):
        table.clear()
    pending_action.cancel(DEV, "test")
    yield
    for table in (phone._CONFIRM, phone._PENDING, phone._CHOICE, phone._OFFER, phone._BODY):
        table.clear()
    pending_action.cancel(DEV, "test")


def _route(text, intent, conf=1.0):
    ctx = final_router.TurnContext(DEV, "t", None, None)
    d = Decision(intent, source="model", backend="gemma", confidence=conf)
    return asyncio.run(final_router.route(ctx, d, text))


@pytest.mark.parametrize("text,expected", [
    ("Nhạc to quá", {"direction": "down"}),
    ("Ồn quá", {"direction": "down"}),
    ("Nhỏ tiếng lại một chút", {"direction": "down"}),
    ("Vặn nhỏ nhạc lại", {"direction": "down"}),
    ("Nhạc nhỏ tiếng quá", {"direction": "up"}),
    ("Tôi không nghe rõ", {"direction": "up"}),
    ("To lên chút", {"direction": "up"}),
    ("Mở to nhất", {"volume": 100}),
    ("Âm lượng 30", {"volume": 30}),
    ("Âm lượng", {}),  # không rõ hướng: vẫn hỏi, không tự tăng
])
def test_volume_understands_complaints_without_asking(text, expected):
    assert asyncio.run(phone.extract("music_volume", text)) == expected


@pytest.mark.parametrize("song,line", [
    ("nhạc", "Mở nhạc."),
    ("bài khác", "Mở nhạc."),
    ("đổi bài khác đi", "Mở nhạc."),
    ("bài hát Lạc trôi", "Mở nhạc Lạc trôi."),
])
def test_music_readback_never_reads_a_generic_request_as_a_title(song, line):
    assert phone.readback("music_play", {"song": song}) == line


@pytest.mark.parametrize("intent,slot", [("nav_start", "destination"), ("ride_quote", "destination")])
def test_answering_the_slot_question_runs_after_readback_without_asking_again(intent, slot):
    phone.set_pending(DEV, intent)

    reply = _route("Chợ Rẫy", "chat")

    assert not phone.has_pending(DEV)
    assert all("?" not in line for line in reply.speech)
    assert reply.on_audio_start is not None  # lệnh xếp hàng, đếm ngược khi câu đọc lại tới kính
    assert reply.params[slot] == "Chợ Rẫy"


def test_answering_the_contact_question_still_asks_to_confirm():
    """Gọi người khác vẫn hỏi lại: câu điền tên tự do không đủ tin để gọi thẳng."""
    phone.set_pending(DEV, "contact_call")
    with device_ctx.use(DEV, True):
        from live import call_bridge
        saved = call_bridge.unavailable_reason
        call_bridge.unavailable_reason = lambda *a, **k: None
        try:
            reply = _route("Mẹ", "chat")
        finally:
            call_bridge.unavailable_reason = saved
    assert list(reply.speech) == ["Gọi cho Mẹ phải không?"]


def test_saying_no_to_the_slot_question_cancels():
    phone.set_pending(DEV, "nav_start")

    reply = _route("Thôi", "chat")

    assert list(reply.speech) == [final_router.CANCELLED_MESSAGE]
    assert reply.on_audio_start is None


@pytest.mark.parametrize("intent,code", [
    ("nav_start", "INVALID_DESTINATION"),
    ("ride_quote", "INVALID_DESTINATION"),
    ("music_play", "SONG_NOT_FOUND"),
])
def test_not_found_reopens_the_slot_so_the_next_sentence_is_the_name(intent, code, monkeypatch):
    line = result_speech.error_speech(code)
    monkeypatch.setattr(phone, "speech", lambda *a, **k: iter([line]))
    heard = []
    with device_ctx.use(DEV):
        phone.run_background(intent, "x", {}, deliver=heard.append).join(2)
    assert heard == [line]
    assert phone.take_pending(DEV) == (intent, phone.SLOT[intent])


def test_other_failures_do_not_reopen_the_slot(monkeypatch):
    monkeypatch.setattr(phone, "speech", lambda *a, **k: iter([result_speech.error_speech("NO_RIDE_AVAILABLE")]))
    with device_ctx.use(DEV):
        phone.run_background("ride_quote", "x", {}, deliver=lambda _: None).join(2)
    assert phone.take_pending(DEV) is None


def test_ride_and_navigation_results_are_honest_and_screen_free():
    quote = result_speech._success_speech("ride_quote", {"quote_id": "q"})
    confirm = result_speech._success_speech("ride_confirm", {"ride_status": "requested"})
    # Grab chỉ đặt khi người dùng bấm Đặt xe trong Grab: không nói "đã gửi yêu cầu đặt xe", không bắt nói "xác nhận".
    assert "nói xác nhận" not in quote and "Đặt xe" in quote
    assert "Đã gửi yêu cầu" not in confirm
    nav_stop = result_speech._success_speech("navigation_stop", {})
    assert "Google Maps" in nav_stop  # điện thoại không tắt được Maps: nói rõ, không báo bừa
    for line in (result_speech._success_speech("navigation_start", {}), quote):
        assert "thông báo" not in line
