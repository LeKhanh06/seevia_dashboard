"""Thống kê các chức năng còn lại (Task 13): tiền (`money_decided`/`money_uncertain`, chụp lại ghép tác vụ), chat
(`needs_web_decided` + `needs_web:<chủ đề>`), tìm đồ/mô tả/hỏi ảnh ("không thấy" -> failed), nhạc, phủ intent, riêng tư.
"""

import asyncio
import json
import time
from types import SimpleNamespace

import pytest

import config
import schemas
from handlers import read_money
from lanes import general
from pipeline import final_router, image_quality, intent_early, usage_turn
from pipeline.intent_early import Decision
from pipeline.reply import Reply
from pipeline.usage_tasks import tracker
from services import usage
from tests.test_money_retry import _Box, _drive, _fast, _jpeg, _patch_read

DEV = "dev-money"  # = thiết bị phiên live giả `_Box` của test_money_retry


@pytest.fixture
def events(monkeypatch):
    out: list[dict] = []
    real_emit = usage.emit

    def capture(etype, **fields):
        out.append({"type": etype, **{k: v for k, v in fields.items() if v is not None}})
        return real_emit(etype, **fields)

    monkeypatch.setattr(usage, "emit", capture)
    monkeypatch.setattr(final_router, "_sync", lambda ctx, agen: agen)  # SyncIter cần loop của lượt thật
    tracker.reset()
    usage_turn.reset_for_tests()
    yield out
    tracker.reset()
    usage_turn.reset_for_tests()


def _of(events, etype, **match):
    return [e for e in events if e["type"] == etype and all(e.get(k) == v for k, v in match.items())]


def _signals(events, attempt_id):
    return sorted((s["signal"], s.get("reason_code") or "") for s in _of(events, "attempt_signal", attempt_id=attempt_id))


class _Warmer:
    def session(self, mode, params=None):
        return SimpleNamespace(token="tok-f")


def _ctx(single_shot=True):
    async def image():
        jpeg = _jpeg(sharp=True)
        return SimpleNamespace(jpeg=jpeg, llm_jpeg=lambda: jpeg)

    return SimpleNamespace(device=DEV, single_shot=single_shot, prewarmer=_Warmer(), image=image, spec_answer=None)


def _turn(route, consume=True):
    """Một lượt: giữ chỗ -> route (cùng ngữ cảnh, như pipeline/turn.py) -> mở -> đọc hết lời đáp -> kính báo played."""
    attempt = usage_turn.begin(DEV, time.monotonic(), transport="ws")
    reply = asyncio.run(route())
    usage_turn.routed(attempt, reply)
    if consume:
        asyncio.run(_drain(reply.speech))
        usage_turn.played(attempt, None, {})
    return attempt, reply


async def _drain(speech):
    if hasattr(speech, "__aiter__"):
        return [piece async for piece in speech]
    return list(speech)


def _money(monkeypatch, label, prob, top=None, single_shot=True):
    async def fake(messages):
        return label, prob, top if top is not None else [(label, prob)]

    monkeypatch.setattr(read_money, "choose_ranked", fake)
    ctx = _ctx(single_shot)
    return lambda: final_router._lane_b(ctx, Decision("money"), "đọc tiền")


def _finished(events, attempt_id):
    (fin,) = _of(events, "attempt_finished", attempt_id=attempt_id)
    return fin


# ── tiền ─────────────────────────────────────────────────────────────────────────────────────

def test_money_uncertain_signal_and_retake_merge(monkeypatch, events):
    first, reply = _turn(_money(monkeypatch, "50 nghìn đồng", 0.9, [("50 nghìn đồng", 0.9), ("500 nghìn đồng", 0.05)]))
    assert list(reply.speech) == [read_money.STEP_BACK]
    assert _signals(events, first) == [("money_decided", ""), ("money_uncertain", "step_back")]
    assert _finished(events, first)["status"] == "uncertain"
    assert _of(events, "task_finished") == []  # câu hướng dẫn: tác vụ còn mở chờ chụp lại

    second, _ = _turn(_money(monkeypatch, "50 nghìn đồng", 0.9999))
    assert _signals(events, second) == [("money_decided", "")]
    assert _finished(events, second)["status"] == "success"
    (start1,) = _of(events, "attempt_started", attempt_id=first)
    (start2,) = _of(events, "attempt_started", attempt_id=second)
    assert start1["task_id"] == start2["task_id"]  # chụp lại = cùng tác vụ, hai lượt
    (task,) = _of(events, "task_finished")
    assert task["status"] == "success"


