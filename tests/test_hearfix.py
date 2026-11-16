"""V2: cứu lệnh bị STT nghe sai khi bộ phân loại cho `chat` (pipeline/hearfix.py + final_router)."""

import asyncio

import numpy as np
import pytest

import config
from lanes import phone
from pipeline import final_router, hearfix, pending_action
from pipeline.hearfix import Suggestion
from pipeline.intent_early import Decision

DEV = "dev-hearfix"


@pytest.fixture(autouse=True)
def _clean():
    for table in (phone._CONFIRM, phone._PENDING, phone._CHOICE, phone._OFFER, phone._BODY):
        table.clear()
    pending_action.cancel(DEV, "test")
    yield
    for table in (phone._CONFIRM, phone._PENDING, phone._CHOICE, phone._OFFER, phone._BODY):
        table.clear()
    pending_action.cancel(DEV, "test")


def _route(text, sug, state=None):
    import pipeline.hearfix as hf

    orig = hf.suggest
    hf.suggest = lambda t, s=None: sug
    try:
        ctx = final_router.TurnContext(DEV, "t", None, None)
        ctx.device_state = state or {}
        return asyncio.run(final_router.route(ctx, Decision("chat", source="model", backend="gemma", confidence=0.9), text))
    finally:
        hf.suggest = orig


# ── chuẩn hoá gần âm ────────────────────────────────────────────────────────

def test_fold_and_phon_merge_stt_confusions():
    assert hearfix.fold("Tiêu đề báo!") == "tieu de bao"
    assert hearfix.phon("Nhắn") == hearfix.phon("Nhận")  # chỉ khác dấu
    assert hearfix.phon("trần") == hearfix.phon("chân")  # tr/ch
    assert hearfix.phon("xin") == hearfix.phon("sin")  # x/s


# ── quyết định theo ngưỡng ──────────────────────────────────────────────────

CLASSES = ["chat", "find", "message_send", "ocr", "ride_confirm", "space"]


def _p(**kw):
    v = np.array([kw.get(c, 0.0) for c in CLASSES])
    return v / v.sum()


def test_decide_tiers():
    assert hearfix.decide(_p(chat=0.9, find=0.1), CLASSES) is None
    s = hearfix.decide(_p(chat=0.1, find=0.85, ocr=0.05), CLASSES)
    assert (s.kind, s.intent) == ("act", "find")
    s = hearfix.decide(_p(chat=0.1, message_send=0.85, ocr=0.05), CLASSES)
    assert (s.kind, s.intent) == ("confirm", "message_send")
    s = hearfix.decide(_p(chat=0.2, ocr=0.6, find=0.15, space=0.05), CLASSES)
    assert s.kind == "options" and s.options == ["ocr", "find"] and s.prob < config.HEARFIX_STRONG
    assert hearfix.decide(_p(chat=0.5, ocr=0.3, find=0.2), CLASSES) is None  # chat vẫn đứng đầu
    assert hearfix.decide(_p(chat=0.4, ocr=0.4, find=0.2), CLASSES) is None  # chưa đủ manh mối
    assert hearfix.decide(_p(chat=0.05, find=0.95), CLASSES, words=config.HEARFIX_MAX_WORDS + 1) is None  # quá dài


def test_ride_confirm_needs_a_pending_quote():
    p = _p(chat=0.05, ride_confirm=0.9, find=0.05)
    assert hearfix.decide(p, CLASSES, {}) is None or hearfix.decide(p, CLASSES, {}).intent != "ride_confirm"
    assert hearfix.decide(p, CLASSES, {"ride_quote_pending": True}).intent == "ride_confirm"


def test_options_speech_reads_back_what_was_heard():
    q = hearfix.options_question("Tiêu đê bảo.", ["ocr", "find", "space"])
    assert q == "Mình nghe là 'Tiêu đê bảo'. Bạn muốn đọc chữ, tìm đồ hay tả cảnh?"
    assert hearfix.options_question("x", ["ocr"]) == "Mình nghe là 'x'. Bạn muốn đọc chữ?"


def test_disabled_or_missing_model_changes_nothing(monkeypatch):
    monkeypatch.setattr(config, "HEARFIX_ENABLED", False)
    assert hearfix.suggest("tràn nước suối để ở đâu") is None
    monkeypatch.setattr(config, "HEARFIX_ENABLED", True)
    monkeypatch.setattr(hearfix, "_model", None)
    monkeypatch.setattr(hearfix, "_model_failed", True)
    assert hearfix.suggest("tràn nước suối để ở đâu") is None


# ── định tuyến ──────────────────────────────────────────────────────────────

def test_no_suggestion_falls_through_to_chat(monkeypatch):
    seen = []
    monkeypatch.setattr(final_router, "_lane_a", lambda ctx, d, text: _async_reply(seen, d, text))
    reply = _route("Vịnh Hạ Long thuộc tỉnh nào", None)
    assert reply.lane == "A" and reply.intent == "chat"
    assert seen == [("chat", "Vịnh Hạ Long thuộc tỉnh nào")]


