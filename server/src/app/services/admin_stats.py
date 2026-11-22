"""Admin dashboard statistics (shapes of seevia_dashboard ``API_CONTRACT.md``).

Counting rules (business doc + spec §9.2):

* A **use** is one closed task, one alert-mode activation, one incoming internet call that ended,
  or one finished SOS (see :mod:`app.services.usage_aggregate`). Every use has exactly one outcome,
  one start day and one start hour, so ``totals.uses`` = Σ series = Σ ``by_day`` = Σ heatmap =
  Σ ``outcomes`` = Σ groups + ``sos_uses``.
* Completion/first-try/abandon rates are over task uses only (sessions/calls/SOS excluded).
* Periods are Vietnam calendar days, ``from``/``to`` inclusive; the previous period has the same
  length and ends the day before ``from``. Rates are 0-100; missing samples → ``null``.
* No response carries an owner/device pseudonym; only aggregated numbers (devices in
  ``attention`` use the same ids as ``/admin/devices``).
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from typing import TYPE_CHECKING, Any, Final, Literal
from zoneinfo import ZoneInfo

from app.repositories.admin_stats import AdminStatsRepository, DailyRow
from app.services import llm_cost
from app.services.admin_directory import AdminDirectoryService, ListQuery
from app.services.usage_aggregate import empty_metrics, merge, percentile
from app.services.usage_ids import pseudonym

if TYPE_CHECKING:
    from collections.abc import Iterable

    from sqlalchemy.ext.asyncio import AsyncSession

VN: Final = ZoneInfo("Asia/Ho_Chi_Minh")
VN_NAME: Final = "Asia/Ho_Chi_Minh"
MAX_DAYS: Final = 400

PeriodName = Literal["today", "week", "month", "custom"]

# Dashboard group -> [(dashboard feature key, raw features, uses_unit)].
GROUPS: Final[dict[str, list[tuple[str, tuple[str, ...], str]]]] = {
    "see_read": [
        ("ocr", ("ocr",), "uses"),
        ("ocr_live", ("ocr_live",), "sessions"),
        ("money", ("money",), "uses"),
        ("ask", ("ask",), "uses"),
    ],
    "find": [
        ("find", ("find",), "uses"),
        ("space", ("space",), "uses"),
        ("hazard", ("hazard",), "uses"),
    ],
    "connect": [
        ("chat", ("chat",), "uses"),
        ("family", ("call", "message_send", "message_read"), "uses"),
        ("internet_call", ("internet_call",), "calls"),
        ("music", ("music",), "uses"),
    ],
    "go": [
        ("directions", ("navigation",), "uses"),
        ("ride", ("ride",), "uses"),
        ("alert_mode", ("alert_mode",), "activations"),
    ],
}
GROUP_PAGES: Final = {"see-read": "see_read", "find": "find", "connect": "connect", "go": "go"}
GROUP_NAMES: Final = {
    "see_read": "See & Read",
    "find": "Find & Understand",
    "connect": "Connect",
    "go": "Go",
}
DASH_KEY: Final = {raw: key for feats in GROUPS.values() for key, raws, _ in feats for raw in raws}
GROUP_OF: Final = {key: group for group, feats in GROUPS.items() for key, _, _ in feats}
EXPERIMENTAL: Final = frozenset({"hazard"})
NON_TASK: Final = frozenset({"internet_call", "alert_mode"})
# Raw features counted as uses: every dashboard feature plus SOS.
USE_FEATURES: Final = frozenset(DASH_KEY) | {"sos"}
TASK_FEATURES: Final = frozenset(raw for raw, key in DASH_KEY.items() if key not in NON_TASK)
OUTCOME_KEYS: Final = (
    "completed",
    "completed_early",
    "failed",
    "cancelled",
    "unconfirmed",
    "abandoned",
)


# ---------------------------------------------------------------- period


@dataclass(frozen=True, slots=True)
class Period:
    """Inclusive Vietnam-day range plus its same-length predecessor."""

    name: str
    first: date
    last: date

    @property
    def days(self) -> int:
        """Number of calendar days."""
        return (self.last - self.first).days + 1

    @property
    def dates(self) -> list[date]:
        """Every day of the period."""
        return [self.first + timedelta(days=i) for i in range(self.days)]

    @property
    def previous(self) -> Period:
        """Same length, ending the day before ``first``."""
        last = self.first - timedelta(days=1)
        return Period(self.name, last - timedelta(days=self.days - 1), last)

    @property
    def hourly(self) -> bool:
        """One-day periods are reported per hour."""
        return self.first == self.last

    def bounds(self) -> tuple[datetime, datetime]:
        """``[start, end)`` in UTC."""
        start = datetime.combine(self.first, time.min, tzinfo=VN)
        end = datetime.combine(self.last + timedelta(days=1), time.min, tzinfo=VN)
        return start.astimezone(UTC), end.astimezone(UTC)


class InvalidPeriodError(ValueError):
    """``from``/``to``/``period`` do not describe a usable report period."""


def today_vn(now: datetime | None = None) -> date:
    """Today's Vietnam date."""
    return (now or datetime.now(UTC)).astimezone(VN).date()


