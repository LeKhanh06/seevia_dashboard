"""Nối một lượt v2 với bộ ghép thống kê (`pipeline/usage_tasks.py`): mở lượt, kết quả dự kiến, phát xong.

Gọi từ `pipeline/turn.py` (mở lượt ngay khi intent cuối đã chốt, TRƯỚC `final_router.route` để tín hiệu trong làn
— ảnh bị từ chối, cần tra web — có ngữ cảnh), từ phiên WS (`played`, bị ngắt, đóng phiên) và từ đường HTTP
(phát hết byte cuối). Mọi hàm ở đây nuốt lỗi của chính nó: thống kê hỏng không được làm hỏng lượt.
"""

from __future__ import annotations

import logging
import time
from datetime import UTC, datetime, timedelta

import config
from pipeline.reply import Reply
from pipeline.usage_tasks import tracker
from services import usage

logger = logging.getLogger("blind_assist.usage")

# intent (schemas.py + LANE của intent_early) -> `feature` của sự kiện. Intent không có ở đây không tạo lượt
# (vd. alert_on/off: đo theo phiên `alert_mode`; no_speech: chỉ bấm nút).
FEATURES: dict[str, str] = {
    "ocr": "ocr",
    "money": "money",
    "find": "find",
    "space": "space",
    "ask": "ask",
    "hazard": "hazard",
    "chat": "chat",
    "contact_call": "call",
    "message_send": "message_send",
    "message_read": "message_read",
    "emergency_call": "emergency_call",
    "music_play": "music",
    "music_stop": "music",
    "music_volume": "music",
    "nav_start": "navigation",
    "nav_stop": "navigation",
    "ride_quote": "ride",
    "ride_confirm": "ride",
}
# Mỗi lần kích hoạt là một việc riêng, không bao giờ ghép vào tác vụ trước (spec §6.12).
INDEPENDENT = frozenset({"emergency_call"})
# Lượt chờ `played` tối đa trước khi biết độ dài audio (chặn trên; `audio_sent` thu hẹp lại).
_INITIAL_PLAYBACK_WAIT_S = 180.0


def feature_for(intent: str) -> str | None:
    """`feature` thống kê của một intent, hoặc None khi intent không đo theo lượt."""
    return FEATURES.get(intent or "")


def wall(mono: float) -> datetime:
    """Đổi mốc `time.monotonic()` thành giờ UTC thật."""
    return datetime.now(UTC) - timedelta(seconds=time.monotonic() - mono)


def begin(
    device: str,
    intent: str,
    released: float,
    *,
    transport: str,
    glasses: dict | None = None,
    has_speech: bool = True,
) -> str | None:
    """Mở lượt thống kê; trả `attempt_id` (đặt luôn vào `usage.current_attempt`) hoặc None."""
    try:
        feature = feature_for(intent)
        if feature is None:
            return None
        hold = _hold_ms(glasses)
        if not has_speech and hold is not None and hold < config.USAGE_MIN_PRESS_MS:
            return None  # nhấn chạm, không lời: không phải một lần dùng
        entry = {"ws": "v2_session", "post": "v2_turn", "process": "process"}.get(transport, "v2_session")
        firmware = (glasses or {}).get("fw")
        attempt_id, _ = tracker.start(
            device, feature, now=released, independent=intent in INDEPENDENT, entry=entry,
            firmware_version=firmware if isinstance(firmware, str) else None,
        )
        tracker.require(attempt_id, "playback", deadline=released + _INITIAL_PLAYBACK_WAIT_S)
        usage.current_attempt.set(attempt_id)
        return attempt_id
    except Exception:  # noqa: BLE001
        logger.exception("usage: mở lượt lỗi")
        return None


def routed(attempt_id: str | None, reply: Reply) -> None:
    """Router đã trả `Reply`: ghi kết quả dự kiến; lượt OCR live chờ thêm phiên live đọc xong."""
    if attempt_id is None:
        return
    try:
        if reply.live == "ocr":
            tracker.require(attempt_id, "live")
        tracker.outcome(
            attempt_id, reply.outcome, now=time.monotonic(),
            error_code=reply.error_code, keeps_open=reply.keeps_open,
        )
    except Exception:  # noqa: BLE001
        logger.exception("usage: ghi kết quả lỗi")


