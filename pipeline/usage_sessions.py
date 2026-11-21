"""Phiên thống kê (Task 12): kết nối kính, chế độ cảnh báo vật cản, OCR live, nghe nhạc, dẫn đường.

Mỗi phiên phát `session_started` -> `session_heartbeat` mỗi `USAGE_HEARTBEAT_S` (thread nền của `services/usage.py`)
-> `session_ended` đúng một lần. AB lấy thời lượng từ mốc kết thúc, hoặc heartbeat cuối khi VH chết giữa chừng.

- `connection`: một phiên mỗi socket `/v2/session` (`live/device_session.py`), mang `fw` + chẩn đoán từ `hello`.
- `alert_mode`, `music`, `navigation`: tối đa một phiên mở mỗi kính; mở lại khi đang mở = vẫn phiên đó (nav dựng lại
  sau OCR live, đổi bài hát). Cảnh báo (`alert_raised`) chỉ ghi khi đã thực sự phát tới kính.
- `ocr_live`: một phiên mỗi lần chạy OCR live (`lanes/ocr_live.run`, trừ đọc tiền lại), `align_ms` khi kết thúc.

Không ghi gì ngoài mã phiên, loại, lý do kết thúc, mức cảnh báo và số chẩn đoán của kính. Lỗi ở đây chỉ ghi log.
"""

from __future__ import annotations

import logging
import re
import threading
import time
from dataclasses import dataclass

import config
from services import usage

logger = logging.getLogger("blind_assist.usage")

# Loại chỉ có một phiên mở mỗi kính.
_SINGLE = frozenset({"alert_mode", "music", "navigation"})
# Kết nối kính kết thúc -> các phiên khác của kính đó cũng kết thúc. Bị thay (kính nối lại ngay) thì nhạc / dẫn đường
# trên điện thoại vẫn chạy nên giữ; cảnh báo vật cản thì không (phiên kính mới chưa bật lại).
_MEDIA = frozenset({"music", "navigation"})

_CODE = re.compile(r"^[A-Za-z0-9_.:-]{1,64}$")
_VERSION = re.compile(r"^[A-Za-z0-9_.+-]{1,40}$")
# Trường chẩn đoán `hello` (firmware mới) -> khoảng hợp lệ của AB; sai kiểu / ngoài khoảng thì bỏ trường đó.
_INT_FIELDS = {"uptime_ms": (0, 2**53), "wifi_drops": (0, 1_000_000), "last_wifi_reason": (0, 1000), "rssi": (-127, 0)}


@dataclass
class _Session:
    session_id: str
    kind: str
    device: str
    firmware: str | None
    last_beat: float


_lock = threading.Lock()
_open: dict[str, _Session] = {}
_firmware: dict[str, str] = {}  # kính -> `fw` của kết nối gần nhất, gắn vào mọi phiên của kính đó


def firmware_version(fw: object) -> str | None:
    return fw if isinstance(fw, str) and _VERSION.match(fw) else None


def device_info(hello: dict) -> dict | None:
    """Chẩn đoán kính trong `hello` (không bắt buộc), đã lọc theo hợp đồng AB."""
    info: dict[str, object] = {}
    for key, (lo, hi) in _INT_FIELDS.items():
        value = hello.get(key)
        if isinstance(value, int) and not isinstance(value, bool) and lo <= value <= hi:
            info[key] = value
    reason = hello.get("reset_reason")
    if isinstance(reason, str) and _CODE.match(reason):
        info["reset_reason"] = reason
    return info or None


def open_session(
    kind: str, device: str, *, firmware: str | None = None, info: dict | None = None, now: float | None = None,
) -> str | None:
    """Mở phiên; trả `session_id`. Loại một-phiên-mỗi-kính đang mở thì trả phiên đang mở. Tắt thống kê -> None."""
    if not config.USAGE_ENABLED:
        return None
    try:
        now = time.monotonic() if now is None else now
        with _lock:
            if kind in _SINGLE:
                for sess in _open.values():
                    if sess.kind == kind and sess.device == device:
                        return sess.session_id
            if kind == "connection" and firmware:
                _firmware[device] = firmware
            fw = firmware or _firmware.get(device)
            sess = _Session(usage.uuid7(), kind, device, fw, now)
            _open[sess.session_id] = sess
        usage.emit("session_started", session_id=sess.session_id, kind=kind, device_id=device,
                   firmware_version=fw, device_info=info)
        return sess.session_id
    except Exception:  # noqa: BLE001
        logger.exception("usage: mở phiên %s lỗi", kind)
        return None


def close_session(session_id: str | None, end_reason: str, *, align_ms: int | None = None) -> None:
    """Đóng phiên (gọi lại = bỏ qua)."""
    if session_id is None:
        return
    with _lock:
        sess = _open.pop(session_id, None)
    if sess is None:
        return
    try:
        usage.emit("session_ended", session_id=sess.session_id, kind=sess.kind, end_reason=end_reason,
                   device_id=sess.device, firmware_version=sess.firmware, align_ms=align_ms)
    except Exception:  # noqa: BLE001
        logger.exception("usage: đóng phiên lỗi")


def current(device: str, kind: str) -> str | None:
    with _lock:
        return next((s.session_id for s in _open.values() if s.kind == kind and s.device == device), None)


def close_kind(device: str, kind: str, end_reason: str) -> None:
    """Đóng phiên `kind` đang mở của kính (nếu có)."""
    close_session(current(device, kind), end_reason)


def connection_ended(device: str, end_reason: str) -> None:
    """Kết nối kính kết thúc: đóng cảnh báo / OCR live của kính; nhạc và dẫn đường giữ khi chỉ bị thay phiên."""
    with _lock:
        ids = [s.session_id for s in _open.values()
               if s.device == device and s.kind != "connection" and not (end_reason == "replaced" and s.kind in _MEDIA)]
    for session_id in ids:
        close_session(session_id, end_reason)


def close_all(end_reason: str) -> None:
    """Tắt server: đóng mọi phiên còn mở."""
    with _lock:
        ids = list(_open)
    for session_id in ids:
        close_session(session_id, end_reason)


def alert(session_id: str | None, level: int) -> None:
    if session_id is None:
        return
    with _lock:
        sess = _open.get(session_id)
    if sess is not None:
        usage.emit("alert_raised", session_id=session_id, level=int(level), device_id=sess.device,
                   firmware_version=sess.firmware)


def alert_for(device: str, level: int) -> None:
    """Cảnh báo vật cản vừa phát tới kính `device` -> ghi vào phiên `alert_mode` đang mở của kính."""
    try:
        alert(current(device, "alert_mode"), level)
    except Exception:  # noqa: BLE001
        logger.exception("usage: ghi cảnh báo lỗi")


def heartbeat_all(now: float | None = None) -> None:
    """Mỗi phiên mở phát `session_heartbeat` mỗi `USAGE_HEARTBEAT_S` (gọi từ tick của thread nền)."""
    now = time.monotonic() if now is None else now
    every = config.USAGE_HEARTBEAT_S
    with _lock:
        due = [s for s in _open.values() if now - s.last_beat >= every]
        for sess in due:
            sess.last_beat = now
    for sess in due:
        usage.emit("session_heartbeat", session_id=sess.session_id, kind=sess.kind, device_id=sess.device,
                   firmware_version=sess.firmware)


def reset_for_tests() -> None:
    with _lock:
        _open.clear()
        _firmware.clear()
    if heartbeat_all not in usage._ticks:
        usage.on_tick(heartbeat_all)


usage.on_tick(heartbeat_all)
