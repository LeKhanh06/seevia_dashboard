"""Pure aggregation of layer-2 usage rows into additive per-day metrics.

The same functions build the precomputed layer-3 rows (rollup worker, report time zone) and the
on-the-fly answer for another time zone or ``exact=true``: one implementation, so precomputed and
exact numbers can only differ by histogram resolution (percentiles).

Counting rules follow ``SeeVia_Huong_dan_trien_khai_thong_ke.md``: attempts and tasks belong to the
day of their start (a task started 23:59 and closed 00:01 counts for its start day); sessions and
answered calls contribute the part of their duration inside the day; alerts count on the day they
were spoken; samples missing one of a metric's two marks are counted but kept out of percentiles.
"""

from __future__ import annotations

from bisect import bisect_left
from collections import defaultdict
from datetime import UTC, date, datetime, time, timedelta
from typing import TYPE_CHECKING, Any

from app.repositories.usage_rows import ERROR_STATUSES, DayResult, GroupKey

if TYPE_CHECKING:
    from collections.abc import Iterable
    from zoneinfo import ZoneInfo

    from app.repositories.usage_rows import Layer2Rows, SosRow

# Upper bounds (ms) of the latency histogram; one extra open bucket for anything above.
BUCKETS_MS: tuple[int, ...] = (
    250,
    500,
    750,
    1000,
    1500,
    2000,
    3000,
    4000,
    6000,
    8000,
    12000,
    20000,
    30000,
    60000,
    120000,
)
HIST_SIZE = len(BUCKETS_MS) + 1

# A "use" (dashboard ``uses``) is one closed task, one alert-mode activation, one incoming internet
# call that ended, or one finished SOS. Each lands in exactly one outcome bucket below, so totals,
# per-day, per-hour and per-outcome sums always agree.
OUTCOME_OF_TASK: dict[str, str] = {
    "success": "completed",
    "completed_early": "completed_early",
    "failed": "failed",
    "technical_error": "failed",
    "cancelled": "cancelled",
    "unconfirmed": "unconfirmed",
    "abandoned": "abandoned",
    "uncertain": "abandoned",
    "rejected_image": "abandoned",
}
CALL_OUTCOME: dict[str, str] = {
    "ended": "completed",
    "failed": "failed",
    "rejected": "cancelled",
    "missed": "abandoned",
}
SOS_CANCEL_WINDOW = timedelta(seconds=5)
# Sessions whose time counts as "using SeeVia" (connection alone is not usage).
USAGE_SESSION_FEATURE = {
    "ocr_live": "ocr_live",
    "alert_mode": "alert_mode",
    "music": "music",
    "navigation": "navigation",
}


def histogram(values: Iterable[float]) -> list[int]:
    """Count ``values`` (ms) into the fixed buckets."""
    hist = [0] * HIST_SIZE
    for value in values:
        hist[bisect_left(BUCKETS_MS, value)] += 1
    return hist


def percentile(hist: list[int], q: float) -> float | None:
    """Interpolate the ``q`` quantile inside its bucket; ``None`` without samples."""
    total = sum(hist)
    if total == 0:
        return None
    rank = q * total
    seen = 0
    for index, count in enumerate(hist):
        if count and seen + count >= rank:
            low = 0 if index == 0 else BUCKETS_MS[index - 1]
            if index >= len(BUCKETS_MS):
                return float(low)
            high = BUCKETS_MS[index]
            return low + (high - low) * max(0.0, rank - seen) / count
        seen += count
    return float(BUCKETS_MS[-1])


def exact_percentile(values: list[float], q: float) -> float | None:
    """Nearest-rank-interpolated percentile of raw samples (exact mode)."""
    if not values:
        return None
    ordered = sorted(values)
    position = q * (len(ordered) - 1)
    low = int(position)
    high = min(low + 1, len(ordered) - 1)
    return ordered[low] + (ordered[high] - ordered[low]) * (position - low)


