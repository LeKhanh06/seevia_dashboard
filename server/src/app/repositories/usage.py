"""Usage-statistics persistence: layer-1 insert, order-independent layer-2 upserts, dirty days.

Every layer-2 statement is an ``INSERT ... ON CONFLICT DO UPDATE`` that only fills empty columns
(``COALESCE(existing, new)``), widens time bounds (``LEAST`` / ``GREATEST``) or adds counters, so
applying the same set of events in any order yields the same row. Final outcomes (status, error
code, finish time) are written once: the first one stored wins. The ingest service calls the
layer-2 methods only for events whose layer-1 insert was new, so a resent batch changes nothing.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from enum import Enum, auto
from typing import TYPE_CHECKING, Any, cast

from sqlalchemy import String, and_, case, delete, func, or_, select, text, true
from sqlalchemy import cast as sql_cast
from sqlalchemy.dialects.postgresql import insert

from app.models import GlassesDevice, GlassesLinkStatus
from app.models.usage import (
    UsageActiveDay,
    UsageAttempt,
    UsageCall,
    UsageDaily,
    UsageDirtyDay,
    UsageEvent,
    UsageHourly,
    UsageSession,
    UsageSos,
    UsageTask,
)
from app.repositories.usage_rows import (
    AlertRow,
    AttemptRow,
    CallRow,
    DayResult,
    Layer2Rows,
    SessionRow,
    SosRow,
    TaskRow,
)

if TYPE_CHECKING:
    from zoneinfo import ZoneInfo

    from sqlalchemy import Table
    from sqlalchemy.ext.asyncio import AsyncSession
    from sqlalchemy.sql import Select
    from sqlalchemy.sql.elements import ColumnElement

    from app.database import Base


class Merge(Enum):
    """How one column of an existing layer-2 row absorbs a new value."""

    FILL = auto()  # COALESCE(existing, new)
    EARLIEST = auto()  # LEAST
    LATEST = auto()  # GREATEST
    ADD_COUNTS = auto()  # per-key integer sum of two JSONB objects
    JSON_MERGE = auto()  # existing || new
    ONCE = auto()  # written only while the row's final marker column is NULL


_ATTEMPT_RULES = {
    "started_at": Merge.EARLIEST,
    "signals": Merge.ADD_COUNTS,
    "status": Merge.ONCE,
    "finished_at": Merge.ONCE,
    "error_code": Merge.ONCE,
    "playback": Merge.ONCE,
}
_TASK_RULES = {
    "started_at": Merge.EARLIEST,
    "status": Merge.ONCE,
    "finished_at": Merge.ONCE,
    "attempts": Merge.ONCE,
}
_SESSION_RULES = {
    "started_at": Merge.EARLIEST,
    "last_seen_at": Merge.LATEST,
    "alerts_by_level": Merge.ADD_COUNTS,
    "device_info": Merge.JSON_MERGE,
    "ended_at": Merge.ONCE,
    "end_reason": Merge.ONCE,
}

# AB's own call/SOS rows: the final state (and its time/reason) is written once.
_CALL_RULES = {
    "started_at": Merge.EARLIEST,
    "final_state": Merge.ONCE,
    "ended_at": Merge.ONCE,
    "end_reason": Merge.ONCE,
}
_SOS_RULES = {
    "started_at": Merge.EARLIEST,
    "status": Merge.ONCE,
    "finished_at": Merge.ONCE,
    "error_code": Merge.ONCE,
}


def report_day(moment: datetime, tz: ZoneInfo) -> date:
    """Return the calendar day of ``moment`` in the report time zone."""
    return moment.astimezone(tz).date()


def days_between(start: datetime, end: datetime, tz: ZoneInfo) -> list[date]:
    """Return every report day touched by ``[start, end]`` (inclusive)."""
    first, last = report_day(start, tz), report_day(max(start, end), tz)
    return [first + timedelta(days=i) for i in range((last - first).days + 1)]


def _merged(
    rule: Merge,
    column: ColumnElement[Any],
    new: ColumnElement[Any],
    final_marker: ColumnElement[Any],
) -> ColumnElement[Any]:
    if rule is Merge.EARLIEST:
        return func.least(column, new)
    if rule is Merge.LATEST:
        return func.greatest(column, new)
    if rule is Merge.ADD_COUNTS:
        # SQL function created by migration 20261116_0013.
        return func.usage_add_counts(column, new)
    if rule is Merge.JSON_MERGE:
        return column.op("||")(new)
    if rule is Merge.ONCE:
        return case((final_marker.is_(None), new), else_=column)
    return func.coalesce(column, new)


class UsageRepository:
    """Queries and writes for the usage tables of one transaction."""

    def __init__(self, session: AsyncSession) -> None:
        """Bind the repository to a transaction-scoped session."""
        self._session = session

    async def active_owner(self, device_id: str) -> str | None:
        """Return the ``public_user_id`` currently paired with ``device_id``, if any."""
        owner: str | None = await self._session.scalar(
            select(GlassesDevice.user_id).where(
                GlassesDevice.device_id == device_id,
                GlassesDevice.status == GlassesLinkStatus.ACTIVE,
            )
        )
        return owner

    async def insert_event(self, row: dict[str, Any]) -> bool:
        """Insert one layer-1 event; ``False`` when ``event_id`` was already stored."""
        stmt = (
            insert(UsageEvent)
            .values(**row)
            .on_conflict_do_nothing(index_elements=[UsageEvent.event_id])
            .returning(UsageEvent.event_id)
        )
        return (await self._session.execute(stmt)).first() is not None

    async def upsert_attempt(self, values: dict[str, Any]) -> datetime | None:
        """Merge attempt columns; return the row's ``started_at`` (may still be unknown)."""
        row = await self._upsert(UsageAttempt, "attempt_id", "status", _ATTEMPT_RULES, values)
        return cast("datetime | None", row[0])

    async def upsert_task(self, values: dict[str, Any]) -> datetime | None:
        """Merge task columns; return the task's ``started_at``."""
        row = await self._upsert(UsageTask, "task_id", "status", _TASK_RULES, values)
        return cast("datetime | None", row[0])

    async def upsert_session(
        self, values: dict[str, Any]
    ) -> tuple[datetime | None, datetime | None]:
        """Merge session columns; return ``(started_at, end or latest heartbeat)``."""
        row = await self._upsert(
            UsageSession, "session_id", "ended_at", _SESSION_RULES, values, immutable={"kind"}
        )
        started, ended, last_seen = row
        return cast("datetime | None", started), cast("datetime | None", ended or last_seen)

    async def upsert_call(self, values: dict[str, Any]) -> datetime | None:
        """Merge one Internet call's columns; return its ``started_at``."""
        row = await self._upsert(
            UsageCall,
            "call_id",
            "final_state",
            _CALL_RULES,
            values,
            immutable={"direction", "emergency"},
        )
        return cast("datetime | None", row[0])

    async def upsert_sos(self, values: dict[str, Any]) -> datetime | None:
        """Merge one SOS activation's columns; return its ``started_at``."""
        row = await self._upsert(UsageSos, "sos_id", "status", _SOS_RULES, values)
        return cast("datetime | None", row[0])

    async def mark_dirty(self, days: set[date]) -> None:
        """Queue report days for the rollup worker (re-marking refreshes ``marked_at``)."""
        if not days:
            return
        stmt = insert(UsageDirtyDay).values([{"day": day} for day in sorted(days)])
        stmt = stmt.on_conflict_do_update(
            index_elements=[UsageDirtyDay.day], set_={"marked_at": func.clock_timestamp()}
        )
        await self._session.execute(stmt)

    async def _upsert(  # noqa: PLR0913 -- one generic statement builder for all layer-2 tables
        self,
        model: type[Base],
        key: str,
        final_marker: str,
        rules: dict[str, Merge],
        values: dict[str, Any],
        *,
        immutable: frozenset[str] | set[str] = frozenset(),
    ) -> tuple[Any, ...]:
        table = cast("Table", model.__table__)
        stmt = insert(table).values(**values)
        updates = {
            name: _merged(
                rules.get(name, Merge.FILL),
                table.c[name],
                stmt.excluded[name],
                table.c[final_marker],
            )
            for name in values
            if name != key and name not in immutable
        }
        returning = [table.c.started_at]
        if model is UsageSession:
            returning += [table.c.ended_at, table.c.last_seen_at]
        upsert = stmt.on_conflict_do_update(index_elements=[table.c[key]], set_=updates)
        return tuple((await self._session.execute(upsert.returning(*returning))).one())


