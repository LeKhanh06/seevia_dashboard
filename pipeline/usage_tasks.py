"""Ghép lượt (attempt) thành tác vụ (task) và chốt kết quả khi đủ xác nhận — cho thống kê sử dụng.

Quy tắc (tài liệu nghiệp vụ `SeeVia_Huong_dan_trien_khai_thong_ke.md` + spec §6):
- Lượt mới cùng kính, cùng chức năng GHÉP vào tác vụ đang mở khi tác vụ đó đang chờ người dùng (chụp lại, hỏi
  lại, chờ xác nhận) VÀ cách mốc gọi lượt trước ≤ `USAGE_MERGE_WINDOW_S` (60 s; 61 s là tác vụ mới). Không rõ quan
  hệ hoặc `independent=True` -> tác vụ mới. Tác vụ đang chờ mà bị bỏ qua (quá cửa sổ) -> `abandoned`.
- Một lượt chỉ chốt khi mọi phần đã `require()` được xác nhận: `playback` (kính báo `played`) và/hoặc `action`
  (điện thoại báo kết quả thật). Phần nào báo lỗi trước thì quyết định trạng thái. Chốt một lần duy nhất.
- Lượt chờ `played` quá hạn -> `unconfirmed`; tác vụ chờ người dùng quá `USAGE_ABANDON_S` -> `abandoned`.

Đồng hồ là `time.monotonic()` truyền vào (`now=`) để test được biên 60/61 s; giờ ghi trong sự kiện là giờ UTC thật
lúc `emit`. Trạng thái nằm trong bộ nhớ: VH chết thì AB tự chốt lượt treo là `UNCLOSED` sau 30 phút.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field

import config
from services import usage

logger = logging.getLogger("blind_assist.usage")

SUCCESSES = ("success", "completed_early")


@dataclass
class _Task:
    task_id: str
    device: str
    feature: str
    attempts: int = 0
    last_call: float = 0.0
    waiting_since: float | None = None  # đang chờ người dùng (giữ mở)
    busy: int = 0  # số lượt chưa chốt


@dataclass
class _Attempt:
    attempt_id: str
    task: _Task
    started: float
    pending: set[str] = field(default_factory=set)
    deadline: float | None = None
    status: str | None = None
    error_code: str | None = None
    keeps_open: bool = False
    playback: str | None = None
    request_id: str | None = None
    first_audio: bool = False
    audio: bool = False  # đã gửi khối audio đầu của lời đáp
    done: bool = False


class Tracker:
    """Bộ ghép theo kính; an toàn đa luồng."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._tasks: dict[tuple[str, str], _Task] = {}
        self._attempts: dict[str, _Attempt] = {}
        self._requests: dict[str, str] = {}

    # ── vòng đời lượt ───────────────────────────────────────────────────────

    def start(
        self,
        device: str,
        feature: str,
        *,
        now: float,
        independent: bool = False,
        entry: str = "v2_session",
        firmware_version: str | None = None,
    ) -> tuple[str, str]:
        """Mở một lượt; trả `(attempt_id, task_id)` và phát `attempt_started`."""
        abandoned: _Task | None = None
        with self._lock:
            key = (device, feature)
            task = self._tasks.get(key)
            mergeable = (
                task is not None
                and not independent
                and task.waiting_since is not None
                and task.busy == 0
                and now - task.last_call <= config.USAGE_MERGE_WINDOW_S
            )
            if not mergeable:
                if task is not None and task.waiting_since is not None and task.busy == 0:
                    abandoned = task  # người dùng không thử lại trong cửa sổ
                task = _Task(task_id=usage.uuid7(), device=device, feature=feature)
                self._tasks[key] = task
            assert task is not None
            task.attempts += 1
            task.last_call = now
            task.waiting_since = None
            task.busy += 1
            attempt = _Attempt(attempt_id=usage.uuid7(), task=task, started=now)
            self._attempts[attempt.attempt_id] = attempt
        if abandoned is not None:
            self._emit_task(abandoned, "abandoned")
        usage.emit(
            "attempt_started",
            attempt_id=attempt.attempt_id,
            task_id=task.task_id,
            feature=feature,
            entry=entry,
            device_id=device,
            firmware_version=firmware_version,
        )
        return attempt.attempt_id, task.task_id

    def require(self, attempt_id: str, *parts: str, deadline: float | None = None) -> None:
        """Lượt chỉ chốt sau khi các phần này được `confirm()` (`playback`, `action`)."""
        with self._lock:
            attempt = self._attempts.get(attempt_id)
            if attempt is None or attempt.done:
                return
            attempt.pending.update(parts)
            if deadline is not None and "playback" in parts:
                attempt.deadline = deadline

    def mark(self, attempt_id: str, **marks: object) -> None:
        """Ghi mốc thời gian (`first_audio_at`, `processing_ms`, `align_ms`)."""
        with self._lock:
            attempt = self._attempts.get(attempt_id)
            if attempt is None:
                return
            if "first_audio_at" in marks:
                if attempt.first_audio:
                    marks.pop("first_audio_at")
                attempt.first_audio = True
            device = attempt.task.device
        if marks:
            usage.emit("attempt_marks", attempt_id=attempt_id, device_id=device, **marks)

    def note_audio(self, attempt_id: str, _at: float) -> None:
        """Khối audio đầu của lời đáp đã đi (kính có thể đã bắt đầu phát)."""
        with self._lock:
            attempt = self._attempts.get(attempt_id)
            if attempt is not None:
                attempt.audio = True

    def had_audio(self, attempt_id: str) -> bool:
        """Người dùng đã bắt đầu nghe lời đáp chưa (đã gửi audio hoặc kính báo tiếng đầu)."""
        with self._lock:
            attempt = self._attempts.get(attempt_id)
            return bool(attempt and (attempt.audio or attempt.first_audio))

    def had_first_audio(self, attempt_id: str) -> bool:
        """Kính đã phát tiếng đầu của lượt này chưa (phân biệt `interrupted` / `completed_early`)."""
        with self._lock:
            attempt = self._attempts.get(attempt_id)
            return bool(attempt and attempt.first_audio)

    def outcome(
        self,
        attempt_id: str,
        status: str,
        *,
        now: float,
        error_code: str | None = None,
        keeps_open: bool = False,
        request_id: str | None = None,
    ) -> None:
        """Kết quả DỰ KIẾN của lượt (từ `Reply`); chốt ngay nếu không còn phần nào chờ."""
        with self._lock:
            attempt = self._attempts.get(attempt_id)
            if attempt is None or attempt.done:
                return
            if attempt.status is None or attempt.status in SUCCESSES:
                attempt.status, attempt.error_code = status, error_code
            attempt.keeps_open = attempt.keeps_open or keeps_open
            if request_id is not None:
                attempt.request_id = request_id
                self._requests[request_id] = attempt_id
            ready = not attempt.pending
        if ready:
            self._finish(attempt_id, now)

    def confirm(
        self,
        attempt_id: str,
        part: str,
        *,
        now: float,
        status: str | None = None,
        error_code: str | None = None,
        playback: str | None = None,
    ) -> None:
        """Một phần đã xác nhận; phần báo lỗi đầu tiên quyết định trạng thái."""
        with self._lock:
            attempt = self._attempts.get(attempt_id)
            if attempt is None or attempt.done or part not in attempt.pending:
                return
            attempt.pending.discard(part)
            if part == "playback":
                attempt.playback = playback or "confirmed"
            if status is not None and status not in SUCCESSES:
                if attempt.status is None or attempt.status in SUCCESSES:
                    attempt.status, attempt.error_code = status, error_code
            elif status is not None and attempt.status is None:
                attempt.status = status
            ready = not attempt.pending and attempt.status is not None
        if ready:
            self._finish(attempt_id, now)

    def finish(self, attempt_id: str, status: str, *, now: float, error_code: str | None = None) -> None:
        """Chốt ngay (huỷ, bị ngắt, lỗi kỹ thuật) bất kể phần đang chờ."""
        with self._lock:
            attempt = self._attempts.get(attempt_id)
            if attempt is None or attempt.done:
                return
            attempt.status, attempt.error_code = status, error_code
            attempt.keeps_open = False
            attempt.pending.clear()
        self._finish(attempt_id, now)

    def by_request(self, request_id: str) -> str | None:
        """Lượt đang giữ `request_id` của lệnh điện thoại."""
        with self._lock:
            return self._requests.get(request_id)

    def expire(self, now: float) -> None:
        """Chốt lượt chờ `played` quá hạn và tác vụ chờ người dùng quá `USAGE_ABANDON_S`."""
        late: list[str] = []
        stale: list[_Task] = []
        with self._lock:
            for attempt in self._attempts.values():
                if not attempt.done and attempt.deadline is not None and now > attempt.deadline:
                    late.append(attempt.attempt_id)
            for key, task in list(self._tasks.items()):
                if (
                    task.waiting_since is not None
                    and task.busy == 0
                    and now - task.waiting_since > config.USAGE_ABANDON_S
                ):
                    stale.append(task)
                    del self._tasks[key]
        for attempt_id in late:
            with self._lock:
                attempt = self._attempts.get(attempt_id)
                if attempt is None or attempt.done:
                    continue
                attempt.pending.discard("playback")
                attempt.playback = "unconfirmed"
                if attempt.status is None or attempt.status in SUCCESSES:
                    attempt.status = "unconfirmed"
                ready = not attempt.pending
            if ready:
                self._finish(attempt_id, now)
        for task in stale:
            self._emit_task(task, "abandoned")

    # ── nội bộ ──────────────────────────────────────────────────────────────

    def _finish(self, attempt_id: str, now: float) -> None:
        with self._lock:
            attempt = self._attempts.pop(attempt_id, None)
            if attempt is None or attempt.done:
                return
            attempt.done = True
            if attempt.request_id is not None:
                self._requests.pop(attempt.request_id, None)
            task = attempt.task
            task.busy = max(0, task.busy - 1)
            status = attempt.status or "technical_error"
            close_task = not attempt.keeps_open
            if close_task:
                if self._tasks.get((task.device, task.feature)) is task:
                    del self._tasks[(task.device, task.feature)]
            else:
                task.waiting_since = now
        usage.emit(
            "attempt_finished",
            attempt_id=attempt.attempt_id,
            task_id=task.task_id,
            status=status,
            error_code=attempt.error_code,
            playback=attempt.playback,
            request_id=attempt.request_id,
            device_id=task.device,
        )
        if close_task:
            self._emit_task(task, status)

    def _emit_task(self, task: _Task, status: str) -> None:
        usage.emit(
            "task_finished",
            task_id=task.task_id,
            feature=task.feature,
            status=status,
            attempts=max(1, task.attempts),
            device_id=task.device,
        )

    def reset(self) -> None:
        """Xoá trạng thái (test)."""
        with self._lock:
            self._tasks.clear()
            self._attempts.clear()
            self._requests.clear()


tracker = Tracker()
usage.on_tick(tracker.expire)
