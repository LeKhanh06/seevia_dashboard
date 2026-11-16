"""PostgreSQL checks for the usage-statistics schema (migration 20261116_0013)."""

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import UsageAttempt, UsageEvent, UsageSession

_NOW = datetime(2026, 11, 16, 3, 0, tzinfo=UTC)


async def test_event_id_conflict_inserts_nothing(db_session: AsyncSession) -> None:
    """Resending the same event_id is a no-op (layer-1 dedup)."""
    event_id = uuid4()
    row = {"event_id": event_id, "type": "attempt_started", "occurred_at": _NOW, "body": {}}
    stmt = insert(UsageEvent).values(**row).on_conflict_do_nothing().returning(UsageEvent.event_id)
    first = (await db_session.execute(stmt)).all()
    second = (await db_session.execute(stmt)).all()
    await db_session.commit()

    assert len(first) == 1
    assert second == []
    count = await db_session.scalar(text("SELECT count(*) FROM usage_events"))
    assert count == 1


async def test_attempt_request_id_unique(db_session: AsyncSession) -> None:
    """Two attempts cannot claim the same phone request_id."""
    request_id = uuid4()
    db_session.add(UsageAttempt(attempt_id=uuid4(), request_id=request_id))
    await db_session.flush()
    db_session.add(UsageAttempt(attempt_id=uuid4(), request_id=request_id))
    with pytest.raises(IntegrityError):
        await db_session.flush()
    await db_session.rollback()


async def test_session_kind_checked(db_session: AsyncSession) -> None:
    """Unknown session kinds are rejected by the database."""
    db_session.add(UsageSession(session_id=uuid4(), kind="selfie"))
    with pytest.raises(IntegrityError):
        await db_session.flush()
    await db_session.rollback()


async def test_jsonb_defaults(db_session: AsyncSession) -> None:
    """Signals / alerts_by_level / device_info default to empty objects."""
    attempt_id, session_id = uuid4(), uuid4()
    db_session.add(UsageAttempt(attempt_id=attempt_id))
    db_session.add(UsageSession(session_id=session_id, kind="connection"))
    await db_session.commit()

    signals = await db_session.scalar(
        text("SELECT signals FROM usage_attempts WHERE attempt_id = :id"), {"id": attempt_id}
    )
    info = await db_session.scalar(
        text("SELECT alerts_by_level || device_info FROM usage_sessions WHERE session_id = :id"),
        {"id": session_id},
    )
    assert signals == {}
    assert info == {}
