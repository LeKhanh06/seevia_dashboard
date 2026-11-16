"""Pure usage aggregation: histograms, percentiles, day attribution, cross-midnight sessions."""

from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

from app.repositories.usage_rows import (
    AlertRow,
    AttemptRow,
    Layer2Rows,
    SessionRow,
    SosRow,
    TaskRow,
)
from app.services.usage_aggregate import (
    aggregate_day,
    empty_metrics,
    exact_percentile,
    histogram,
    merge,
    percentile,
)

VN = ZoneInfo("Asia/Ho_Chi_Minh")


def vn(local: str) -> datetime:
    """Turn a Vietnam wall-clock time into an aware UTC datetime."""
    return datetime.fromisoformat(local).replace(tzinfo=VN).astimezone(UTC)


def _attempt(start: str, end: str | None, status: str | None, **extra: object) -> AttemptRow:
    values: dict[str, object] = {
        "task_id": "t",
        "feature": "ocr",
        "owner_hash": "o1",
        "device_hash": "d1",
        "component_version": "v1",
        "firmware_version": "2.1.0",
        "started_at": vn(start),
        "first_audio_at": None,
        "finished_at": vn(end) if end else None,
        "status": status,
        "error_code": None,
        "processing_ms": None,
        "align_ms": None,
        "signals": {},
    }
    values.update(extra)
    return AttemptRow(**values)  # type: ignore[arg-type]


def _task(start: str, status: str | None, attempts: int) -> TaskRow:
    return TaskRow("ocr", "o1", "d1", "v1", "2.1.0", vn(start), status, attempts)


def _hist(result: object, key: str) -> list[int]:
    value = _total(result, key)
    assert isinstance(value, list)
    return value


def _total(result: object, key: str) -> object:
    merged = empty_metrics()
    for metrics in result.daily.values():  # type: ignore[attr-defined]
        merged = merge(merged, metrics)
    return merged[key]


def test_histogram_and_percentile() -> None:
    """p50 of 100..1000 ms lands in the 500-750 bucket; empty -> None."""
    hist = histogram(range(100, 1001, 100))
    p50 = percentile(hist, 0.5)
    assert p50 is not None
    assert 500 <= p50 <= 750
    assert percentile([0] * len(hist), 0.5) is None
    assert exact_percentile([], 0.9) is None
    assert exact_percentile([1.0, 2.0, 3.0], 0.5) == 2.0


def test_merge_is_additive() -> None:
    """Merging two days adds counters, count dicts and histograms."""
    a = empty_metrics() | {"attempts": 2, "signals": {"reprompt": 1}}
    b = empty_metrics() | {"attempts": 3, "signals": {"reprompt": 2, "needs_web": 1}}
    a["first_audio_hist"] = histogram([300])
    b["first_audio_hist"] = histogram([300, 5000])
    out = merge(a, b)
    assert out["attempts"] == 5
    assert out["signals"] == {"reprompt": 3, "needs_web": 1}
    assert sum(out["first_audio_hist"]) == 3


def test_retake_scenario_counts() -> None:
    """Chụp lại: 2 lượt, 1 ảnh từ chối, 1 tác vụ thành công sau 2 lượt (không phải lần đầu)."""
    rows = Layer2Rows(
        attempts=[
            _attempt(
                "2026-11-16 09:00:00",
                "2026-11-16 09:00:03",
                "rejected_image",
                signals={"image_rejected": 1},
            ),
            _attempt(
                "2026-11-16 09:00:40",
                "2026-11-16 09:00:47",
                "success",
                first_audio_at=vn("2026-11-16 09:00:42"),
            ),
        ],
        tasks=[_task("2026-11-16 09:00:00", "success", 2)],
    )
    result = aggregate_day(rows, date(2026, 11, 16), VN)
    assert _total(result, "attempts") == 2
    assert _total(result, "image_rejected") == 1
    assert _total(result, "tasks_closed") == 1
    assert _total(result, "first_try_success") == 0
    assert _total(result, "extra_attempts") == 1
    assert sum(_hist(result, "first_audio_hist")) == 1  # lượt 1 thiếu mốc tiếng đầu: ngoài mẫu


def test_task_counted_on_start_day() -> None:
    """Bắt đầu 23:59, chốt 00:01: thuộc ngày bắt đầu, ngày sau không có."""
    rows = Layer2Rows(
        attempts=[_attempt("2026-11-16 23:59:00", "2026-11-17 00:01:00", "success")],
        tasks=[_task("2026-11-16 23:59:00", "success", 1)],
    )
    day1 = aggregate_day(rows, date(2026, 11, 16), VN)
    day2 = aggregate_day(rows, date(2026, 11, 17), VN)
    assert _total(day1, "tasks_closed") == 1
    assert _total(day2, "tasks_closed") == 0
    assert _total(day2, "attempts") == 0


