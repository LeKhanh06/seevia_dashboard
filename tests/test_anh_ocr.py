"""Chính sách ảnh đọc chữ (2026-10-22): đọc thẳng ảnh lượt SVGA, xin ĐÚNG MỘT `capture` khi chữ nhỏ, chặn vòng lặp Gemma."""

import asyncio
import io
import json
from types import SimpleNamespace

import pytest
from PIL import Image

import config
from lanes import ocr_live, repeat_guard
from live import protocol, text_align
from live.session import LiveSession
from live.ws_adapter import LiveSocket
from pipeline import final_router
from pipeline.intent_early import Decision


def _box(h, w=200, x=100, y=100):
    return (x, y, x + w, y + h)


# ── plan_read ────────────────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("line,kind,size", [
    (30, "read", ""),      # chữ lớn: ảnh lượt SVGA đủ
    (14, "read", ""),      # đúng ngưỡng
    (12, "capture", "sxga"),   # x1,6 -> 19 >= 18, XGA (x1,28) chưa đủ
    (15 * 0.8, "capture", "sxga"),
    (10, "capture", "uxga"),   # cần x2
    (8, "capture", "uxga"),    # chưa tới 18 nhưng UXGA-scaled 16 >= 12: vẫn xin UXGA
    (5, "align", ""),          # UXGA-scaled 10 < 12: chữ quá nhỏ, bảo người dùng lại gần
])
def test_plan_read_picks_smallest_sufficient_size(line, kind, size):
    plan = text_align.plan_read([_box(line)], 800, 600, 800)
    assert (plan.kind, plan.size) == (kind, size)


def test_plan_read_without_boxes_tries_reading_first():
    assert text_align.plan_read([], 800, 600, 800).kind == "try"


def test_plan_read_uses_real_pixels_of_turn_image():
    """Lỗi cũ (Q1): dòng 14 px trên ảnh 800 px bị nhân 1600/800 = 2 -> 28 và coi là đạt. Giờ hệ số 1."""
    assert text_align.plan_read([_box(14)], 800, 600, 800).kind == "read"
    assert text_align.plan_read([_box(10)], 800, 600, 800).kind == "capture"
    # ảnh lượt rộng 1600 giải mã về 800 (hệ số 2): hộp 7 px trên ảnh xám = 14 px thật
    assert text_align.plan_read([_box(7)], 800, 600, 1600).kind == "read"


def test_plan_read_ignores_flat_noise_boxes():
    noise = [(0, 0, 3, 3)] * 5 + [(0, 0, 10, 12)] * 5
    assert text_align.line_height(noise, 800, 800) == 0.0


def test_plan_read_never_asks_smaller_or_equal_size_than_turn_image():
    # ảnh lượt đã XGA: không xin lại XGA, chỉ xin lớn hơn
    plan = text_align.plan_read([_box(10)], 1024, 768, 1024)
    assert plan.kind == "capture" and plan.size in ("sxga", "uxga")


def test_text_region_in_native_pixels():
    plan = text_align.plan_read([(100, 100, 300, 130)], 400, 300, 800)  # ảnh xám nửa cỡ: toạ độ gấp đôi
    assert plan.box is not None and plan.box[0] < 200 and plan.box[2] > 600


# ── repeat_guard ─────────────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("text", [
    "te\n" + "Coca-Cola\n" * 5,
    "Bibo\nsnack\n" * 4,
    "x\n" + "...\n" * 6,
    "Địa chỉ: K2 - K3 - K4 - K5 - K6 - K7 - K8 - K9",
])
def test_repeat_guard_detects_loops(text):
    at = repeat_guard.cut_point(text)
    assert at is not None and at < len(text)


@pytest.mark.parametrize("text", [
    "Công ty A\nCông ty B\nSĐT 0909 123 456\nĐịa chỉ 12 Lê Lợi",
    "Phòng 101, 102 và 103 nằm ở tầng 1",
    "0969.694.922 0969.694.838 0969.281.808 0969.696.960",
    "Hello world this is\nHello world this is\nbye bye bye bye",
    "Giá 10\nGiá 20\nGiá 30\nGiá 40\nGiá 50\nGiá 60\nGiá 70",
    "Nước suối\nNước suối\nNước suối",   # ba dòng giống nhau còn hợp lệ (nhãn lặp trên kệ)
])
def test_repeat_guard_leaves_normal_text(text):
    assert repeat_guard.cut_point(text) is None


def test_read_image_stops_runaway_stream(monkeypatch):
    sent = []

    async def stream(messages, **kw):
        for i in range(200):
            sent.append(i)
            yield "Coca-Cola\n"

    monkeypatch.setattr(ocr_live.llm, "stream_chat", stream)
    monkeypatch.setattr(config, "OCR_DIGIT_HEDGE_PROB", 0.0)

    async def go():
        return [p async for p in ocr_live.read_image(_jpeg(100, 100), "raw", "dev-loop")]

    pieces = asyncio.run(go())
    assert len(sent) < 10, "stream phải bị dừng sớm, không chạy tới trần token"
    assert "".join(pieces).count("Coca-Cola") <= 4


