"""Nối một lượt v2 với bộ ghép thống kê (`pipeline/usage_tasks.py`): giữ chỗ, mở lượt, kết quả, phát xong.

Gọi từ `pipeline/turn.py` (giữ chỗ lúc nhả nút, MỞ sau `final_router.route` khi đã biết chức năng thật theo
`Reply.intent`; tín hiệu phát trong làn được giữ tạm rồi gửi kèm), từ phiên WS (`played`, bị ngắt, đóng phiên),
từ đường HTTP (phát hết byte cuối) và từ làn D (`pending_action`, `handlers/action_flow.py`, `handlers/phone.py`:
cửa sổ huỷ, kết quả thật của điện thoại). Mọi hàm ở đây nuốt lỗi của chính nó: thống kê hỏng không được làm
hỏng lượt.
"""

from __future__ import annotations

import logging
import re
import threading
import time
from dataclasses import dataclass
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
# Intent cố ý KHÔNG đo theo lượt (test phủ: mọi intent của `schemas.Intent` + `LANE` có feature hoặc nằm ở đây).
NOT_MEASURED: dict[str, str] = {
    "alert_on": "đo theo phiên alert_mode",
    "alert_off": "đo theo phiên alert_mode",
    "unknown": "không nghe ra lời: chỉ hỏi lại, không phải một lần dùng chức năng",
}
# Mỗi lần kích hoạt là một việc riêng, không bao giờ ghép vào tác vụ trước (spec §6.12).
INDEPENDENT = frozenset({"emergency_call"})
# Câu ĐẦU của lời đáp làn A có ảnh nói "không thấy / không đọc được" -> lượt `failed` (câu đã gợi ý chụp lại nên tác
# vụ còn mở). Tìm đồ: "không thấy <vật>" là không tìm ra. Hỏi ảnh / mô tả / vật cản: chỉ khi ảnh không đủ để trả lời
# ("không thấy rõ", "không đọc được"); "không thấy vật cản / không thấy ai" là một câu trả lời thật, vẫn `success`.
_NOT_FOUND = re.compile(r"^(?:tôi |mình )?(?:không|chưa) (?:nhìn )?thấy")
_UNREADABLE = re.compile(r"^(?:tôi |mình )?(?:không|chưa) (?:thể )?(?:nhìn|thấy|đọc|xác định)(?: được)?(?: rõ| được)")
_JUDGED = {"find": (_NOT_FOUND, "NOT_FOUND"), "ask": (_UNREADABLE, "UNREADABLE"),
           "space": (_UNREADABLE, "UNREADABLE"), "hazard": (_UNREADABLE, "UNREADABLE")}
JUDGED_INTENTS = frozenset(_JUDGED)
# Lượt chờ `played` tối đa trước khi biết độ dài audio (chặn trên; `audio_sent` thu hẹp lại).
_INITIAL_PLAYBACK_WAIT_S = 180.0


def feature_for(intent: str) -> str | None:
    """`feature` thống kê của một intent, hoặc None khi intent không đo theo lượt."""
    return FEATURES.get(intent or "")


def wall(mono: float) -> datetime:
    """Đổi mốc `time.monotonic()` thành giờ UTC thật."""
    return datetime.now(UTC) - timedelta(seconds=time.monotonic() - mono)


@dataclass
class _Draft:
    device: str
    released: float
    entry: str
    firmware: str | None


# Lượt đã giữ chỗ (đầu lượt) nhưng chưa mở: chức năng chỉ biết sau khi route xong (`Reply.intent`).
_drafts: dict[str, _Draft] = {}
_drafts_lock = threading.Lock()


def begin(
    device: str,
    released: float,
    *,
    transport: str,
    glasses: dict | None = None,
    has_speech: bool = True,
) -> str | None:
    """Giữ chỗ một lượt lúc nhả nút; trả `attempt_id` (đặt vào `usage.current_attempt`) hoặc None.

    Chưa mở lượt ở đây: lượt trả lời "có/không/tên người" phải ghép vào tác vụ gọi/nhắn đang hỏi dở, mà chức
    năng thật chỉ rõ sau `final_router.route` (vd. "có" -> `contact_call`). Tín hiệu phát trong lúc route được
    `usage` giữ tạm và gửi khi `routed()` mở lượt.
    """
    try:
        hold = _hold_ms(glasses)
        if not has_speech and hold is not None and hold < config.USAGE_MIN_PRESS_MS:
            return None  # nhấn chạm, không lời: không phải một lần dùng
        entry = {"ws": "v2_session", "post": "v2_turn", "process": "process"}.get(transport, "v2_session")
        firmware = (glasses or {}).get("fw")
        attempt_id = usage.uuid7()
        with _drafts_lock:
            _drafts[attempt_id] = _Draft(device, released, entry, firmware if isinstance(firmware, str) else None)
        usage.hold(attempt_id)
        return attempt_id
    except Exception:  # noqa: BLE001
        logger.exception("usage: giữ chỗ lượt lỗi")
        return None