def test_money_no_money_is_failed_and_model_error_is_technical(monkeypatch, events):
    attempt, _ = _turn(_money(monkeypatch, "không có tiền", 0.9))
    fin = _finished(events, attempt)
    assert (fin["status"], fin["error_code"]) == ("failed", "NO_MONEY")
    assert _signals(events, attempt) == [("money_decided", "")]

    async def broken(messages):
        raise RuntimeError("mất mạng")

    monkeypatch.setattr(read_money, "choose_ranked", broken)
    ctx = _ctx()
    attempt, _ = _turn(lambda: final_router._lane_b(ctx, Decision("money"), "đọc tiền"))
    fin = _finished(events, attempt)
    assert (fin["status"], fin["error_code"]) == ("technical_error", "MODEL_ERROR")
    assert _signals(events, attempt) == []  # không có kết quả nhận diện: không vào mẫu số tỷ lệ uncertain


@pytest.mark.parametrize("retry_label,status", [("50 nghìn đồng", "success"), ("không rõ", "uncertain")])
def test_money_retry_live_decides_attempt(monkeypatch, events, retry_label, status):
    # Lượt có WS: đọc chưa chắc -> câu hướng dẫn + phiên live đọc lại; lượt chốt theo lần đọc lại.
    attempt, reply = _turn(_money(monkeypatch, "50 nghìn đồng", 0.9, single_shot=False), consume=False)
    assert reply.live == "ocr"
    usage_turn.played(attempt, None, {})
    assert _of(events, "attempt_finished", attempt_id=attempt) == []  # còn chờ phiên live

    _fast(monkeypatch)
    _patch_read(monkeypatch, label=retry_label, prob=0.9999)
    box = _Box(monkeypatch)
    box.session.params["usage_attempt"] = attempt
    asyncio.run(_drive(box, [(0.4, _jpeg(sharp=True))]))
    assert _finished(events, attempt)["status"] == status
    assert _signals(events, attempt) == [("money_decided", ""), ("money_uncertain", "come_closer")]
    assert (_of(events, "task_finished") == []) == (status == "uncertain")


def test_money_retry_closed_before_reading_is_cancelled(monkeypatch, events):
    attempt, _ = _turn(_money(monkeypatch, "50 nghìn đồng", 0.9, single_shot=False))
    _fast(monkeypatch)
    box = _Box(monkeypatch)
    box.session.params["usage_attempt"] = attempt
    box.session.closed.set()
    asyncio.run(_drive(box, []))
    assert _finished(events, attempt)["status"] == "cancelled"


def test_no_money_amount_in_events(monkeypatch, events):
    _turn(_money(monkeypatch, "500 nghìn đồng", 0.9999))
    _turn(_money(monkeypatch, "20 nghìn đồng", 0.9, [("20 nghìn đồng", 0.9), ("200 nghìn đồng", 0.1)]))
    dumped = json.dumps(events, ensure_ascii=False)
    for secret in ("nghìn", "đồng", "500000", "20000", "500 ", "20 "):
        assert secret not in dumped


# ── chat ─────────────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("text,topic", [
    ("thời tiết hôm nay thế nào", "weather"),
    ("ngày mai trời có mưa không", "weather"),
    ("giá vàng hôm nay bao nhiêu", "price"),
    ("tỷ giá đô la", "price"),
    ("có tin tức gì mới không", "news"),
    ("đọc tin thời sự", "news"),
    ("tỉ số trận tối qua", "sports"),
    ("lịch thi đấu của việt nam", "sports"),
    ("lịch chiếu phim tối nay", "schedule"),
    ("siêu thị mấy giờ mở cửa", "schedule"),
    ("nhà thuốc gần đây ở đâu", "other"),
    ("kể chuyện cười đi", "other"),
])
def test_web_topic(text, topic):
    assert intent_early.web_topic(text) == topic