def test_cross_midnight_alert_session() -> None:
    """Cảnh báo 23:50-00:10 VN, 2 cảnh báo: 600 s mỗi ngày; cả kỳ 1200 s và 6 cảnh báo/giờ."""
    session = SessionRow(
        "alert_mode",
        "o1",
        "d1",
        None,
        vn("2026-11-16 23:50:00"),
        None,
        vn("2026-11-17 00:10:00"),
        "user_off",
        None,
    )
    alerts = [
        AlertRow("o1", "d1", vn("2026-11-16 23:55:00"), 2),
        AlertRow("o1", "d1", vn("2026-11-17 00:05:00"), 2),
    ]
    rows = Layer2Rows(sessions=[session], alerts=alerts)
    d1 = aggregate_day(rows, date(2026, 11, 16), VN)
    d2 = aggregate_day(rows, date(2026, 11, 17), VN)
    assert _total(d1, "session_seconds") == {"alert_mode": 600.0}
    assert _total(d2, "session_seconds") == {"alert_mode": 600.0}
    both = merge(merge(empty_metrics(), _total_metrics(d1)), _total_metrics(d2))
    seconds = float(both["session_seconds"]["alert_mode"])
    alerts_total = sum(both["alerts_by_level"].values())
    assert seconds == 1200.0
    assert alerts_total / (seconds / 3600) == 6.0
    assert _total(d1, "sessions") == {"alert_mode": 1}
    assert _total(d2, "sessions") == {}


def _total_metrics(result: object) -> dict[str, object]:
    merged = empty_metrics()
    for metrics in result.daily.values():  # type: ignore[attr-defined]
        merged = merge(merged, metrics)
    return merged


def test_missing_first_audio_counted_not_sampled() -> None:
    """Lượt thiếu mốc tiếng đầu vẫn được đếm, ngoài tập mẫu p50/p90."""
    rows = Layer2Rows(attempts=[_attempt("2026-11-16 10:00:00", "2026-11-16 10:00:05", "success")])
    result = aggregate_day(rows, date(2026, 11, 16), VN)
    assert _total(result, "attempts") == 1
    assert sum(_hist(result, "first_audio_hist")) == 0
    assert sum(_hist(result, "time_to_final_hist")) == 1


def test_usage_seconds_overlap_counted_once() -> None:
    """Hai lượt chồng nhau 2 s: thời gian sử dụng tính một lần."""
    rows = Layer2Rows(
        attempts=[
            _attempt("2026-11-16 10:00:00", "2026-11-16 10:00:05", "success"),
            _attempt("2026-11-16 10:00:03", "2026-11-16 10:00:08", "success", feature="chat"),
        ]
    )
    result = aggregate_day(rows, date(2026, 11, 16), VN)
    total = sum(m["usage_seconds_total"] for m in result.daily.values())
    assert total == 8.0


def test_sos_cancel_within_5s_and_answer_time() -> None:
    """SOS huỷ trong 5 s được đếm riêng; thời gian tới người nghe vào mẫu."""
    t0 = vn("2026-11-16 08:00:00")
    rows = Layer2Rows(
        sos=[
            SosRow("o1", "d1", t0, None, t0 + timedelta(seconds=3), "cancelled", None),
            SosRow(
                "o1",
                "d1",
                t0,
                t0 + timedelta(seconds=9),
                t0 + timedelta(seconds=40),
                "success",
                None,
            ),
            SosRow("o1", "d1", t0, None, t0 + timedelta(seconds=90), "failed", "NO_ANSWER"),
        ]
    )
    result = aggregate_day(rows, date(2026, 11, 16), VN)
    assert _total(result, "sos") == 3
    assert _total(result, "sos_cancelled_5s") == 1
    assert _total(result, "sos_by_status") == {"cancelled": 1, "success": 1, "failed": 1}
    assert sum(_hist(result, "sos_to_answer_hist")) == 1


def test_active_only_from_real_use() -> None:
    """Kính chỉ kết nối không tính là hoạt động."""
    connection = SessionRow(
        "connection",
        "o2",
        "d2",
        "2.1.0",
        vn("2026-11-16 07:00:00"),
        vn("2026-11-16 08:00:00"),
        None,
        None,
        None,
    )
    rows = Layer2Rows(
        attempts=[_attempt("2026-11-16 09:00:00", "2026-11-16 09:00:05", "success")],
        sessions=[connection],
    )
    result = aggregate_day(rows, date(2026, 11, 16), VN)
    assert result.active == {("o1", "d1")}