def _open(attempt_id: str | None, intent: str) -> bool:
    """Mở lượt đã giữ chỗ với chức năng của `intent`; intent không đo -> bỏ lượt (và tín hiệu đã giữ)."""
    if attempt_id is None:
        return False
    with _drafts_lock:
        draft = _drafts.pop(attempt_id, None)
    if draft is None:
        return False  # đã mở / đã bỏ
    feature = feature_for(intent)
    if feature is None:
        usage.release(attempt_id, keep=False)
        return False
    tracker.start(
        draft.device, feature, now=draft.released, independent=intent in INDEPENDENT, entry=draft.entry,
        firmware_version=draft.firmware, attempt_id=attempt_id, occurred_at=wall(draft.released).isoformat(),
    )
    tracker.require(attempt_id, "playback", deadline=draft.released + _INITIAL_PLAYBACK_WAIT_S)
    usage.release(attempt_id, keep=True)
    return True


def routed(attempt_id: str | None, reply: Reply) -> None:
    """Router đã trả `Reply`: mở lượt theo `reply.intent`, ghi kết quả dự kiến.

    OCR live chờ thêm phiên live đọc xong; lệnh điện thoại (`reply.awaits_action`) chờ điện thoại báo kết quả
    thật. `reply.counted=False` (câu "có/không" lạc đề) -> không phải một lượt.
    """
    if attempt_id is None:
        return
    try:
        if not _open(attempt_id, reply.intent if reply.counted else ""):
            return
        if reply.live == "ocr":
            tracker.require(attempt_id, "live")
        if reply.awaits_action:
            tracker.require(attempt_id, "action", deadline=time.monotonic() + config.USAGE_ACTION_WAIT_S)
        for reason in reply.reprompt:
            usage.signal("reprompt", reason, attempt_id=attempt_id)
        tracker.outcome(
            attempt_id, reply.outcome, now=time.monotonic(),
            error_code=reply.error_code, keeps_open=reply.keeps_open or bool(reply.reprompt),
        )
    except Exception:  # noqa: BLE001
        logger.exception("usage: ghi kết quả lỗi")


def judge(intent: str, first_sentence: str) -> tuple[str, str] | None:
    """(`failed`, mã) khi câu đầu của lời đáp làn A có ảnh nói không thấy / không đọc được; còn lại None."""
    rule = _JUDGED.get(intent)
    if rule is None:
        return None
    head = re.split(r"[.!?…\n]", (first_sentence or "").strip().lower(), maxsplit=1)[0]
    return ("failed", rule[1]) if rule[0].search(head) else None


def verdict(attempt_id: str | None, status: str, error_code: str | None = None, *, keeps_open: bool = False) -> None:
    """Sửa kết quả dự kiến khi chỉ biết lúc lời đáp đang sinh (sau `routed`); lượt đã chốt thì bỏ qua."""
    if attempt_id is None:
        return
    try:
        tracker.outcome(attempt_id, status, now=time.monotonic(), error_code=error_code, keeps_open=keeps_open)
    except Exception:  # noqa: BLE001
        logger.exception("usage: sửa kết quả lỗi")


def aborted(attempt_id: str | None, intent: str, *, cancelled: bool) -> None:
    """Lượt hỏng trước khi có `Reply` (bị lượt mới/huỷ cắt, hoặc ngoại lệ): mở theo intent đã đoán rồi chốt."""
    if attempt_id is None:
        return
    try:
        if not _open(attempt_id, intent):
            return
        if cancelled:
            interrupted(attempt_id)
        else:
            failed(attempt_id, "TURN_ERROR")
    except Exception:  # noqa: BLE001
        logger.exception("usage: chốt lượt hỏng lỗi")


# ── làn D: lệnh điện thoại ───────────────────────────────────────────────────

# Lý do huỷ lệnh chờ (`pending_action.cancel`) -> cách đếm. Người dùng tự huỷ trong cửa sổ (bấm nút, nói lệnh
# khác, khung `cancel`) là huỷ thật; bị lệnh mới thay hoặc bị ngắt (rớt mạng, cuộc gọi đến) không phải lỗi.
_USER_CANCEL = frozenset({"lượt mới", "kính huỷ"})
_SUPERSEDED = frozenset({"lệnh mới thay thế"})
# Mã lỗi điện thoại mà làn D hỏi lại ngay (`lanes/phone._SAY_AGAIN`: nói lại tên bài / điểm đến) -> tác vụ còn mở.
_SAY_AGAIN_CODES = {"SONG_NOT_FOUND": "missing_slot", "INVALID_DESTINATION": "missing_slot"}


def cancel_window(attempt_id: str | None) -> None:
    """Câu đọc lại đã tới kính, cửa sổ huỷ bắt đầu đếm."""
    if attempt_id is not None:
        usage.signal("cancel_window_open", attempt_id=attempt_id)


def action_cancelled(attempt_id: str | None, reason: str) -> None:
    """Lệnh chờ bị huỷ trước khi chạy."""
    if attempt_id is None:
        return
    try:
        now = time.monotonic()
        if reason in _SUPERSEDED:
            tracker.finish(attempt_id, "cancelled", now=now, error_code="superseded")
        elif reason in _USER_CANCEL:
            usage.signal("cancelled_in_window", attempt_id=attempt_id)
            tracker.finish(attempt_id, "cancelled", now=now)
        else:
            usage.signal("interrupted", attempt_id=attempt_id)
            tracker.finish(attempt_id, "cancelled", now=now, error_code="interrupted")
    except Exception:  # noqa: BLE001
        logger.exception("usage: huỷ lệnh chờ lỗi")