async def _async_reply(seen, d, text):
    from pipeline.reply import Reply

    seen.append((d.intent, text))
    return Reply(speech=["x"], intent=d.intent, lane="A")


def test_options_reply_has_no_side_effect():
    sug = Suggestion("options", "ocr", 0.5, ["ocr", "find", "space"])
    reply = _route("tiêu đê bảo", sug)
    assert list(reply.speech) == ["Mình nghe là 'tiêu đê bảo'. Bạn muốn đọc chữ, tìm đồ hay tả cảnh?"]
    assert reply.on_audio_start is None and reply.live is None and not phone.has_pending(DEV)


def test_confirm_asks_best_guess_and_waits_for_yes(monkeypatch):
    async def fake_extract(intent, text):
        return {"contact_name": "mẹ"}

    monkeypatch.setattr(phone, "extract", fake_extract)
    reply = _route("tội cho mẹ", Suggestion("confirm", "contact_call", 0.8))
    assert list(reply.speech) == ["Gọi cho mẹ phải không?"]
    assert reply.on_audio_start is None  # chưa xếp lệnh nào: phải chờ "đúng"
    assert phone.has_pending(DEV)
    assert phone.take_pending_confirm(DEV) == ("contact_call", {"contact_name": "mẹ"})


def test_confirm_without_params_asks_generically(monkeypatch):
    async def fake_extract(intent, text):
        return {}

    monkeypatch.setattr(phone, "extract", fake_extract)
    reply = _route("nắng cho", Suggestion("confirm", "message_send", 0.8))
    assert list(reply.speech) == ["Bạn muốn nhắn tin phải không?"]


def test_confirmed_guess_goes_through_normal_readback(monkeypatch):
    """Sau "đúng", lệnh đi đúng đường làn D (đọc lại + cửa sổ huỷ), không chạy ngay."""
    async def fake_extract(intent, text):
        return {"contact_name": "mẹ"}

    monkeypatch.setattr(phone, "extract", fake_extract)
    _route("tội cho mẹ", Suggestion("confirm", "contact_call", 0.8))
    monkeypatch.setattr(final_router, "_call_blocker", lambda ctx: None)
    ctx = final_router.TurnContext(DEV, "t2", None, None)
    reply = asyncio.run(final_router.route(ctx, Decision("chat", source="model", backend="gemma", confidence=0.9), "Đúng"))
    assert list(reply.speech) == ["Đang gọi cho mẹ."] and reply.on_audio_start is not None
    pending_action.cancel(DEV, "test")


def test_act_runs_a_stop_command_directly(monkeypatch):
    ran = []
    monkeypatch.setitem(phone.HANDLERS, "nav_stop", lambda image, text, params=None: iter(ran.append("stop") or ["Đã dừng chỉ đường."]))
    reply = _route("nhưng lộ trình này", Suggestion("act", "nav_stop", 0.9))
    assert reply.intent == "nav_stop" and reply.lane == "D"
    assert "".join(reply.speech) == "Đã dừng chỉ đường." and ran == ["stop"]


def test_needs_web_chat_is_never_touched():
    ctx = final_router.TurnContext(DEV, "t", None, None)
    d = Decision("chat", needs_web=True, source="model", backend="gemma", confidence=0.9)
    import pipeline.hearfix as hf

    called = []
    orig = hf.suggest
    hf.suggest = lambda *a, **k: called.append(1)
    try:
        # _web_answer cần mạng/search: chỉ cần chắc chắn hearfix không được hỏi tới
        reply = asyncio.run(final_router.route(ctx, d, "thời tiết hôm nay"))
        reply.speech = ()
    finally:
        hf.suggest = orig
    assert not called


# ── mô hình đã huấn luyện (file phát hành) ─────────────────────────────────

@pytest.mark.skipif(not hearfix.MODEL_PATH.exists(), reason="chưa huấn luyện: python -m tools.train_hearfix")
class TestShippedModel:
    def test_loads_and_is_fast(self):
        import time

        m = hearfix.Model.load()
        t0 = time.perf_counter()
        for _ in range(50):
            hearfix.decide(m.proba("Tràn nước suối để ở đâu"), m.classes, {})
        assert (time.perf_counter() - t0) / 50 < 0.005  # < 5 ms

    @pytest.mark.parametrize("text", [
        "Vịnh Hạ Long thuộc tỉnh nào", "Hôm nay trời đẹp quá", "Kể cho tôi nghe một câu chuyện cười",
        "Thủ đô của nước Pháp là gì", "Một cân bằng bao nhiêu gam", "Tại sao bầu trời có màu xanh",
        "Sông dài nhất Việt Nam là sông nào", "Cho tôi biết ý nghĩa của từ kiên nhẫn", "Bạn tên là gì",
        "Nấu phở bò cần những nguyên liệu gì", "Hôm nay là thứ mấy", "Ai là người phát minh ra bóng đèn",
        "Cách pha cà phê sữa đá ngon", "Con mèo thường sống được bao lâu", "Năm nay Tết rơi vào ngày nào",
    ])
    def test_real_questions_are_left_alone(self, text):
        assert hearfix.suggest(text, {}) is None