def empty_metrics() -> dict[str, Any]:
    """All additive metric keys, zeroed."""
    return {
        "attempts": 0,
        "uses": 0,
        "outcomes": {},
        "tasks_started": 0,
        "tasks_closed": 0,
        "tasks_pending": 0,
        "tasks_by_status": {},
        "first_try_success": 0,
        "extra_attempts": 0,
        "attempts_by_status": {},
        "image_rejected": 0,
        "signals": {},
        "errors": {},
        "first_audio_hist": [0] * HIST_SIZE,
        "time_to_final_hist": [0] * HIST_SIZE,
        "processing_hist": [0] * HIST_SIZE,
        "align_hist": [0] * HIST_SIZE,
        "usage_seconds": 0.0,
        "usage_seconds_total": 0.0,
        "sessions": {},
        "session_seconds": {},
        "session_ended": {},
        "session_ended_seconds": {},
        "session_cancelled": {},
        "session_errors": 0,
        "alerts_by_level": {},
        "calls": 0,
        "calls_known": 0,
        "calls_answered": 0,
        "call_talk_seconds": 0.0,
        "call_errors": {},
        "sos": 0,
        "sos_by_status": {},
        "sos_cancelled_5s": 0,
        "sos_to_answer_hist": [0] * HIST_SIZE,
        "last_error_at": {},
    }


def merge(a: dict[str, Any], b: dict[str, Any]) -> dict[str, Any]:
    """Add two metric dicts key by key (numbers, count dicts, histograms; latest timestamps)."""
    out = dict(a)
    for key, value in b.items():
        current = out.get(key)
        if key == "last_error_at":
            merged = dict(current or {})
            for code, at in value.items():
                merged[code] = max(merged.get(code, at), at)
            out[key] = merged
        elif isinstance(value, dict):
            merged = dict(current or {})
            for sub, count in value.items():
                merged[sub] = merged.get(sub, 0) + count
            out[key] = merged
        elif isinstance(value, list):
            base = current or [0] * len(value)
            out[key] = [x + y for x, y in zip(base, value, strict=True)]
        else:
            out[key] = (current or 0) + value
    return out


def day_bounds(day: date, tz: ZoneInfo) -> tuple[datetime, datetime]:
    """``[start, end)`` of ``day`` in ``tz`` as aware UTC datetimes."""
    start = datetime.combine(day, time.min, tzinfo=tz)
    return start.astimezone(UTC), (start + timedelta(days=1)).astimezone(UTC)


def _key(
    owner: str | None, device: str | None, feature: str, comp: str | None, fw: str | None
) -> GroupKey:
    return (owner or "", device or "", feature, comp or "", fw or "")


def _overlap(start: datetime, end: datetime, lo: datetime, hi: datetime) -> float:
    return max(0.0, (min(end, hi) - max(start, lo)).total_seconds())


def _union_seconds(intervals: list[tuple[datetime, datetime]], lo: datetime, hi: datetime) -> float:
    clipped = sorted((max(s, lo), min(e, hi)) for s, e in intervals if min(e, hi) > max(s, lo))
    total, cur_s, cur_e = 0.0, None, None
    for s, e in clipped:
        if cur_e is None or s > cur_e:
            if cur_e is not None and cur_s is not None:
                total += (cur_e - cur_s).total_seconds()
            cur_s, cur_e = s, e
        else:
            cur_e = max(cur_e, e)
    if cur_e is not None and cur_s is not None:
        total += (cur_e - cur_s).total_seconds()
    return total


def _ms(a: datetime | None, b: datetime | None) -> float | None:
    if a is None or b is None or b < a:
        return None
    return (b - a).total_seconds() * 1000


def _bump(counter: dict[str, int], key: str, n: int = 1) -> None:
    counter[key] = counter.get(key, 0) + n


def _use(
    m: dict[str, Any],
    hourly: dict[tuple[int, str], list[int]],
    hour: int,
    feature: str,
    outcome: str | None,
) -> None:
    """Count one dashboard "use" in its day row, its outcome and its start hour."""
    m["uses"] += 1
    _bump(m["outcomes"], outcome or "failed")
    hourly[(hour, feature)][0] += 1


def _sos_quick_cancel(sos: SosRow) -> bool:
    """Tell whether an SOS was cancelled within the 5-second false-start window."""
    return sos.finished_at is not None and sos.finished_at - sos.started_at <= SOS_CANCEL_WINDOW