# ── ocr_live.run: đường chụp một lần ─────────────────────────────────────────────────────────
def _jpeg(w, h, color=(200, 200, 200)) -> bytes:
    out = io.BytesIO()
    Image.new("RGB", (w, h), color).save(out, format="JPEG")
    return out.getvalue()


class _Rig:
    def __init__(self, monkeypatch, size="sxga", fallback=b"turn-image"):
        self.sent: list[dict] = []
        self.said: list[str] = []
        self.read: list[bytes] = []

        async def send_text(s):
            self.sent.append(json.loads(s))

        async def send_bytes(b):
            pass

        async def speak(ws, text):
            self.said.append("".join(text))

        def spoken(jpeg, mode, device):
            self.read.append(jpeg)
            return ["Nội dung."]

        monkeypatch.setattr(ocr_live, "speak_ws", speak)
        monkeypatch.setattr(ocr_live.speech, "sync_iter", lambda agen, loop: agen)
        monkeypatch.setattr(ocr_live, "spoken", spoken)
        monkeypatch.setattr(config, "LIVE_MAX_FPS", 0)
        self.ws = LiveSocket(send_text, send_bytes)
        self.session = LiveSession("tok", "dev-cap", "ocr", {"mode": "read", "capture_size": size, "fallback_jpeg": fallback})


def _plan_with_text(monkeypatch, has_text=True):
    boxes = (_box(30),) if has_text else ()
    monkeypatch.setattr(text_align, "plan_jpeg", lambda jpeg: text_align.ReadPlan("read" if has_text else "try", boxes=boxes))


def test_one_shot_asks_exactly_one_capture_and_reads_it(monkeypatch):
    rig = _Rig(monkeypatch)
    _plan_with_text(monkeypatch)
    hi = _jpeg(1280, 960)
    rig.ws.feed_frame(hi)
    asyncio.run(ocr_live.run(rig.ws, rig.session))
    assert rig.sent[0] == {"t": "capture", "size": "sxga"}
    assert {"t": "cue", "id": "hold_still"} in rig.sent
    assert not any(f["t"] == "cam" for f in rig.sent), "không stream khung live"
    assert rig.read == [hi]
    assert rig.sent[-1] == {"t": "end"}
    assert ocr_live.EXIT_READ not in rig.said, "đường nhanh không có câu mở chế độ nên không báo thoát"


def test_one_shot_ignores_small_live_frames_and_waits_for_capture(monkeypatch):
    rig = _Rig(monkeypatch, size="uxga")
    _plan_with_text(monkeypatch)
    hi = _jpeg(1600, 1200)
    rig.ws.feed_frame(_jpeg(640, 480))
    rig.ws.feed_frame(hi)
    asyncio.run(ocr_live.run(rig.ws, rig.session))
    assert rig.read == [hi]


def test_one_shot_falls_back_to_turn_image_on_timeout(monkeypatch):
    rig = _Rig(monkeypatch)
    monkeypatch.setattr(config, "OCR_CAPTURE_WAIT_S", 0.2)
    asyncio.run(ocr_live.run(rig.ws, rig.session))
    assert rig.read == [b"turn-image"]


def test_one_shot_falls_back_when_new_image_has_no_text(monkeypatch):
    """Người dùng đã dời tay trong lúc đẩy ảnh: ảnh mới không còn chữ thì đọc ảnh lượt (đã có chữ)."""
    rig = _Rig(monkeypatch)
    _plan_with_text(monkeypatch, has_text=False)
    rig.ws.feed_frame(_jpeg(1280, 960))
    asyncio.run(ocr_live.run(rig.ws, rig.session))
    assert rig.read == [b"turn-image"]


# ── final_router._lane_b ─────────────────────────────────────────────────────────────────────
class _Warmer:
    def __init__(self, plan):
        self.plan, self.calls = plan, []

    def assess_buffer(self):
        async def get():
            return self.plan

        return asyncio.ensure_future(get())

    def session(self, mode, params=None):
        self.calls.append((mode, params))
        return SimpleNamespace(token="tok-1")


def _route(plan, monkeypatch, spoken=None, collect=False):
    ctx = SimpleNamespace(device="dev-b", single_shot=False, loop=None, prewarmer=_Warmer(plan))

    async def image():
        return SimpleNamespace(jpeg=b"turn-image")

    ctx.image = image
    if spoken is not None:
        monkeypatch.setattr(ocr_live, "spoken", spoken)
    monkeypatch.setattr(final_router, "_sync", lambda ctx, agen: agen)

    async def go():
        reply = await final_router._lane_b(ctx, Decision("ocr", "read"), "đọc chữ")
        if collect:  # cùng event loop với generator đang treo
            reply.spoken = [p async for p in reply.speech]
        return reply

    return asyncio.run(go()), ctx


def test_lane_b_big_text_reads_turn_image_without_live(monkeypatch):
    plan = text_align.ReadPlan("read", line_px=30, width=800)
    reply, ctx = _route(plan, monkeypatch, spoken=lambda jpeg, mode, dev: ["x"])
    assert reply.live is None and not reply.cues and ctx.prewarmer.calls == []


