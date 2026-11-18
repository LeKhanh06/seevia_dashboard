"""Ghi sự kiện thống kê sử dụng và gửi theo lô sang AB (`POST /api/v1/usage/events`).

Luồng lượt KHÔNG bao giờ chờ ở đây: `emit()` chỉ lọc trường, gắn mã/giờ rồi bỏ vào hàng đợi trong bộ nhớ.
Một thread nền ("usage-io") ghi hàng đợi xuống spool JSONL trên đĩa (AB tắt hay VH khởi động lại vẫn không
mất), rồi gửi lô ≤ `USAGE_BATCH` sự kiện mỗi `USAGE_FLUSH_S` giây. Gửi lại giữ nguyên `event_id`, AB chặn trùng.

Chỉ trường vận hành trong `ALLOWED_FIELDS` được giữ (khớp `app/schemas/usage.py` của AB). Khoá lạ bị bỏ và
cảnh báo một lần mỗi khoá — câu nói, chữ OCR, số tiền, tên người, số điện thoại, nội dung tin nhắn, điểm đến,
tên bài hát không có đường vào sự kiện. Token không bao giờ ghi log.
"""

from __future__ import annotations

import contextvars
import json
import logging
import os
import queue
import secrets
import subprocess
import threading
import time
import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

import config
from pipeline import device_ctx

logger = logging.getLogger("blind_assist.usage")

_COMMON = frozenset({"firmware_version"})
ALLOWED_FIELDS: dict[str, frozenset[str]] = {
    "attempt_started": frozenset({"attempt_id", "task_id", "feature", "entry"}),
    "attempt_signal": frozenset({"attempt_id", "signal", "reason_code"}),
    "attempt_marks": frozenset({"attempt_id", "first_audio_at", "processing_ms", "align_ms"}),
    "attempt_finished": frozenset(
        {"attempt_id", "task_id", "status", "error_code", "playback", "request_id"}
    ),
    "task_finished": frozenset({"task_id", "feature", "status", "attempts"}),
    "session_started": frozenset({"session_id", "kind", "device_info"}),
    "session_heartbeat": frozenset({"session_id", "kind"}),
    "session_ended": frozenset({"session_id", "kind", "end_reason", "align_ms"}),
    "alert_raised": frozenset({"session_id", "level"}),
}

# Lượt đang chạy trong ngữ cảnh hiện tại: `signal()` ở sâu trong làn/handler không cần truyền tay.
current_attempt: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "usage_attempt", default=None
)

_queue: queue.Queue[dict[str, Any]] = queue.Queue()
_warned: set[str] = set()
_state_lock = threading.Lock()
_io_lock = threading.Lock()
_thread: threading.Thread | None = None
_stop = threading.Event()
_ticks: list[Callable[[float], None]] = []
_version: str | None = None
_backoff_until = 0.0
_backoff_s = 0.0


def uuid7() -> str:
    """UUID v7 (RFC 9562): 48 bit mili-giây + ngẫu nhiên — tăng dần theo thời gian, hợp index."""
    ms = int(time.time() * 1000) & ((1 << 48) - 1)
    rand_a = secrets.randbits(12)
    rand_b = secrets.randbits(62)
    value = (ms << 80) | (0x7 << 76) | (rand_a << 64) | (0b10 << 62) | rand_b
    return str(uuid.UUID(int=value))


def now_iso() -> str:
    """Giờ UTC dạng ISO có múi giờ (AB đòi `AwareDatetime`)."""
    return datetime.now(UTC).isoformat()


def component_version() -> str:
    """`<git sha>+stt_dual=..,hazard=..,money=..` — tính một lần (so lỗi/độ trễ giữa các phiên bản)."""
    global _version
    if _version is None:
        try:
            sha = subprocess.run(
                ["git", "rev-parse", "--short", "HEAD"],
                cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                capture_output=True, text=True, timeout=2, check=False,
            ).stdout.strip() or "unknown"
        except (OSError, subprocess.SubprocessError):
            sha = "unknown"
        flags = (
            f"stt_dual={int(bool(getattr(config, 'STT_DUAL', False)))},"
            f"hazard={getattr(config, 'HAZARD_LOGIC', 'area')},"
            f"money={getattr(config, 'MONEY_MODE', 'choice')}"
        )
        _version = f"{sha}+{flags}"
    return _version


def emit(etype: str, **fields: Any) -> str | None:
    """Ghi một sự kiện (không chặn). Trả `event_id`, hoặc None khi tắt / loại lạ."""
    if not config.USAGE_ENABLED:
        return None
    allowed = ALLOWED_FIELDS.get(etype)
    if allowed is None:
        _warn_once(f"type:{etype}")
        return None
    event: dict[str, Any] = {
        "event_id": uuid7(),
        "type": etype,
        "occurred_at": fields.pop("occurred_at", None) or now_iso(),
        "device_id": fields.pop("device_id", None) or device_ctx.current.get(),
        "component_version": component_version(),
    }
    for key, value in fields.items():
        if key not in allowed and key not in _COMMON:
            _warn_once(f"{etype}.{key}")
            continue
        if value is not None:
            event[key] = value
    _queue.put_nowait(event)
    return event["event_id"]