def action_sent(request_id: str | None = None, wait_s: float | None = None) -> str | None:
    """Lệnh đã tới AB: lượt hiện tại chờ thêm kết quả thật từ điện thoại. Trả `attempt_id` để báo kết quả sau
    (callback chạy ở luồng khác, không có ngữ cảnh)."""
    attempt_id = usage.current_attempt.get()
    if attempt_id is None:
        return None
    try:
        deadline = time.monotonic() + (wait_s if wait_s is not None else config.USAGE_ACTION_WAIT_S)
        tracker.require(attempt_id, "action", deadline=deadline)
        if request_id:
            tracker.bind_request(attempt_id, request_id)
    except Exception:  # noqa: BLE001
        logger.exception("usage: ghi lệnh điện thoại lỗi")
    return attempt_id


def bind_request(attempt_id: str | None, request_id: str) -> None:
    """Gắn `request_id` AB vào lượt (đi trong `attempt_finished` để AB đối chiếu)."""
    if attempt_id is not None:
        try:
            tracker.bind_request(attempt_id, request_id)
        except Exception:  # noqa: BLE001
            logger.exception("usage: gắn request_id lỗi")


def action_done(
    attempt_id: str | None, status: str, error_code: str | None = None, *, reprompt: str | None = None,
) -> None:
    """Điện thoại/AB đã báo kết quả của lệnh (gọi một lần; lần sau bị bỏ qua). `reprompt`: kết quả là một câu
    hỏi lại (vd. trùng tên người thân) -> tác vụ còn mở."""
    if attempt_id is None:
        return
    try:
        if not tracker.waiting_for(attempt_id, "action"):
            tracker.require(attempt_id, "action")  # lệnh hỏng trước khi kịp gửi (không qua `action_sent`)
        if reprompt:
            usage.signal("reprompt", reprompt, attempt_id=attempt_id)
        tracker.confirm(
            attempt_id, "action", now=time.monotonic(), status=status, error_code=error_code,
            keeps_open=bool(reprompt),
        )
    except Exception:  # noqa: BLE001
        logger.exception("usage: ghi kết quả điện thoại lỗi")


def phone_result(attempt_id: str | None, data: dict | None) -> None:
    """Kết quả `GET /requests/{id}` hoặc callback AB -> trạng thái lượt. `None` = hết giờ chờ."""
    if data is None:
        action_done(attempt_id, "technical_error", "RESULT_TIMEOUT")
        return
    state = data.get("request_state")
    if state == "succeeded":
        action_done(attempt_id, "success")
    elif state == "timed_out":
        action_done(attempt_id, "technical_error", "RESULT_TIMEOUT")
    else:
        code = str((data.get("error") or {}).get("code") or "") or "PHONE_FAILED"
        action_done(attempt_id, "failed", code, reprompt=_SAY_AGAIN_CODES.get(code))


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
        if not tracker.active(attempt_id):
            return  # lượt không đo (chỉ giữ chỗ) hoặc đã chốt
        if tracker.waiting_for(attempt_id, "action"):
            # Lệnh điện thoại đã/đang đi: bấm nút chỉ cắt câu đang đọc. Huỷ lệnh chờ (nếu còn) do
            # `action_cancelled` ghi; lệnh đã chạy thì lượt vẫn chờ kết quả thật.
            tracker.confirm(attempt_id, "playback", now=now, playback="interrupted")
            return
        if tracker.had_audio(attempt_id):
            tracker.finish(attempt_id, "completed_early", now=now)
        else:
            usage.emit("attempt_signal", attempt_id=attempt_id, signal="interrupted")
            tracker.finish(attempt_id, "cancelled", now=now, error_code="interrupted")
    except Exception:  # noqa: BLE001
        logger.exception("usage: ngắt lượt lỗi")


def live_done(
    attempt_id: str | None, *, status: str, error_code: str | None = None, align_ms: int | None = None,
    keeps_open: bool = False,
) -> None:
    """Phiên OCR live của lượt đã đọc xong (hoặc không có chữ / bị huỷ). `keeps_open`: đọc tiền lại vẫn chưa chắc."""
    if attempt_id is None:
        return
    try:
        if align_ms is not None:
            tracker.mark(attempt_id, align_ms=align_ms)
        tracker.confirm(
            attempt_id, "live", now=time.monotonic(), status=status, error_code=error_code, keeps_open=keeps_open,
        )
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


def reset_for_tests() -> None:
    with _drafts_lock:
        _drafts.clear()


def _hold_ms(glasses: dict | None) -> float | None:
    if not glasses:
        return None
    press, release = glasses.get("t_press_ms"), glasses.get("t_release_ms")
    if isinstance(press, int | float) and isinstance(release, int | float) and release >= press:
        return release - press
    return None