def resolve_period(
    name: PeriodName, first: date | None, last: date | None, *, now: datetime | None = None
) -> Period:
    """Validate ``from``/``to`` (inclusive) or derive them from ``period``."""
    today = today_vn(now)
    if first is None or last is None:
        if name == "today":
            first = last = today
        elif name == "month":
            first, last = today.replace(day=1), today
        elif name == "week":
            first, last = today - timedelta(days=today.weekday()), today
        else:
            msg = "Thiếu from/to cho kỳ tuỳ chọn."
            raise InvalidPeriodError(msg)
    if first > last:
        msg = "from phải trước hoặc bằng to."
        raise InvalidPeriodError(msg)
    if (last - first).days + 1 > MAX_DAYS:
        msg = f"Kỳ tối đa {MAX_DAYS} ngày."
        raise InvalidPeriodError(msg)
    return Period(name, first, last)


# ---------------------------------------------------------------- number helpers


def rate(numerator: float, denominator: float) -> float | None:
    """Percentage 0-100 with one decimal, ``None`` without a denominator."""
    return round(100 * numerator / denominator, 1) if denominator else None


def change_pct(current: float, previous: float) -> float | None:
    """Relative change in percent; ``None`` when the previous value is 0."""
    return round(100 * (current - previous) / previous, 1) if previous else None


def points(current: float | None, previous: float | None) -> float | None:
    """Difference in percentage points."""
    return None if current is None or previous is None else round(current - previous, 1)


def p_ms(hist: list[int], q: float) -> int | None:
    """Percentile of a latency histogram, in whole ms."""
    value = percentile(hist, q)
    return None if value is None else round(value)


def p_seconds(hist: list[int], q: float = 0.5) -> float | None:
    """Percentile of a latency histogram, in seconds (one decimal)."""
    value = percentile(hist, q)
    return None if value is None else round(value / 1000, 1)


def share_largest_remainder(counts: list[int]) -> list[int] | None:
    """Integer shares that add up to exactly 100 (``None`` when everything is 0)."""
    total = sum(counts)
    if not total:
        return None
    raw = [100 * c / total for c in counts]
    out = [int(r) for r in raw]
    order = sorted(range(len(raw)), key=lambda i: raw[i] - out[i], reverse=True)
    for i in order[: 100 - sum(out)]:
        out[i] += 1
    return out


def _avg(total: float, count: float) -> float | None:
    return round(total / count, 1) if count else None


def _vn_iso(value: str | None) -> str | None:
    if value is None:
        return None
    return datetime.fromisoformat(value).astimezone(VN).isoformat()


# ---------------------------------------------------------------- window