class _Held:
    """Tín hiệu của một lượt mới GIỮ chỗ (chưa biết chức năng): giữ tạm tới khi lượt mở hoặc bị bỏ."""

    __slots__ = ("attempt_id", "items", "keep")

    def __init__(self, attempt_id: str) -> None:
        self.attempt_id = attempt_id
        self.items: list[tuple[str, str | None]] | None = []
        self.keep = False


_held: contextvars.ContextVar[_Held | None] = contextvars.ContextVar("usage_held", default=None)
_held_lock = threading.Lock()


def hold(attempt_id: str) -> None:
    """Giữ chỗ lượt `attempt_id` trong ngữ cảnh hiện tại; `signal()` từ giờ được giữ tạm tới `release()`."""
    current_attempt.set(attempt_id)
    _held.set(_Held(attempt_id))


def release(attempt_id: str, *, keep: bool) -> None:
    """Lượt đã mở (`keep`: gửi các tín hiệu đã giữ, từ đây gửi thẳng) hoặc bị bỏ (vứt, tín hiệu sau cũng vứt)."""
    held = _held.get()
    if held is None or held.attempt_id != attempt_id:
        return
    with _held_lock:
        items, held.items, held.keep = held.items, None, keep
    for name, reason in (items or []) if keep else []:
        emit("attempt_signal", attempt_id=attempt_id, signal=name, reason_code=reason)


def signal(name: str, reason_code: str | None = None, *, attempt_id: str | None = None) -> None:
    """Đếm một tín hiệu cho lượt (mặc định: lượt đang chạy trong ngữ cảnh); không có lượt thì bỏ qua."""
    attempt_id = attempt_id or current_attempt.get()
    if attempt_id is None:
        return
    held = _held.get()
    if held is not None and held.attempt_id == attempt_id:
        with _held_lock:
            if held.items is not None:
                held.items.append((name, reason_code))
                return
            if not held.keep:
                return  # lượt bị bỏ (không đo): tín hiệu không có lượt để gắn
    emit("attempt_signal", attempt_id=attempt_id, signal=name, reason_code=reason_code)


def on_tick(fn: Callable[[float], None]) -> None:
    """Đăng ký hàm chạy mỗi vòng thread nền (bộ ghép tác vụ dùng để chốt hết hạn)."""
    _ticks.append(fn)


def _warn_once(key: str) -> None:
    with _state_lock:
        if key in _warned:
            return
        _warned.add(key)
    logger.warning("usage: bỏ trường/loại không được phép %s", key)


# ── spool ────────────────────────────────────────────────────────────────────


def _spool_dir() -> str:
    path = config.USAGE_SPOOL_DIR
    os.makedirs(path, exist_ok=True)
    return path


def _cursor_path() -> str:
    return os.path.join(_spool_dir(), "cursor.json")


def _read_cursor() -> tuple[str, int]:
    try:
        with open(_cursor_path(), encoding="utf-8") as fh:
            data = json.load(fh)
        return str(data["file"]), int(data["offset"])
    except (OSError, ValueError, KeyError, TypeError):
        return "", 0


def _write_cursor(name: str, offset: int) -> None:
    tmp = _cursor_path() + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump({"file": name, "offset": offset}, fh)
    os.replace(tmp, _cursor_path())


def _spool_files() -> list[str]:
    return sorted(n for n in os.listdir(_spool_dir()) if n.startswith("spool-") and n.endswith(".jsonl"))


def _drain_to_disk() -> int:
    """Ghi mọi sự kiện đang chờ xuống spool hôm nay; trả số sự kiện đã ghi."""
    events: list[dict[str, Any]] = []
    while True:
        try:
            events.append(_queue.get_nowait())
        except queue.Empty:
            break
    if not events:
        return 0
    name = f"spool-{datetime.now(UTC):%Y%m%d}.jsonl"
    path = os.path.join(_spool_dir(), name)
    with open(path, "ab") as fh:
        # Dòng cụt của lần chết trước (không có '\n' cuối) phải đứng riêng, không dính vào dòng mới.
        if fh.tell() > 0:
            with open(path, "rb") as check:
                check.seek(-1, os.SEEK_END)
                if check.read(1) != b"\n":
                    fh.write(b"\n")
        fh.write(b"".join(json.dumps(e, ensure_ascii=False).encode() + b"\n" for e in events))
    return len(events)


