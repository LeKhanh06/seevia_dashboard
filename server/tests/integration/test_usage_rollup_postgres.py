"""PostgreSQL integration tests for the usage rollup worker (layer 3)."""

from __future__ import annotations

import asyncio
from datetime import UTC, date, datetime, timedelta
from typing import TYPE_CHECKING
from uuid import uuid4

from sqlalchemy import select, text

from app.models.usage import UsageActiveDay, UsageAttempt, UsageDaily, UsageHourly
from app.workers.usage_rollup import UsageRollupWorker
from tests.integration.usage_helpers import (
    attempt,
    ev,
    post_events,
    task_done,
    usage_client,
    usage_settings,
    vn,
)

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from app.database import AsyncSessionFactory


def _worker(factory: AsyncSessionFactory) -> UsageRollupWorker:
    return UsageRollupWorker(factory, usage_settings("unused"))


async def _sum(db: AsyncSession, day: date, key: str) -> int:
    rows = (await db.execute(select(UsageDaily.metrics).where(UsageDaily.day == day))).scalars()
    return sum(int(str(m.get(key, 0))) for m in rows)


async def test_rollup_recomputes_dirty_day(
    db_session: AsyncSession, postgres_session_factory: AsyncSessionFactory
) -> None:
    """Ingest -> dirty day -> worker builds daily/hourly/active rows and clears the mark."""
    _, task_id, events = attempt(
        "ocr", vn("2026-11-16 09:00:00"), vn("2026-11-16 09:00:05"), "success"
    )
    events.append(
        task_done("ocr", task_id, vn("2026-11-16 09:00:05"), status="success", attempts=1)
    )
    async with usage_client(postgres_session_factory) as (client, token):
        await post_events(client, token, events)

    assert await _worker(postgres_session_factory).run_once() == 1

    day = date(2026, 11, 16)
    assert await _sum(db_session, day, "attempts") == 1
    assert await _sum(db_session, day, "tasks_closed") == 1
    hourly = (await db_session.execute(select(UsageHourly))).scalars().all()
    assert [(h.hour, h.feature, h.uses, h.attempts) for h in hourly] == [(9, "ocr", 1, 1)]
    assert len((await db_session.execute(select(UsageActiveDay))).scalars().all()) == 1
    assert await db_session.scalar(text("SELECT count(*) FROM usage_dirty_days")) == 0
    assert await _worker(postgres_session_factory).run_once() == 0


async def test_late_result_updates_previous_day(
    db_session: AsyncSession, postgres_session_factory: AsyncSessionFactory
) -> None:
    """A result arriving after the rollup recomputes the start day (pending -> closed)."""
    _, task_id, events = attempt(
        "call", vn("2026-11-16 23:59:00"), vn("2026-11-17 00:01:00"), "success"
    )
    async with usage_client(postgres_session_factory) as (client, token):
        await post_events(client, token, events)
        worker = _worker(postgres_session_factory)
        # Đồng hồ cố định ngay sau sự kiện: đồng hồ thật > 30 phút sau thì lượt bị quét UNCLOSED.
        clock = vn("2026-11-17 00:05:00")
        await worker.run_once(now=clock)
        day = date(2026, 11, 16)
        assert await _sum(db_session, day, "tasks_pending") == 1
        await post_events(
            client,
            token,
            [task_done("call", task_id, vn("2026-11-17 00:01:00"), status="success", attempts=1)],
        )
        await worker.run_once(now=clock)
    await db_session.rollback()  # fresh snapshot
    assert await _sum(db_session, day, "tasks_pending") == 0
    assert await _sum(db_session, day, "tasks_closed") == 1
    assert await _sum(db_session, date(2026, 11, 17), "tasks_closed") == 0


async def test_unclosed_swept_after_30min(
    db_session: AsyncSession, postgres_session_factory: AsyncSessionFactory
) -> None:
    """VH died mid-attempt: after 30 minutes the attempt becomes technical_error / UNCLOSED."""
    started = datetime.now(UTC) - timedelta(minutes=45)
    attempt_id = uuid4()
    event = ev("attempt_started", started, attempt_id=attempt_id, task_id=uuid4(), feature="chat")
    async with usage_client(postgres_session_factory) as (client, token):
        await post_events(client, token, [event])
    await _worker(postgres_session_factory).run_once()

    row = (await db_session.execute(select(UsageAttempt))).scalar_one()
    assert (row.status, row.error_code) == ("technical_error", "UNCLOSED")


async def test_two_workers_do_not_double_count(
    db_session: AsyncSession, postgres_session_factory: AsyncSessionFactory
) -> None:
    """Two workers racing on the same dirty days still yield one set of rows per day."""
    events = []
    for day in range(10, 16):
        _, _, more = attempt(
            "chat", vn(f"2026-11-{day} 10:00:00"), vn(f"2026-11-{day} 10:00:04"), "success"
        )
        events += more
    async with usage_client(postgres_session_factory) as (client, token):
        await post_events(client, token, events)

    first, second = _worker(postgres_session_factory), _worker(postgres_session_factory)
    await asyncio.gather(first.run_once(), second.run_once())
    await first.run_once()

    total = 0
    for day in range(10, 16):
        total += await _sum(db_session, date(2026, 11, day), "attempts")
    assert total == 6
    per_day = await db_session.scalar(
        text(
            "SELECT max(n) FROM (SELECT count(*) n FROM usage_daily GROUP BY day, owner_hash,"
            " device_hash, feature, component_version, firmware_version) x"
        )
    )
    assert per_day == 1