def test_lane_b_small_text_asks_one_capture_without_cue(monkeypatch):
    plan = text_align.ReadPlan("capture", size="sxga", line_px=12, width=800)
    reply, ctx = _route(plan, monkeypatch)
    assert reply.live == "ocr" and reply.live_token == "tok-1" and not reply.cues
    mode, params = ctx.prewarmer.calls[0]
    assert mode == "ocr" and params["capture_size"] == "sxga" and params["fallback_jpeg"] == b"turn-image"


def test_lane_b_detector_miss_reads_when_gemma_finds_text(monkeypatch):
    async def reads(jpeg, mode, dev):
        yield "Biển hiệu.\n"
        yield "Mở cửa 8 giờ."

    reply, ctx = _route(text_align.ReadPlan("try"), monkeypatch, spoken=reads, collect=True)
    assert reply.live is None and ctx.prewarmer.calls == []
    assert reply.spoken == ["Biển hiệu.\n", "Mở cửa 8 giờ."]


def test_lane_b_detector_miss_goes_live_when_gemma_sees_no_text(monkeypatch):
    async def none(jpeg, mode, dev):
        yield ocr_live.NO_TEXT + "\n"

    reply, ctx = _route(text_align.ReadPlan("try"), monkeypatch, spoken=none)
    assert reply.live == "ocr" and reply.cues == ["ocr_start"]
    assert "capture_size" not in ctx.prewarmer.calls[0][1]


def test_lane_b_detector_miss_goes_live_on_llm_error(monkeypatch):
    async def boom(jpeg, mode, dev):
        raise ocr_live.llm.LLMError("mạng")
        yield ""

    reply, _ = _route(text_align.ReadPlan("try"), monkeypatch, spoken=boom)
    assert reply.live == "ocr"


def test_lane_b_tiny_text_goes_to_alignment(monkeypatch):
    reply, _ = _route(text_align.ReadPlan("align", line_px=3, width=800), monkeypatch)
    assert reply.live == "ocr" and reply.cues == ["ocr_start"]


def test_capture_frames_are_accepted_by_mailbox_limit():
    """UXGA q12 ~127 KB, ảnh nhiều chi tiết tới ~190 KB: vẫn dưới trần LIVE_MAX_FRAME_BYTES (300 KB) của server."""
    assert 200_000 < config.LIVE_MAX_FRAME_BYTES
    assert protocol.capture("sxga") == json.dumps({"t": "capture", "size": "sxga"})


# ── ảnh mờ: chụp lại một lần ─────────────────────────────────────────────────────────────────
def _text_gray(blur=0.0):
    import cv2
    import numpy as np

    img = np.full((600, 800), 255, np.uint8)
    for i in range(5):
        cv2.putText(img, "Chuong %d noi dung sach" % i, (80, 140 + 80 * i), cv2.FONT_HERSHEY_SIMPLEX, 1.4, 0, 3)
    return cv2.GaussianBlur(img, (0, 0), blur) if blur else img


def test_blur_ratio_separates_sharp_from_shaken():
    sharp, shaken = _text_gray(), _text_gray(2.5)
    boxes = [(60, 100, 700, 560)]
    assert text_align.blur_ratio(sharp, boxes) > config.OCR_BLUR_RATIO * 3
    assert text_align.blur_ratio(shaken, boxes) < config.OCR_BLUR_RATIO
    assert text_align.blur_ratio(sharp, []) == 0.0
    assert text_align.blur_ratio(sharp, [(0, 0, 8, 8)]) == 0.0  # vùng quá nhỏ: không đo, không đoán là mờ


def test_lane_b_blurry_text_recaptures_once_after_settle(monkeypatch):
    plan = text_align.ReadPlan("read", line_px=30, width=800, blur=2.0)
    reply, ctx = _route(plan, monkeypatch)
    assert reply.live == "ocr" and not reply.cues
    params = ctx.prewarmer.calls[0][1]
    assert params["capture_size"] == "svga" and params["capture_settle_s"] == config.OCR_BLUR_SETTLE_S


def test_lane_b_unknown_blur_reads_directly(monkeypatch):
    plan = text_align.ReadPlan("read", line_px=30, width=800, blur=0.0)
    reply, ctx = _route(plan, monkeypatch, spoken=lambda jpeg, mode, dev: ["x"])
    assert reply.live is None and ctx.prewarmer.calls == []


def test_lane_b_small_and_blurry_keeps_planned_size(monkeypatch):
    plan = text_align.ReadPlan("capture", size="sxga", line_px=12, width=800, blur=1.5)
    _, ctx = _route(plan, monkeypatch)
    assert ctx.prewarmer.calls[0][1]["capture_size"] == "sxga"


def test_one_shot_blurry_says_hold_still_before_capturing(monkeypatch):
    rig = _Rig(monkeypatch, size="svga")
    rig.session.params["capture_settle_s"] = 0.05
    _plan_with_text(monkeypatch)
    rig.ws.feed_frame(_jpeg(800, 600))
    asyncio.run(ocr_live.run(rig.ws, rig.session))
    kinds = [f["t"] for f in rig.sent]
    assert kinds.index("cue") < kinds.index("capture")