def _next_batch() -> tuple[list[dict[str, Any]], str, int]:
    """Đọc ≤ USAGE_BATCH sự kiện từ con trỏ; trả (lô, file, offset sau lô). Dòng hỏng bị bỏ qua."""
    cur_file, cur_off = _read_cursor()
    batch: list[dict[str, Any]] = []
    end_file, end_off = cur_file, cur_off
    for name in _spool_files():
        if cur_file and name < cur_file:
            continue
        offset = cur_off if name == cur_file else 0
        with open(os.path.join(_spool_dir(), name), "rb") as fh:
            fh.seek(offset)
            while len(batch) < config.USAGE_BATCH:
                line = fh.readline()
                if not line or not line.endswith(b"\n"):
                    break  # hết file, hoặc dòng đang ghi dở
                offset += len(line)
                end_file, end_off = name, offset
                try:
                    batch.append(json.loads(line))
                except ValueError:
                    logger.warning("usage: bỏ một dòng spool hỏng ở %s", name)
        if len(batch) >= config.USAGE_BATCH:
            break
    return batch, end_file, end_off


def _cleanup(sent_file: str) -> None:
    """Xoá file spool đã gửi hết và cũ hơn 7 ngày."""
    cutoff = f"spool-{datetime.now(UTC) - timedelta(days=7):%Y%m%d}.jsonl"
    for name in _spool_files():
        if name < sent_file and name < cutoff:
            try:
                os.remove(os.path.join(_spool_dir(), name))
            except OSError:
                pass


def _post(batch: list[dict[str, Any]]) -> int:
    """Gửi một lô; trả mã HTTP (0 = lỗi mạng)."""
    try:
        response = httpx.post(
            config.USAGE_ENDPOINT,
            json={"events": batch},
            headers={"Authorization": f"Bearer {config.USAGE_API_TOKEN}"},
            timeout=10.0,
        )
    except httpx.HTTPError as exc:
        logger.warning("usage: gửi lô lỗi mạng (%s)", type(exc).__name__)
        return 0
    if response.status_code == 200:
        try:
            rejected = response.json().get("rejected") or []
        except ValueError:
            rejected = []
        for item in rejected[:20]:
            logger.warning("usage: AB từ chối %s (%s)", item.get("event_id"), item.get("code"))
    return response.status_code


def flush_now() -> int:
    """Ghi hàng đợi xuống đĩa rồi gửi hết các lô có thể; trả số sự kiện AB đã nhận lô (2xx)."""
    global _backoff_until, _backoff_s
    with _io_lock:
        _drain_to_disk()
        if not config.USAGE_API_TOKEN or time.monotonic() < _backoff_until:
            return 0
        sent = 0
        while True:
            batch, end_file, end_off = _next_batch()
            if not batch:
                if end_file:
                    _write_cursor(end_file, end_off)  # chỉ toàn dòng hỏng: vẫn tiến con trỏ
                return sent
            code = _post(batch)
            if 200 <= code < 300:
                sent += len(batch)
            elif code in (0, 408, 429) or code >= 500:
                _backoff_s = min(300.0, max(5.0, _backoff_s * 2))
                _backoff_until = time.monotonic() + _backoff_s
                return sent
            else:
                # 4xx còn lại (sai token, lô sai dạng): gửi lại cũng hỏng như vậy — ghi log và đi tiếp.
                logger.error("usage: AB trả %s cho lô %d sự kiện, bỏ qua lô", code, len(batch))
            _backoff_s = 0.0
            _write_cursor(end_file, end_off)
            _cleanup(end_file)


def _loop() -> None:
    next_send = 0.0
    while not _stop.wait(1.0):
        now = time.monotonic()
        for fn in list(_ticks):
            try:
                fn(now)
            except Exception:  # noqa: BLE001 -- một tick hỏng không được giết thread gửi
                logger.exception("usage: tick lỗi")
        try:
            if now >= next_send:
                flush_now()
                next_send = now + config.USAGE_FLUSH_S
            else:
                with _io_lock:
                    _drain_to_disk()
        except Exception:  # noqa: BLE001 -- đĩa đầy / quyền: thử lại vòng sau, không làm chết server
            logger.exception("usage: ghi/gửi lỗi")


def start_sender() -> None:
    """Bật thread nền (gọi lúc server khởi động)."""
    global _thread
    if not config.USAGE_ENABLED or (_thread is not None and _thread.is_alive()):
        return
    _stop.clear()
    _thread = threading.Thread(target=_loop, name="usage-io", daemon=True)
    _thread.start()


def stop_sender(timeout: float = 5.0) -> None:
    """Dừng thread nền và gửi nốt (gọi lúc server tắt)."""
    global _thread
    _stop.set()
    if _thread is not None:
        _thread.join(timeout)
        _thread = None
    try:
        flush_now()
    except Exception:  # noqa: BLE001
        logger.exception("usage: gửi nốt lúc tắt lỗi")


def reset_for_tests() -> None:
    """Xoá trạng thái trong bộ nhớ (test)."""
    global _backoff_until, _backoff_s, _version
    while not _queue.empty():
        _queue.get_nowait()
    _warned.clear()
    _ticks.clear()
    _backoff_until = _backoff_s = 0.0
    _version = None
    current_attempt.set(None)  # lượt giữ chỗ của test trước không rò sang test sau (cùng ngữ cảnh pytest)
    _held.set(None)
