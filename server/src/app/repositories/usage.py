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

from sqlalchemy import case, func, select
from sqlalchemy.dialects.postgresql import insert

from app.models import GlassesDevice, GlassesLinkStatus
from app.models.usage import (
    UsageAttempt,
    UsageDirtyDay,
    UsageEvent,
    UsageSession,
    UsageTask,
)

if TYPE_CHECKING:
    from zoneinfo import ZoneInfo

    from sqlalchemy import Table
    from sqlalchemy.ext.asyncio import AsyncSession
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

    async def mark_dirty(self, days: set[date]) -> None:
        """Queue report days for the rollup worker (re-marking refreshes ``marked_at``)."""
        if not days:
            return
        stmt = insert(UsageDirtyDay).values([{"day": day} for day in sorted(days)])
        stmt = stmt.on_conflict_do_update(
            index_elements=[UsageDirtyDay.day], set_={"marked_at": func.clock_timestamp()}
        )
        await self._session.execute(stmt)

    async def _upsert(  # noqa: PLR0913 -- one generic statement builder for three tables
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