_SWEEP_ATTEMPTS_SQL = (
    "UPDATE usage_attempts SET status = 'technical_error', error_code = 'UNCLOSED',"
    " finished_at = :now"
    " WHERE finished_at IS NULL AND status IS NULL AND started_at < :cut"
    " RETURNING started_at"
)
_SWEEP_TASKS_SQL = (
    "UPDATE usage_tasks SET status = 'technical_error', finished_at = :now,"
    " attempts = coalesce(attempts, (SELECT count(*) FROM usage_attempts a"
    " WHERE a.task_id = usage_tasks.task_id))"
    " WHERE finished_at IS NULL AND status IS NULL AND started_at < :cut"
    " RETURNING started_at"
)
_CLAIM_DIRTY_SQL = (
    "UPDATE usage_dirty_days SET locked_until = :until WHERE day IN ("
    " SELECT day FROM usage_dirty_days"
    " WHERE locked_until IS NULL OR locked_until < :now"
    " ORDER BY day LIMIT :limit FOR UPDATE SKIP LOCKED)"
    " RETURNING day, marked_at"
)


class UsageReadRepository:
    """Layer-2 reads and layer-3 replacement for the rollup worker and exact queries."""

    def __init__(self, session: AsyncSession) -> None:
        """Bind to a transaction-scoped session."""
        self._session = session

    async def layer2(self, lo: datetime, hi: datetime, owner_hash: str | None = None) -> Layer2Rows:
        """Return every layer-2 row touching ``[lo, hi)``; ``owner_hash`` narrows to one owner."""
        a, t, se, ev = UsageAttempt, UsageTask, UsageSession, UsageEvent
        c, so = UsageCall, UsageSos

        def mine(model: type[Base]) -> list[ColumnElement[bool]]:
            if owner_hash is None:
                return []
            return [getattr(model, "owner_hash") == owner_hash]  # noqa: B009 -- shared column name

        rows = Layer2Rows()
        attempts = select(
            sql_cast(a.task_id, String),
            a.feature,
            a.owner_hash,
            a.device_hash,
            a.component_version,
            a.firmware_version,
            a.started_at,
            a.first_audio_at,
            a.finished_at,
            a.status,
            a.error_code,
            a.processing_ms,
            a.align_ms,
            a.signals,
        ).where(a.started_at >= lo, a.started_at < hi, *mine(a))
        rows.attempts = [AttemptRow(*r) for r in await self._all(attempts)]

        first = (
            select(a.component_version, a.firmware_version)
            .where(a.task_id == t.task_id)
            .order_by(a.started_at)
            .limit(1)
            .lateral("first_attempt")
        )
        tasks = (
            select(
                t.feature,
                t.owner_hash,
                t.device_hash,
                first.c.component_version,
                first.c.firmware_version,
                t.started_at,
                t.status,
                t.attempts,
                sql_cast(t.task_id, String),
            )
            .outerjoin(first, true())
            .where(t.started_at >= lo, t.started_at < hi, *mine(t))
        )
        rows.tasks = [TaskRow(*r) for r in await self._all(tasks)]

        sessions = select(
            se.kind,
            se.owner_hash,
            se.device_hash,
            se.firmware_version,
            se.started_at,
            se.last_seen_at,
            se.ended_at,
            se.end_reason,
            se.align_ms,
        ).where(
            se.started_at < hi,
            func.coalesce(se.ended_at, se.last_seen_at, se.started_at) >= lo,
            *mine(se),
        )
        rows.sessions = [SessionRow(*r) for r in await self._all(sessions)]

        alerts = select(
            ev.owner_hash, ev.device_hash, ev.occurred_at, ev.body["level"].as_integer()
        ).where(ev.type == "alert_raised", ev.occurred_at >= lo, ev.occurred_at < hi, *mine(ev))
        rows.alerts = [AlertRow(*r) for r in await self._all(alerts)]

        calls = select(
            c.owner_hash,
            c.device_hash,
            c.started_at,
            c.answered_at,
            c.ended_at,
            c.final_state,
            c.end_reason,
            c.direction,
        ).where(
            c.emergency.is_(False),
            or_(
                and_(c.started_at >= lo, c.started_at < hi),
                and_(c.answered_at < hi, c.ended_at >= lo),
            ),
            *mine(c),
        )
        rows.calls = [CallRow(*r) for r in await self._all(calls)]

        sos = select(
            so.owner_hash,
            so.device_hash,
            so.started_at,
            so.answered_at,
            so.finished_at,
            so.status,
            so.error_code,
        ).where(so.started_at >= lo, so.started_at < hi, *mine(so))
        rows.sos = [SosRow(*r) for r in await self._all(sos)]
        return rows

    async def _all(self, stmt: Select[Any]) -> list[tuple[Any, ...]]:
        return [tuple(row) for row in (await self._session.execute(stmt)).all()]

    async def sweep_unclosed(self, cutoff: datetime, now: datetime) -> set[datetime]:
        """Close rows still open since before ``cutoff`` as ``UNCLOSED``; return their starts."""
        starts: set[datetime] = set()
        for sql in (_SWEEP_ATTEMPTS_SQL, _SWEEP_TASKS_SQL):
            result = await self._session.execute(text(sql), {"now": now, "cut": cutoff})
            starts.update(row[0] for row in result.all())
        return starts

    async def claim_dirty(
        self, now: datetime, lease_s: float, limit: int
    ) -> list[tuple[date, datetime]]:
        """Lease up to ``limit`` dirty days (``FOR UPDATE SKIP LOCKED``)."""
        result = await self._session.execute(
            text(_CLAIM_DIRTY_SQL),
            {"now": now, "until": now + timedelta(seconds=lease_s), "limit": limit},
        )
        return [(row[0], row[1]) for row in result.all()]

    async def replace_day(self, day: date, result: DayResult) -> None:
        """Swap every layer-3 row of ``day`` for ``result``."""
        for model in (UsageDaily, UsageHourly, UsageActiveDay):
            await self._session.execute(delete(model).where(model.day == day))
        if result.daily:
            await self._session.execute(
                insert(UsageDaily),
                [
                    {
                        "day": day,
                        "owner_hash": key[0],
                        "device_hash": key[1],
                        "feature": key[2],
                        "component_version": key[3],
                        "firmware_version": key[4],
                        "metrics": metrics,
                    }
                    for key, metrics in result.daily.items()
                ],
            )
        if result.hourly:
            await self._session.execute(
                insert(UsageHourly),
                [
                    {
                        "day": day,
                        "hour": hour,
                        "feature": feature,
                        "uses": uses,
                        "attempts": attempts,
                    }
                    for (hour, feature), (uses, attempts) in result.hourly.items()
                ],
            )
        if result.active:
            await self._session.execute(
                insert(UsageActiveDay),
                [{"day": day, "owner_hash": o, "device_hash": d} for o, d in result.active],
            )

    async def release_dirty(self, day: date, marked_at: datetime) -> None:
        """Drop the dirty mark unless the day was re-marked while it was being computed."""
        await self._session.execute(
            text("DELETE FROM usage_dirty_days WHERE day = :day AND marked_at = :marked"),
            {"day": day, "marked": marked_at},
        )
        await self._session.execute(
            text("UPDATE usage_dirty_days SET locked_until = NULL WHERE day = :day"), {"day": day}
        )