def aggregate_day(  # noqa: C901, PLR0912, PLR0915 -- one pass per row family, kept together on purpose
    rows: Layer2Rows, day: date, tz: ZoneInfo
) -> DayResult:
    """Build every layer-3 row of ``day`` (in ``tz``) from the layer-2 rows touching it."""
    lo, hi = day_bounds(day, tz)
    daily: dict[GroupKey, dict[str, Any]] = defaultdict(empty_metrics)
    hourly: dict[tuple[int, str], list[int]] = defaultdict(lambda: [0, 0])
    active: set[tuple[str, str]] = set()
    usage_intervals: dict[tuple[str, str, str], list[tuple[datetime, datetime]]] = defaultdict(list)

    # OCR that had to frame the page live is reported as its own feature ("ocr_live").
    live_tasks = {
        a.task_id
        for a in rows.attempts
        if a.feature == "ocr" and a.align_ms is not None and a.task_id is not None
    }
    for a in rows.attempts:
        if a.started_at is None or not lo <= a.started_at < hi or a.feature is None:
            continue
        feature = "ocr_live" if a.feature == "ocr" and a.task_id in live_tasks else a.feature
        m = daily[
            _key(a.owner_hash, a.device_hash, feature, a.component_version, a.firmware_version)
        ]
        m["attempts"] += 1
        hourly[(a.started_at.astimezone(tz).hour, feature)][1] += 1
        active.add((a.owner_hash or "", a.device_hash or ""))
        if a.status is not None:
            _bump(m["attempts_by_status"], a.status)
        for name, count in a.signals.items():
            _bump(m["signals"], name, count)
        m["image_rejected"] += a.signals.get("image_rejected", 0)
        if a.error_code and a.status in ERROR_STATUSES:
            _bump(m["errors"], a.error_code)
            when = (a.finished_at or a.started_at).isoformat()
            m["last_error_at"][a.error_code] = max(m["last_error_at"].get(a.error_code, when), when)
        if (first := _ms(a.started_at, a.first_audio_at)) is not None:
            m["first_audio_hist"][bisect_left(BUCKETS_MS, first)] += 1
        if (final := _ms(a.started_at, a.finished_at)) is not None:
            m["time_to_final_hist"][bisect_left(BUCKETS_MS, final)] += 1
        if a.processing_ms is not None:
            m["processing_hist"][bisect_left(BUCKETS_MS, a.processing_ms)] += 1
        if a.align_ms is not None:
            m["align_hist"][bisect_left(BUCKETS_MS, a.align_ms)] += 1
        if a.finished_at is not None:
            usage_intervals[(a.owner_hash or "", a.device_hash or "", feature)].append(
                (a.started_at, a.finished_at)
            )

    for t in rows.tasks:
        if t.started_at is None or not lo <= t.started_at < hi or t.feature is None:
            continue
        feature = "ocr_live" if t.feature == "ocr" and t.task_id in live_tasks else t.feature
        m = daily[
            _key(t.owner_hash, t.device_hash, feature, t.component_version, t.firmware_version)
        ]
        m["tasks_started"] += 1
        if t.status is None:
            m["tasks_pending"] += 1
            continue
        m["tasks_closed"] += 1
        _bump(m["tasks_by_status"], t.status)
        _use(m, hourly, t.started_at.astimezone(tz).hour, feature, OUTCOME_OF_TASK.get(t.status))
        attempts = t.attempts or 1
        m["extra_attempts"] += attempts - 1
        if t.status in ("success", "completed_early") and attempts == 1:
            m["first_try_success"] += 1

    for s in rows.sessions:
        if s.started_at is None:
            continue
        end = s.ended_at or s.last_seen_at or s.started_at
        seconds = _overlap(s.started_at, end, lo, hi)
        starts_today = lo <= s.started_at < hi
        if seconds <= 0 and not starts_today:
            continue
        feature = USAGE_SESSION_FEATURE.get(s.kind, "")
        m = daily[_key(s.owner_hash, s.device_hash, feature, None, s.firmware_version)]
        m["session_seconds"][s.kind] = m["session_seconds"].get(s.kind, 0) + seconds
        if starts_today:
            _bump(m["sessions"], s.kind)
            if s.kind == "alert_mode":
                outcome = "failed" if s.end_reason == "error" else "completed"
                _use(m, hourly, s.started_at.astimezone(tz).hour, feature, outcome)
            if s.align_ms is not None:
                m["align_hist"][bisect_left(BUCKETS_MS, s.align_ms)] += 1
            if s.ended_at is not None:
                _bump(m["session_ended"], s.kind)
                m["session_ended_seconds"][s.kind] = (
                    m["session_ended_seconds"].get(s.kind, 0)
                    + (s.ended_at - s.started_at).total_seconds()
                )
                if s.end_reason in ("cancelled", "user_off") and s.kind in ("music", "navigation"):
                    _bump(m["session_cancelled"], s.kind)
            if s.kind == "connection" and s.end_reason in ("error", "timeout"):
                m["session_errors"] += 1
        if feature:
            usage_intervals[(s.owner_hash or "", s.device_hash or "", feature)].append(
                (s.started_at, end)
            )

    for alert in rows.alerts:
        if not lo <= alert.occurred_at < hi:
            continue
        m = daily[_key(alert.owner_hash, alert.device_hash, "alert_mode", None, None)]
        _bump(m["alerts_by_level"], str(alert.level))

    for c in rows.calls:
        talk_end = c.ended_at
        if c.answered_at is not None and talk_end is not None:
            talk = _overlap(c.answered_at, talk_end, lo, hi)
        else:
            talk = 0.0
        starts_today = lo <= c.started_at < hi
        if not starts_today and talk <= 0:
            continue
        m = daily[_key(c.owner_hash, c.device_hash, "internet_call", None, None)]
        m["call_talk_seconds"] += talk
        if starts_today:
            m["calls"] += 1
            if c.final_state is not None or c.answered_at is not None:
                m["calls_known"] += 1
            if c.answered_at is not None:
                m["calls_answered"] += 1
            if c.final_state == "failed":
                _bump(m["call_errors"], c.end_reason or "failed")
            if c.direction == "to_glasses" and c.final_state in CALL_OUTCOME:
                hour = c.started_at.astimezone(tz).hour
                _use(m, hourly, hour, "internet_call", CALL_OUTCOME[c.final_state])
        if c.answered_at is not None and talk_end is not None:
            usage_intervals[(c.owner_hash or "", c.device_hash or "", "internet_call")].append(
                (c.answered_at, talk_end)
            )

    for sos in rows.sos:
        if not lo <= sos.started_at < hi:
            continue
        m = daily[_key(sos.owner_hash, sos.device_hash, "sos", None, None)]
        m["sos"] += 1
        status = sos.status or "pending"
        quick_cancel = status == "cancelled" and _sos_quick_cancel(sos)
        if quick_cancel:
            m["sos_cancelled_5s"] += 1
        _bump(m["sos_by_status"], status)
        if (wait := _ms(sos.started_at, sos.answered_at)) is not None:
            m["sos_to_answer_hist"][bisect_left(BUCKETS_MS, wait)] += 1
        if sos.error_code:
            _bump(m["errors"], sos.error_code)
        if status != "pending":
            sos_outcome = {"success": "completed", "failed": "failed"}.get(status)
            if status == "cancelled":
                # Cancelled within 5 s = a false start; cancelled later = the chain did not help.
                sos_outcome = "cancelled" if quick_cancel else "failed"
            _use(m, hourly, sos.started_at.astimezone(tz).hour, "sos", sos_outcome)

    # Usage time: union of a feature's intervals per owner/device (overlap counted once) ...
    totals: dict[tuple[str, str], list[tuple[datetime, datetime]]] = defaultdict(list)
    for (owner, device, feature), intervals in usage_intervals.items():
        seconds = _union_seconds(intervals, lo, hi)
        match = [k for k in daily if k[0] == owner and k[1] == device and k[2] == feature]
        target = match[0] if match else _key(owner, device, feature, None, None)
        daily[target]["usage_seconds"] += seconds
        totals[(owner, device)].extend(intervals)
    # ... and across features under feature "" (the overall figure, again counted once).
    for (owner, device), intervals in totals.items():
        daily[_key(owner, device, "", None, None)]["usage_seconds_total"] = _union_seconds(
            intervals, lo, hi
        )

    return DayResult(
        daily=dict(daily),
        hourly={key: (value[0], value[1]) for key, value in hourly.items()},
        active=active,
    )