@dataclass(slots=True)
class Window:
    """Layer-3 data of one period, pre-merged in the shapes the endpoints need."""

    period: Period
    rows: list[DailyRow]
    hourly: list[tuple[date, int, str, int]]
    active: list[tuple[date, str, str]]
    by_feature: dict[str, dict[str, Any]] = field(default_factory=dict)
    by_day: dict[date, dict[str, dict[str, Any]]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        """Merge rows per raw feature and per (day, raw feature)."""
        by_feature: dict[str, dict[str, Any]] = defaultdict(empty_metrics)
        by_day: dict[date, dict[str, dict[str, Any]]] = defaultdict(
            lambda: defaultdict(empty_metrics)
        )
        for row in self.rows:
            by_feature[row.feature] = merge(by_feature[row.feature], row.metrics)
            by_day[row.day][row.feature] = merge(by_day[row.day][row.feature], row.metrics)
        self.by_feature = dict(by_feature)
        self.by_day = {d: dict(f) for d, f in by_day.items()}

    def metrics(self, raws: Iterable[str]) -> dict[str, Any]:
        """Merge the metrics of several raw features."""
        out = empty_metrics()
        for raw in raws:
            if raw in self.by_feature:
                out = merge(out, self.by_feature[raw])
        return out

    def day_metrics(self, day: date, raws: Iterable[str]) -> dict[str, Any]:
        """Merge the metrics of several raw features on one day."""
        out = empty_metrics()
        for raw in raws:
            if raw in self.by_day.get(day, {}):
                out = merge(out, self.by_day[day][raw])
        return out

    def uses(self, raws: Iterable[str] = USE_FEATURES) -> int:
        """Total uses of ``raws``."""
        return int(self.metrics(raws)["uses"])

    def completion(self, raws: Iterable[str] = TASK_FEATURES) -> float | None:
        """Completed (incl. early) task uses over task uses."""
        m = self.metrics(raws)
        return rate(_done(m), m["uses"])

    def usage_seconds(self) -> int:
        """Overall usage time (overlaps counted once per owner/device/day)."""
        return round(float(self.metrics([""])["usage_seconds_total"]))

    def owners(self) -> set[str]:
        """Distinct paired owners with a use."""
        return {o for _, o, _ in self.active if o}

    def owners_on(self, day: date) -> int:
        """Distinct paired owners with a use on ``day``."""
        return len({o for d, o, _ in self.active if d == day and o})

    def hour_uses(self) -> dict[tuple[date, int], int]:
        """Count uses per (day, hour) for counted features."""
        out: dict[tuple[date, int], int] = defaultdict(int)
        for day, hour, feature, uses in self.hourly:
            if feature in USE_FEATURES:
                out[(day, hour)] += uses
        return out


# ---------------------------------------------------------------- service


class AdminStatsService:
    """Builds every ``/admin/stats/*`` payload."""

    def __init__(self, session: AsyncSession, *, id_key: bytes | None) -> None:
        """Bind to a request session."""
        self._session = session
        self._repo = AdminStatsRepository(session)
        self._key = id_key

    async def window(self, period: Period, owner_hash: str | None = None) -> Window:
        """Load one period (``owner_hash`` narrows to one owner; hourly is then left empty)."""
        return Window(
            period,
            await self._repo.daily(period.first, period.last, owner_hash),
            [] if owner_hash else await self._repo.hourly(period.first, period.last),
            await self._repo.active_days(period.first, period.last, owner_hash),
        )

    async def _pair(self, period: Period) -> tuple[Window, Window]:
        return await self.window(period), await self.window(period.previous)

    @staticmethod
    def _is_future(day: date, hour: int | None = None, *, now: datetime | None = None) -> bool:
        current = (now or datetime.now(UTC)).astimezone(VN)
        if day != current.date():
            return day > current.date()
        return hour is not None and hour > current.hour

    # ---------------------------------------------------------- overview

    async def overview(self, period: Period) -> dict[str, Any]:
        """``GET /admin/stats/overview``."""
        cur, prev = await self._pair(period)
        uses, uses_prev = cur.uses(), prev.uses()
        owners, owners_prev = len(cur.owners()), len(prev.owners())
        completion, completion_prev = cur.completion(), prev.completion()
        seconds, seconds_prev = cur.usage_seconds(), prev.usage_seconds()
        groups = [
            {"key": g, "uses": cur.uses(r for _, raws, _ in feats for r in raws)}
            for g, feats in GROUPS.items()
        ]
        features: list[dict[str, Any]] = [
            {
                "key": key,
                "uses": cur.uses(raws),
                "completion_rate": None if key in NON_TASK else cur.completion(raws),
            }
            for feats in GROUPS.values()
            for key, raws, _ in feats
        ]
        top = sorted((f for f in features if f["uses"]), key=lambda f: -f["uses"])[:5]
        return {
            "range": self._range(period),
            "totals": {
                "uses": uses,
                "active_owners": owners,
                "completion_rate": completion,
                "usage_seconds": seconds,
                "usage_seconds_per_owner": round(seconds / owners) if owners else None,
                "uses_change_pct": change_pct(uses, uses_prev),
                "active_owners_change": owners - owners_prev,
                "completion_rate_change_points": points(completion, completion_prev),
                "usage_seconds_change_pct": change_pct(seconds, seconds_prev),
            },
            "series": await self._series(cur, prev),
            "groups": groups,
            "sos_uses": cur.uses(["sos"]),
            "top_features": top,
            "attention": await self._attention(cur, prev),
        }

    @staticmethod
    def _range(period: Period) -> dict[str, Any]:
        prev = period.previous
        return {
            "period": period.name,
            "from": period.first.isoformat(),
            "to": period.last.isoformat(),
            "compare_from": prev.first.isoformat(),
            "compare_to": prev.last.isoformat(),
        }

    async def _series(self, cur: Window, prev: Window) -> dict[str, Any]:
        period = cur.period
        if period.hourly:
            day, prev_day = period.first, prev.period.first
            hours, hours_prev = cur.hour_uses(), prev.hour_uses()
            lo, hi = period.bounds()
            plo, phi = prev.period.bounds()
            owners = await self._repo.owners_by_hour(lo, hi, VN_NAME)
            owners_prev = await self._repo.owners_by_hour(plo, phi, VN_NAME)
            pts: list[dict[str, Any]] = []
            for h in range(24):
                future = self._is_future(day, h)
                pts.append(
                    {
                        "bucket": datetime.combine(day, time(h), tzinfo=VN).isoformat(),
                        "uses": None if future else hours.get((day, h), 0),
                        "uses_prev": hours_prev.get((prev_day, h), 0),
                        "active_owners": None if future else owners.get(h, 0),
                        "active_owners_prev": owners_prev.get(h, 0),
                        "completion_rate": None,
                        "completion_rate_prev": None,
                        "usage_seconds": None,
                        "usage_seconds_prev": None,
                    }
                )
            return {"granularity": "hour", "points": pts}
        pts = []
        for day, prev_day in zip(period.dates, prev.period.dates, strict=True):
            future = self._is_future(day)
            today, before = (
                cur.day_metrics(day, USE_FEATURES),
                prev.day_metrics(prev_day, USE_FEATURES),
            )
            task, task_prev = (
                cur.day_metrics(day, TASK_FEATURES),
                prev.day_metrics(prev_day, TASK_FEATURES),
            )
            pts.append(
                {
                    "bucket": day.isoformat(),
                    "uses": None if future else today["uses"],
                    "uses_prev": before["uses"],
                    "active_owners": None if future else cur.owners_on(day),
                    "active_owners_prev": prev.owners_on(prev_day),
                    "completion_rate": None if future else rate(_done(task), task["uses"]),
                    "completion_rate_prev": rate(_done(task_prev), task_prev["uses"]),
                    "usage_seconds": None
                    if future
                    else round(cur.day_metrics(day, [""])["usage_seconds_total"]),
                    "usage_seconds_prev": round(
                        prev.day_metrics(prev_day, [""])["usage_seconds_total"]
                    ),
                }
            )
        return {"granularity": "day", "points": pts}

    async def _attention(self, cur: Window, prev: Window) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        now_errors, before = _error_counts(cur), _error_counts(prev)
        rising = sorted(
            (
                (count - before.get(k, 0), k)
                for k, count in now_errors.items()
                if count > before.get(k, 0)
            ),
            reverse=True,
        )
        out += [
            {"type": "error_increase", "error_code": code, "feature": feature, "delta": delta}
            for delta, (code, feature) in rising[:3]
        ]
        sos = cur.metrics(["sos"])
        if sos["uses"]:
            out.append(
                {
                    "type": "sos",
                    "count": sos["uses"],
                    "cancelled": sos["outcomes"].get("cancelled", 0),
                }
            )
        devices = await AdminDirectoryService(self._session, id_key=self._key).devices(
            ListQuery(page=1, page_size=3, sort="last_seen_at", order="desc", status="offline")
        )
        out += [
            {"type": "device_offline", "device_id": d.id, "since": d.last_seen_at.isoformat()}
            for d in devices.items
            if d.last_seen_at is not None
        ]
        return out

    # ---------------------------------------------------------- group pages

    async def group_page(self, page: str, period: Period) -> dict[str, Any]:
        """``GET /admin/stats/{see-read,find,connect,go}``."""
        group = GROUP_PAGES[page]
        cur = await self.window(period)
        raws = [r for _, rs, _ in GROUPS[group] for r in rs]
        total = cur.uses()
        uses = cur.uses(raws)
        summary: dict[str, Any] = {"uses": uses, "share_of_total_pct": rate(uses, total)}
        if group == "go":
            alert = cur.metrics(["alert_mode"])
            on = alert["session_seconds"].get("alert_mode", 0.0)
            summary["alert_mode_seconds"] = round(on)
            summary["alerts_per_hour"] = _per_hour(alert, on)
        else:
            task_raws = [r for r in raws if r in TASK_FEATURES]
            summary["completion_rate"] = cur.completion(task_raws)
            summary["usage_seconds"] = round(cur.metrics(raws)["usage_seconds"])
        features = [self._feature(cur, key, raws_, unit) for key, raws_, unit in GROUPS[group]]
        return {"range": self._range(period), "summary": summary, "features": features}

    @staticmethod
    def _feature(cur: Window, key: str, raws: tuple[str, ...], unit: str) -> dict[str, Any]:
        m = cur.metrics(raws)
        errors = sum(m["errors"].values())
        out: dict[str, Any] = {
            "key": key,
            "uses": m["uses"],
            "uses_unit": unit,
            "completion_rate": None if key in NON_TASK else cur.completion(raws),
            "first_audio_p50_ms": None if key in NON_TASK else p_ms(m["first_audio_hist"], 0.5),
            "errors": errors,
            "is_experimental": key in EXPERIMENTAL,
            "details": _details(key, m),
        }
        if errors:
            code, count = max(m["errors"].items(), key=lambda kv: (kv[1], kv[0]))
            out["top_error"] = {"code": code, "count": count}
        return out

    # ---------------------------------------------------------- community

    async def community(self, period: Period) -> dict[str, Any]:
        """``GET /admin/stats/community`` (install/battery have no source yet → null)."""
        cur = await self.window(period)
        conn = cur.metrics([""])
        active_days = {
            row.day
            for row in cur.rows
            if row.metrics.get("uses") or row.metrics.get("session_seconds", {}).get("connection")
        }
        return {
            "range": self._range(period),
            "install_attempts": None,
            "install_successes": None,
            "install_success_rate": None,
            "install_errors": [],
            "install_p50_seconds": None,
            "emergency_contact_accounts": await self._repo.emergency_contact_accounts(),
            "total_accounts": await self._repo.total_accounts(),
            "disconnects_per_day": round(conn["session_errors"] / period.days),
            "battery_to_20_p50_seconds": None,
            "glasses_sessions": conn["sessions"].get("connection", 0),
            "glasses_session_seconds": round(conn["session_seconds"].get("connection", 0.0)),
            "glasses_active_days": len(active_days),
        }

    # ---------------------------------------------------------- time

    async def time_of_use(self, period: Period) -> dict[str, Any]:
        """``GET /admin/stats/time`` (uses by day, hour, week and weekday x hour)."""
        cur = await self.window(period)
        per_hour = cur.hour_uses()
        cells = [[0] * 24 for _ in range(7)]
        by_hour = [0] * 24
        per_day: dict[date, int] = defaultdict(int)
        for (day, hour), uses in per_hour.items():
            cells[day.isoweekday() - 1][hour] += uses
            by_hour[hour] += uses
            per_day[day] += uses
        day_uses = {d: None if self._is_future(d) else per_day.get(d, 0) for d in period.dates}
        by_day = [
            {"date": d.isoformat(), "weekday": d.isoweekday(), "uses": u}
            for d, u in day_uses.items()
        ]
        hours = [
            {"hour": h, "uses": None if self._is_future(period.first, h) else by_hour[h]}
            if period.hourly
            else {"hour": h, "uses": by_hour[h]}
            for h in range(24)
        ]
        past = {d: u for d, u in day_uses.items() if u}
        busiest = None
        if not period.hourly and past:
            top = max(past, key=lambda d: (past[d], -d.toordinal()))  # ties: earliest day
            busiest = {"date": top.isoformat(), "uses": past[top]}
        return {
            "range": self._range(period),
            "peak_hours": peak_hours(by_hour),
            "busiest_day": busiest,
            "weekend_uses": sum(cells[5]) + sum(cells[6]),
            "by_day": by_day,
            "by_hour": hours,
            "by_week": _weeks(period, per_day) if period.days > 7 else [],  # noqa: PLR2004
            "heatmap": {"weekdays": list(range(1, 8)), "hours": list(range(24)), "cells": cells},
        }

    # ---------------------------------------------------------- quality

    async def quality(self, period: Period) -> dict[str, Any]:
        """``GET /admin/stats/quality``."""
        cur, prev = await self._pair(period)
        task, task_prev = cur.metrics(TASK_FEATURES), prev.metrics(TASK_FEATURES)
        everything = cur.metrics(USE_FEATURES)
        speed_src = cur.metrics(USE_FEATURES - {"sos"})
        rate_now, rate_before = cur.completion(), prev.completion()
        first_now = rate(task["first_try_success"], task["uses"])
        first_before = rate(task_prev["first_try_success"], task_prev["uses"])
        total = everything["uses"]
        outcomes = [
            {
                "key": k,
                "uses": everything["outcomes"].get(k, 0),
                "share_pct": rate(everything["outcomes"].get(k, 0), total),
            }
            for k in OUTCOME_KEYS
        ]
        attempt_counts = {
            "reask": everything["signals"].get("reprompt", 0),
            "image_rejected": everything["image_rejected"],
            "interrupted": everything["signals"].get("interrupted", 0),
        }
        attempts_total = sum(attempt_counts.values())
        return {
            "range": self._range(period),
            "completion": {
                "rate": rate_now,
                "rate_change_points": points(rate_now, rate_before),
                "first_try_rate": first_now,
                "first_try_change_points": points(first_now, first_before),
                "avg_retries": _avg(task["extra_attempts"], task["tasks_closed"]),
                "abandon_rate": rate(task["outcomes"].get("abandoned", 0), task["uses"]),
            },
            "speed": {
                "first_audio_p50_ms": p_ms(speed_src["first_audio_hist"], 0.5),
                "first_audio_p90_ms": p_ms(speed_src["first_audio_hist"], 0.9),
                "final_result_p50_ms": p_ms(speed_src["time_to_final_hist"], 0.5),
            },
            "total_uses": total,
            "outcomes": outcomes,
            "attempts_total": attempts_total,
            "attempts": [
                {"key": k, "count": v, "share_pct": rate(v, attempts_total)}
                for k, v in attempt_counts.items()
            ],
        }

    # ---------------------------------------------------------- engagement

    async def engagement(self, period: Period) -> dict[str, Any]:
        """``GET /admin/stats/engagement``."""
        cur = await self.window(period)
        lo, hi = period.bounds()
        owners = cur.owners()
        owner_days = {(d, o) for d, o, _ in cur.active if o}
        if period.hourly:
            by_hour = await self._repo.owners_by_hour(lo, hi, VN_NAME)
            daily = [
                {
                    "date": datetime.combine(period.first, time(h), tzinfo=VN).isoformat(),
                    "active_owners": None
                    if self._is_future(period.first, h)
                    else by_hour.get(h, 0),
                }
                for h in range(24)
            ]
        else:
            daily = [
                {
                    "date": d.isoformat(),
                    "active_owners": None if self._is_future(d) else cur.owners_on(d),
                }
                for d in period.dates
            ]
        return {
            "range": self._range(period),
            "active_owners": len(owners),
            "new_users": await self._repo.new_accounts(lo, hi),
            "new_active_users": await self._repo.owners_first_seen_between(
                period.first, period.last
            ),
            "active_glasses": len({dev for _, _, dev in cur.active if dev}),
            "total_glasses": await self._repo.total_glasses(),
            "avg_active_days": _avg(len(owner_days), len(owners)),
            "period_days": period.days,
            "granularity": "hour" if period.hourly else "day",
            "daily": daily,
        }

    # ---------------------------------------------------------- safety

    async def safety(self, period: Period) -> dict[str, Any]:
        """``GET /admin/stats/safety``."""
        cur = await self.window(period)
        sos = cur.metrics(["sos"])
        alert = cur.metrics(["alert_mode"])
        on = alert["session_seconds"].get("alert_mode", 0.0)
        counts = [0] + [alert["alerts_by_level"].get(str(level), 0) for level in (1, 2, 3)]
        shares = share_largest_remainder(counts)
        return {
            "range": self._range(period),
            "sos": {
                "count": sos["uses"],
                "success": sos["outcomes"].get("completed", 0),
                "failed": sos["outcomes"].get("failed", 0),
                "confirm_p50_seconds": p_seconds(sos["sos_to_answer_hist"]),
                "cancelled_within_5s": sos["outcomes"].get("cancelled", 0),
            },
            "alert_live": {
                "alert_mode": "controlled_trial",
                "sessions": alert["sessions"].get("alert_mode", 0),
                "total_on_seconds": round(on),
                "alerts_per_hour": _per_hour(alert, on),
                "levels": [
                    {
                        "level": level,
                        "count": counts[level],
                        "share_pct": shares[level] if shares else None,
                    }
                    for level in range(4)
                ],
            },
        }

    # ---------------------------------------------------------- errors

    async def llm(
        self,
        period: Period,
        *,
        prices_path: str | None,
        free_providers: list[str],
        compare_models: list[str],
        usd_vnd: float | None,
    ) -> dict[str, Any]:
        """``GET /admin/stats/llm``: token totals and their cost (priced now, see llm_cost)."""
        rows = await self._repo.llm_daily(period.first, period.last)
        attempts: dict[str, int] = defaultdict(int)
        for daily in await self._repo.daily(period.first, period.last):
            # usage_daily splits live OCR off as "ocr_live"; LLM rows use the attempt's feature.
            feature = "ocr" if daily.feature == "ocr_live" else daily.feature
            attempts[feature] += int(daily.metrics.get("attempts", 0))
        out = llm_cost.report(
            rows,
            prices=llm_cost.load_prices(prices_path),
            free_providers=free_providers,
            compare_models=compare_models,
            attempts_by_feature=dict(attempts),
            usd_vnd=usd_vnd,
        )
        out["period"] = {
            "name": period.name,
            "from": period.first.isoformat(),
            "to": period.last.isoformat(),
        }
        return out

    async def errors(  # noqa: PLR0913
        self,
        period: Period,
        *,
        group: str | None,
        firmware_version: str | None,
        sort: str,
        order: str,
        page: int,
        page_size: int,
    ) -> dict[str, Any]:
        """``GET /admin/stats/errors`` (one row per error code + feature)."""
        cur, prev = await self._pair(period)
        rows = _error_rows(cur, firmware_version)
        before = _error_rows(prev, firmware_version)
        groups_present = sorted({r["group"] for r in rows}, key=list(GROUP_NAMES.values()).index)
        versions = sorted({fw for r in _error_rows(cur, None) for fw in r["_versions"] if fw})
        if group:
            wanted = GROUP_NAMES.get(group, group)
            rows = [r for r in rows if r["group"] == wanted]
        items = []
        for r in rows:
            prior = next(
                (
                    b["count"]
                    for b in before
                    if (b["code"], b["feature"]) == (r["code"], r["feature"])
                ),
                0,
            )
            items.append(
                {
                    "code": r["code"],
                    "feature": r["feature"],
                    "group": r["group"],
                    "count": r["count"],
                    "change": r["count"] - prior,
                    "last_seen": r["last_seen"],
                    "firmware_version": r["firmware_version"],
                }
            )
        known = [i for i in items if i[sort] is not None]
        known.sort(key=lambda i: (i[sort], i["code"], i["feature"]), reverse=order == "desc")
        items = known + [i for i in items if i[sort] is None]
        start = (page - 1) * page_size
        return {
            "items": items[start : start + page_size],
            "total": len(items),
            "page": page,
            "page_size": page_size,
            "available_groups": groups_present,
            "available_firmware_versions": versions,
        }


# ---------------------------------------------------------------- module helpers


def _done(m: dict[str, Any]) -> int:
    outcomes: dict[str, int] = m["outcomes"]
    return outcomes.get("completed", 0) + outcomes.get("completed_early", 0)


def _per_hour(alert: dict[str, Any], on_seconds: float) -> float | None:
    total = sum(alert["alerts_by_level"].values())
    return round(total / (on_seconds / 3600), 1) if on_seconds > 0 else None


def _ended_avg(m: dict[str, Any], kind: str) -> float | None:
    return _avg(m["session_ended_seconds"].get(kind, 0.0), m["session_ended"].get(kind, 0))


def _details(key: str, m: dict[str, Any]) -> dict[str, Any]:  # noqa: C901, PLR0911 -- one branch per dashboard feature
    signals = m["signals"]
    duration = p_seconds(m["time_to_final_hist"])
    if key == "ocr":
        return {
            "rejected_images": m["image_rejected"],
            "avg_retries": _avg(m["extra_attempts"], m["tasks_closed"]),
            "processing_p50_seconds": p_seconds(m["processing_hist"]),
        }
    if key == "ocr_live":
        return {
            "framing_p50_seconds": p_seconds(m["align_hist"]),
            "avg_session_seconds": _ended_avg(m, "ocr_live"),
        }
    if key == "money":
        return {
            "uncertain_rate_pct": rate(
                signals.get("money_uncertain", 0), signals.get("money_decided", 0)
            ),
            "rejected_images": m["image_rejected"],
        }
    if key in ("ask", "find", "space", "hazard"):
        return {"rejected_images": m["image_rejected"], "duration_p50_seconds": duration}
    if key == "chat":
        return {
            "web_lookup_rate_pct": rate(
                signals.get("needs_web", 0), signals.get("needs_web_decided", 0)
            ),
            "duration_p50_seconds": duration,
        }
    if key == "family":
        return {
            "cancel_in_window_pct": rate(
                signals.get("cancelled_in_window", 0), signals.get("cancel_window_open", 0)
            ),
            "reask_pct": rate(signals.get("reprompt", 0), m["attempts"]),
            "confirm_p50_seconds": duration,
        }
    if key == "internet_call":
        return {
            "answer_rate_pct": rate(m["calls_answered"], m["calls_known"]),
            "avg_call_seconds": _avg(m["call_talk_seconds"], m["calls_answered"]),
            "connection_errors": sum(m["call_errors"].values()),
        }
    if key == "music":
        return {
            "avg_play_seconds": _ended_avg(m, "music"),
            "cancel_rate_pct": rate(m["outcomes"].get("cancelled", 0), m["uses"]),
        }
    if key == "directions":
        return {
            "avg_trip_seconds": _ended_avg(m, "navigation"),
            "cancel_midway_pct": rate(
                m["session_cancelled"].get("navigation", 0), m["session_ended"].get("navigation", 0)
            ),
        }
    if key == "ride":
        return {
            "avg_booking_seconds": duration,
            "cancel_midway_pct": rate(m["outcomes"].get("cancelled", 0), m["uses"]),
        }
    on = m["session_seconds"].get("alert_mode", 0.0)
    return {"total_on_seconds": round(on), "alerts_per_hour": _per_hour(m, on)}


def _error_counts(w: Window) -> dict[tuple[str, str], int]:
    out: dict[tuple[str, str], int] = defaultdict(int)
    for row in w.rows:
        key = DASH_KEY.get(row.feature)
        if key is None:
            continue
        for code, count in row.metrics.get("errors", {}).items():
            out[(code, key)] += count
    return dict(out)


def _error_rows(w: Window, firmware: str | None) -> list[dict[str, Any]]:
    acc: dict[tuple[str, str], dict[str, Any]] = {}
    for row in w.rows:
        key = DASH_KEY.get(row.feature)
        if key is None or (firmware is not None and row.firmware_version != firmware):
            continue
        last = row.metrics.get("last_error_at", {})
        for code, count in row.metrics.get("errors", {}).items():
            item = acc.setdefault(
                (code, key),
                {
                    "code": code,
                    "feature": key,
                    "group": GROUP_NAMES[GROUP_OF[key]],
                    "count": 0,
                    "last_seen": None,
                    "firmware_version": None,
                    "_versions": set(),
                },
            )
            item["count"] += count
            item["_versions"].add(row.firmware_version)
            seen = last.get(code)
            if seen is not None and (item["last_seen"] is None or seen > item["last_seen"]):
                item["last_seen"] = seen
                item["firmware_version"] = row.firmware_version or None
    for item in acc.values():
        item["last_seen"] = _vn_iso(item["last_seen"])
    return list(acc.values())


def peak_hours(by_hour: list[int]) -> dict[str, int | None]:
    """Busiest run of hours (≥ 60 % of the peak) and a second one elsewhere; ``end`` exclusive."""
    empty: dict[str, int | None] = {
        "start_hour": None,
        "end_hour": None,
        "secondary_start_hour": None,
        "secondary_end_hour": None,
    }
    if not any(by_hour):
        return empty

    def run(counts: list[int]) -> tuple[int, int, int]:
        top = max(range(24), key=lambda h: (counts[h], -h))
        floor = 0.6 * counts[top]
        start, end = top, top + 1
        while start > 0 and counts[start - 1] >= floor and counts[start - 1] > 0:
            start -= 1
        while end < 24 and counts[end] >= floor and counts[end] > 0:  # noqa: PLR2004
            end += 1
        return start, end, counts[top]

    s1, e1, _ = run(by_hour)
    rest = [0 if s1 - 1 <= h <= e1 else c for h, c in enumerate(by_hour)]
    out = {**empty, "start_hour": s1, "end_hour": e1}
    if any(rest):
        s2, e2, _ = run(rest)
        out["secondary_start_hour"], out["secondary_end_hour"] = s2, e2
    return out


def _weeks(period: Period, per_day: dict[date, int]) -> list[dict[str, Any]]:
    weeks: list[dict[str, Any]] = []
    for d in period.dates:
        if not weeks or d.isoweekday() == 1:
            weeks.append({"week_start": d.isoformat(), "week_end": d.isoformat(), "uses": 0})
        weeks[-1]["week_end"] = d.isoformat()
        weeks[-1]["uses"] += per_day.get(d, 0)
    return weeks


async def owner_stats(
    session: AsyncSession, period: Period, *, id_key: bytes, public_user_id: str
) -> dict[str, Any]:
    """``GET /me/stats``: one owner's own numbers (filtered by pseudonym before merging)."""
    owner = pseudonym(id_key, "owner", public_user_id)
    w = await AdminStatsService(session, id_key=id_key).window(period, owner_hash=owner)
    task = w.metrics(TASK_FEATURES)
    conn = w.metrics([""])
    alert = w.metrics(["alert_mode"])
    calls = w.metrics(["internet_call"])
    features = [
        {
            "key": key,
            "uses": w.uses(raws),
            "completion_rate": None if key in NON_TASK else w.completion(raws),
        }
        for feats in GROUPS.values()
        for key, raws, _ in feats
    ]
    return {
        "range": {"from": period.first.isoformat(), "to": period.last.isoformat()},
        "totals": {
            "uses": w.uses(),
            "completion_rate": w.completion(),
            "first_try_rate": rate(task["first_try_success"], task["uses"]),
            "avg_retries": _avg(task["extra_attempts"], task["tasks_closed"]),
            "usage_seconds": w.usage_seconds(),
            "connection_seconds": round(conn["session_seconds"].get("connection", 0.0)),
            "alert_mode_seconds": round(alert["session_seconds"].get("alert_mode", 0.0)),
            "active_days": len({d for d, _, _ in w.active}),
            "internet_calls": calls["calls"],
            "internet_calls_answered": calls["calls_answered"],
            "sos": w.uses(["sos"]),
        },
        "features": [f for f in features if f["uses"]],
        "by_day": [
            {"date": d.isoformat(), "uses": w.day_metrics(d, USE_FEATURES)["uses"]}
            for d in period.dates
        ],
    }