def test_chat_needs_web_rate_inputs(monkeypatch, events):
    async def fake_answer(intent, text, image=None, search=None, search_failed=False):
        yield "Trả lời.\n"

    monkeypatch.setattr(general, "answer", fake_answer)
    monkeypatch.setattr(final_router.search, "available", lambda: False)
    ctx = _ctx()
    web, _ = _turn(lambda: final_router._lane_a(ctx, Decision("chat", needs_web=True), "thời tiết hôm nay thế nào"))
    plain, _ = _turn(lambda: final_router._lane_a(ctx, Decision("chat"), "kể chuyện cười đi"))
    assert _signals(events, web) == [("needs_web", "weather"), ("needs_web_decided", "")]
    assert _signals(events, plain) == [("needs_web_decided", "")]
    assert _finished(events, web)["status"] == _finished(events, plain)["status"] == "success"
    assert "thời tiết" not in json.dumps(events, ensure_ascii=False)


# ── tìm đồ / mô tả / hỏi ảnh / hỏi vật cản ──────────────────────────────────────────────────

@pytest.mark.parametrize("intent,first,status,code", [
    ("find", "Tôi không thấy rõ chìa khoá, vui lòng chụp lại gần hơn.", "failed", "NOT_FOUND"),
    ("find", "Không thấy cái ví trong ảnh.", "failed", "NOT_FOUND"),
    ("find", "Chìa khoá ở bên trái, cách khoảng một bước.", "success", None),
    ("ask", "Không đọc được ngày, chữ bị mờ.", "failed", "UNREADABLE"),
    ("ask", "Không thấy ai trước mặt bạn.", "success", None),  # một câu trả lời thật, không phải ảnh hỏng
    ("space", "Tôi không nhìn rõ, ảnh quá tối.", "failed", "UNREADABLE"),
    ("hazard", "Không thấy vật cản phía trước, đường thông.", "success", None),
    ("hazard", "Không nhìn rõ phía trước, ảnh bị mờ.", "failed", "UNREADABLE"),
])
def test_find_not_found_is_failed(monkeypatch, events, intent, first, status, code):
    async def fake_answer(intent_, text, image=None, search=None, search_failed=False):
        yield first + "\n"
        yield "Câu sau."

    monkeypatch.setattr(general, "answer", fake_answer)
    monkeypatch.setattr(image_quality, "reject_reason", lambda intent_, jpeg: None)
    ctx = _ctx()
    attempt, _ = _turn(lambda: final_router._lane_a(ctx, Decision(intent), "câu lệnh"))
    fin = _finished(events, attempt)
    assert (fin["status"], fin.get("error_code")) == (status, code)
    # Không thấy -> câu đã gợi ý chụp lại: tác vụ còn mở để lượt sau ghép vào.
    assert (_of(events, "task_finished") == []) == (status == "failed")


def test_judge_only_reads_first_sentence():
    assert usage_turn.judge("find", "Chìa khoá ở bên trái. Không thấy cái ví.") is None
    assert usage_turn.judge("chat", "Không thấy.") is None  # chat không chấm


# ── nhạc / phủ intent ────────────────────────────────────────────────────────────────────────

def test_music_song_not_found_error_code(events):
    attempt = usage_turn.begin(DEV, time.monotonic(), transport="ws")
    usage_turn.routed(attempt, Reply(intent="music_play", lane="D", awaits_action=True))
    usage_turn.played(attempt, None, {})
    usage_turn.phone_result(attempt, {"request_state": "failed", "error": {"code": "SONG_NOT_FOUND"}})
    (start,) = _of(events, "attempt_started", attempt_id=attempt)
    assert start["feature"] == "music"
    fin = _finished(events, attempt)
    assert (fin["status"], fin["error_code"]) == ("failed", "SONG_NOT_FOUND")
    assert ("reprompt", "missing_slot") in _signals(events, attempt)


def test_every_intent_maps_to_feature_or_none():
    intents = {v for k, v in vars(schemas.Intent).items() if k.isupper()} | set(intent_early.LANE)
    for intent in intents:
        assert (usage_turn.feature_for(intent) is None) == (intent in usage_turn.NOT_MEASURED), intent
    assert set(usage_turn.NOT_MEASURED) <= intents
    assert usage_turn.INDEPENDENT == {"emergency_call"}
    assert config.USAGE_MERGE_WINDOW_S == 60
