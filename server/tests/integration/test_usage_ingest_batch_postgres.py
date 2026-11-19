"""Batched usage ingest on PostgreSQL: few round trips, one-by-one parity, conflict fallback."""

from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from sqlalchemy import event, text

from tests.integration.conftest import _truncate_mutable_tables
from tests.integration.usage_helpers import attempt, ev, post_events, task_done, usage_client, vn

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

    from app.database import AsyncSessionFactory

_SNAPSHOT = {
    "usage_events": (
        "SELECT event_id, type, occurred_at, owner_hash, device_hash, body FROM usage_events"
    ),
    "usage_attempts": "SELECT * FROM usage_attempts",
    "usage_tasks": "SELECT * FROM usage_tasks",
    "usage_sessions": "SELECT * FROM usage_sessions",
    "usage_dirty_days": "SELECT day FROM usage_dirty_days",
}


async def _snapshot(db: AsyncSession) -> dict[str, list[Any]]:
    out = {}
    for name, sql in _SNAPSHOT.items():
        rows = (await db.execute(text(sql))).mappings().all()
        out[name] = sorted((tuple(sorted(dict(r).items())) for r in rows), key=repr)
    return out


def _mixed() -> list[dict[str, Any]]:
    """Every layer-2 kind, a repeated ``event_id``, finish-before-start and summed signals."""
    t0 = vn("2026-11-18 09:00:00")
    out: list[dict[str, Any]] = []
    task = uuid4()
    for i in range(3):  # retake: three attempts, one task
        start = t0 + timedelta(seconds=10 * i)
        _, _, evs = attempt(
            "ocr",
            start,
            start + timedelta(seconds=4),
            "success" if i == 2 else "rejected_image",
            task_id=task,
            first_audio=start + timedelta(seconds=1),
            signals=("image_rejected", "reprompt"),
        )
        out += evs
    out.append(task_done("ocr", task, t0 + timedelta(seconds=30), status="success", attempts=3))
    late_id, _, late = attempt("money", t0, t0 + timedelta(seconds=2), "uncertain")
    out += [late[-1], *late[:-1]]  # finish arrives before start
    out.append(
        ev(
            "attempt_signal",
            t0,
            attempt_id=late_id,
            signal="money_uncertain",
            reason_code="step_back",
        )
    )
    out.append(out[0])  # same event_id twice in one batch
    sid = uuid4()
    out += [
        ev("session_started", t0, session_id=sid, kind="alert_mode"),
        ev("session_heartbeat", t0 + timedelta(seconds=60), session_id=sid, kind="alert_mode"),
        ev("alert_raised", t0 + timedelta(seconds=61), session_id=sid, level=3),
        ev(
            "session_ended",
            t0 + timedelta(seconds=90),
            session_id=sid,
            kind="alert_mode",
            end_reason="user_off",
        ),
    ]
    conn = uuid4()
    out += [
        ev(
            "session_started",
            t0,
            session_id=conn,
            kind="connection",
            device_info={"rssi": -60, "reset_reason": "POWERON"},
        ),
        ev(
            "session_ended",
            t0 + timedelta(hours=26),
            session_id=conn,
            kind="connection",
            end_reason="closed",
        ),
    ]
    return out


async def test_batch_equals_one_by_one(
    db_session: AsyncSession,
    postgres_engine: AsyncEngine,
    postgres_session_factory: AsyncSessionFactory,
) -> None:
    """One batch leaves layers 1-2 and the counts exactly as sending each event alone."""
    events = _mixed()
    async with usage_client(postgres_session_factory) as (client, token):
        whole = await post_events(client, token, events)
        batch_rows = await _snapshot(db_session)
        await db_session.rollback()
        await _truncate_mutable_tables(postgres_engine)
        singles = {"accepted": 0, "duplicates": 0, "rejected": []}
        for one in events:
            got = await post_events(client, token, [one])
            singles["accepted"] += got["accepted"]
            singles["duplicates"] += got["duplicates"]
            singles["rejected"] += got["rejected"]
        single_rows = await _snapshot(db_session)
    assert whole == singles
    assert whole["duplicates"] == 1
    assert whole["rejected"] == []
    assert batch_rows == single_rows


async def test_batch_statement_budget(
    db_session: AsyncSession,  # noqa: ARG001 -- truncates the tables after the test
    postgres_engine: AsyncEngine,
    postgres_session_factory: AsyncSessionFactory,
) -> None:
    """500 events cost ~1 statement each (was ~4: SAVEPOINT, INSERT, upsert, RELEASE)."""
    t0 = vn("2026-11-18 10:00:00")
    events: list[dict[str, Any]] = []
    for i in range(100):
        start = t0 + timedelta(seconds=7 * i)
        _, task, evs = attempt(
            "ocr",
            start,
            start + timedelta(seconds=3),
            "success",
            device=f"glasses-{i % 3}",
            first_audio=start + timedelta(seconds=1),
            signals=("image_rejected",),
        )
        events += [
            *evs,
            task_done("ocr", task, start + timedelta(seconds=3), status="success", attempts=1),
        ]
    events = events[:500]
    count = 0

    def _count(*_: object) -> None:
        nonlocal count
        count += 1

    async with usage_client(postgres_session_factory) as (client, token):
        await post_events(client, token, events[:1])  # warm-up (metadata, caches)
        event.listen(postgres_engine.sync_engine, "before_cursor_execute", _count)
        try:
            result = await post_events(client, token, events)
        finally:
            event.remove(postgres_engine.sync_engine, "before_cursor_execute", _count)
    assert result["accepted"] == 499
    assert result["duplicates"] == 1
    assert count <= 1.3 * len(events), count


async def test_conflict_in_big_batch_rejects_only_that_event(
    db_session: AsyncSession, postgres_session_factory: AsyncSessionFactory
) -> None:
    """A request_id clash inside a large batch rejects that event; every other event lands."""
    request_id = uuid4()
    at = vn("2026-11-18 12:00:00")
    first = ev(
        "attempt_finished",
        at,
        attempt_id=uuid4(),
        task_id=uuid4(),
        status="success",
        request_id=request_id,
    )
    clash = ev(
        "attempt_finished",
        at,
        attempt_id=uuid4(),
        task_id=uuid4(),
        status="success",
        request_id=request_id,
    )
    others = []
    for i in range(20):
        _, _, evs = attempt(
            "chat", at + timedelta(seconds=i), at + timedelta(seconds=i + 1), "success"
        )
        others += evs
    async with usage_client(postgres_session_factory) as (client, token):
        result = await post_events(client, token, [first, *others[:20], clash, *others[20:]])
    assert result["accepted"] == 1 + len(others)
    assert result["rejected"] == [{"event_id": clash["event_id"], "code": "CONFLICT"}]
    assert await db_session.scalar(text("SELECT count(*) FROM usage_events")) == 1 + len(others)
    assert (
        await db_session.scalar(text("SELECT count(*) FROM usage_attempts")) == 1 + len(others) // 2
    )