def audio_sent(attempt_id: str | None, first_chunk_at: float | None, audio_s: float) -> None:
    """Đã gửi hết audio của lời đáp (WS): hạn chờ `played` = độ dài audio + `USAGE_PLAYED_GRACE_S`."""
    if attempt_id is None:
        return
    tracker.require(
        attempt_id, "playback", deadline=time.monotonic() + audio_s + config.USAGE_PLAYED_GRACE_S
    )
    if first_chunk_at is not None:
        tracker.note_audio(attempt_id, first_chunk_at)


def played(attempt_id: str | None, first_chunk_at: float | None, marks: dict) -> None:
    """Kính báo `played`: ghi mốc tiếng đầu (giờ gửi khối đầu + độ trễ trong kính) rồi xác nhận phát xong."""
    if attempt_id is None:
        return
    try:
        if first_chunk_at is not None:
            fb, fa = marks.get("first_byte_ms"), marks.get("first_audio_ms")
            in_glasses = (fa - fb) / 1000 if isinstance(fa, int | float) and isinstance(fb, int | float) and fa >= fb else 0.0
            tracker.mark(attempt_id, first_audio_at=wall(first_chunk_at + in_glasses).isoformat())
        if marks.get("aborted"):
            interrupted(attempt_id)
            return
        tracker.confirm(attempt_id, "playback", now=time.monotonic())
    except Exception:  # noqa: BLE001
        logger.exception("usage: played lỗi")


def stream_done(attempt_id: str | None, first_chunk_at: float | None, *, ok: bool) -> None:
    """Đường HTTP: StreamingResponse đã giao byte cuối (`ok`) hoặc client đứt giữa chừng."""
    if attempt_id is None:
        return
    try:
        if first_chunk_at is not None:
            tracker.mark(attempt_id, first_audio_at=wall(first_chunk_at).isoformat())
        if ok:
            tracker.confirm(attempt_id, "playback", now=time.monotonic())
        else:
            tracker.finish(attempt_id, "technical_error", now=time.monotonic(), error_code="PLAYBACK_ABORTED")
    except Exception:  # noqa: BLE001
        logger.exception("usage: kết thúc stream lỗi")


def interrupted(attempt_id: str | None) -> None:
    """Lời đáp bị cắt (bấm nút / huỷ): đã có tiếng -> `completed_early`, chưa có tiếng -> bị ngắt, `cancelled`."""
    if attempt_id is None:
        return
    try:
        now = time.monotonic()
        if tracker.had_audio(attempt_id):
            tracker.finish(attempt_id, "completed_early", now=now)
        else:
            usage.emit("attempt_signal", attempt_id=attempt_id, signal="interrupted")
            tracker.finish(attempt_id, "cancelled", now=now, error_code="interrupted")
    except Exception:  # noqa: BLE001
        logger.exception("usage: ngắt lượt lỗi")


def live_done(attempt_id: str | None, *, status: str, error_code: str | None = None, align_ms: int | None = None) -> None:
    """Phiên OCR live của lượt đã đọc xong (hoặc không có chữ / bị huỷ)."""
    if attempt_id is None:
        return
    try:
        if align_ms is not None:
            tracker.mark(attempt_id, align_ms=align_ms)
        tracker.confirm(attempt_id, "live", now=time.monotonic(), status=status, error_code=error_code)
    except Exception:  # noqa: BLE001
        logger.exception("usage: live xong lỗi")


def failed(attempt_id: str | None, error_code: str) -> None:
    """Lượt hỏng vì lỗi của server (ngoại lệ trong router/TTS)."""
    if attempt_id is None:
        return
    try:
        tracker.finish(attempt_id, "technical_error", now=time.monotonic(), error_code=error_code)
    except Exception:  # noqa: BLE001
        logger.exception("usage: chốt lỗi kỹ thuật lỗi")


def _hold_ms(glasses: dict | None) -> float | None:
    if not glasses:
        return None
    press, release = glasses.get("t_press_ms"), glasses.get("t_release_ms")
    if isinstance(press, int | float) and isinstance(release, int | float) and release >= press:
        return release - press
    return None
