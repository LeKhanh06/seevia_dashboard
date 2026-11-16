"""PostgreSQL integration tests for ``POST /api/v1/usage/events`` (layers 1 and 2)."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import TYPE_CHECKING
from uuid import uuid4

from sqlalchemy import select, text

from app.models import GlassesDevice, GlassesLinkStatus
from app.models.usage import UsageAttempt, UsageDirtyDay, UsageSession, UsageTask
from app.services.usage_ids import pseudonym
from tests.integration.usage_helpers import (
    ID_KEY,
    attempt,
    ev,
    post_events,
    task_done,
    usage_client,
    vn,
)

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from app.database import AsyncSessionFactory

KEY = bytes.fromhex(ID_KEY)


async def _pair(db: AsyncSession, device_id: str, user_id: str) -> None:
    now = datetime.now(UTC)
    db.add(
        GlassesDevice(
            user_id=user_id, device_id=device_id, status=GlassesLinkStatus.ACTIVE, linked_at=now
        )
    )
    await db.commit()


async def test_resend_same_batch_counts_duplicates(
    db_session: AsyncSession, postgres_session_factory: AsyncSessionFactory
) -> None:
    """A resent batch is accepted once; layer 2 does not double count."""
    _, _, events = attempt(
        "ocr",
        vn("2026-11-16 09:00:00"),
        vn("2026-11-16 09:00:05"),
        "success",
        signals=("image_rejected",),
    )
    async with usage_client(postgres_session_factory) as (client, token):
        first = await post_events(client, token, events)
        second = await post_events(client, token, events)

    assert first == {"accepted": len(events), "duplicates": 0, "rejected": []}
    assert second == {"accepted": 0, "duplicates": len(events), "rejected": []}
    row = (await db_session.execute(select(UsageAttempt))).scalar_one()
    assert row.signals == {"image_rejected": 1}


async def test_finish_before_start_same_result(
    db_session: AsyncSession, postgres_session_factory: AsyncSessionFactory
) -> None:
    """Out-of-order delivery converges on the same attempt and task rows."""
    _, task_id, events = attempt(
        "ocr",
        vn("2026-11-16 09:00:00"),
        vn("2026-11-16 09:00:07"),
        "success",
        first_audio=vn("2026-11-16 09:00:02"),
    )
    events.append(
        task_done("ocr", task_id, vn("2026-11-16 09:00:07"), status="success", attempts=1)
    )
    shuffled = list(reversed(events))
    async with usage_client(postgres_session_factory) as (client, token):
        for one in shuffled:
            await post_events(client, token, [one])

    row = (await db_session.execute(select(UsageAttempt))).scalar_one()
    assert row.status == "success"
    assert row.started_at == vn("2026-11-16 09:00:00")
    assert row.first_audio_at == vn("2026-11-16 09:00:02")
    assert row.finished_at == vn("2026-11-16 09:00:07")
    task = (await db_session.execute(select(UsageTask))).scalar_one()
    assert (task.status, task.attempts, task.started_at) == (
        "success",
        1,
        vn("2026-11-16 09:00:00"),
    )


async def test_repeated_finish_keeps_first_status(
    db_session: AsyncSession, postgres_session_factory: AsyncSessionFactory
) -> None:
    """A second, different final outcome (callback repeat) does not overwrite the first."""
    attempt_id, task_id = uuid4(), uuid4()
    at = vn("2026-11-16 10:00:00")
    first = ev(
        "attempt_finished",
        at,
        attempt_id=attempt_id,
        task_id=task_id,
        status="failed",
        error_code="CONTACT_NOT_FOUND",
    )
    second = ev(
        "attempt_finished",
        at + timedelta(seconds=3),
        attempt_id=attempt_id,
        task_id=task_id,
        status="success",
    )
    async with usage_client(postgres_session_factory) as (client, token):
        await post_events(client, token, [first])
        await post_events(client, token, [second])

    row = (await db_session.execute(select(UsageAttempt))).scalar_one()
    assert (row.status, row.error_code, row.finished_at) == ("failed", "CONTACT_NOT_FOUND", at)


async def test_one_bad_event_in_batch(
    db_session: AsyncSession, postgres_session_factory: AsyncSessionFactory
) -> None:
    """499 good events are stored; the bad one is listed with a code, not its value."""
    at = vn("2026-11-16 11:00:00")
    good = [
        ev("session_heartbeat", at + timedelta(seconds=i), session_id=uuid4(), kind="connection")
        for i in range(499)
    ]
    bad = ev("attempt_started", at, attempt_id=uuid4(), task_id=uuid4(), feature="ocr")
    bad["text"] = "nội dung riêng tư"
    batch = [*good]
    batch.insert(len(batch) // 3, bad)
    async with usage_client(postgres_session_factory) as (client, token):
        result = await post_events(client, token, batch)

    assert result["accepted"] == 499
    assert result["rejected"] == [{"event_id": bad["event_id"], "code": "INVALID_EVENT"}]
    stored = await db_session.scalar(text("SELECT count(*) FROM usage_events"))
    assert stored == 499
    leaked = await db_session.scalar(
        text("SELECT count(*) FROM usage_events WHERE body::text LIKE '%riêng tư%'")
    )
    assert leaked == 0


async def test_unknown_type_rejected(postgres_session_factory: AsyncSessionFactory) -> None:
    """An unknown event type is refused with UNKNOWN_TYPE."""
    one = ev("selfie_taken", vn("2026-11-16 11:00:00"))
    async with usage_client(postgres_session_factory) as (client, token):
        result = await post_events(client, token, [one])
    assert result["rejected"] == [{"event_id": one["event_id"], "code": "UNKNOWN_TYPE"}]


async def test_request_id_conflict_rejects_only_that_event(
    db_session: AsyncSession, postgres_session_factory: AsyncSessionFactory
) -> None:
    """Two attempts claiming one request_id: the second is a CONFLICT, the batch still lands."""
    request_id = uuid4()
    at = vn("2026-11-16 12:00:00")
    a = ev(
        "attempt_finished",
        at,
        attempt_id=uuid4(),
        task_id=uuid4(),
        status="success",
        request_id=request_id,
    )
    b = ev(
        "attempt_finished",
        at,
        attempt_id=uuid4(),
        task_id=uuid4(),
        status="success",
        request_id=request_id,
    )
    async with usage_client(postgres_session_factory) as (client, token):
        result = await post_events(client, token, [a, b])
    assert result["accepted"] == 1
    assert result["rejected"] == [{"event_id": b["event_id"], "code": "CONFLICT"}]
    assert await db_session.scalar(text("SELECT count(*) FROM usage_attempts")) == 1


async def test_owner_resolved_from_active_pair_and_no_raw_ids(
    db_session: AsyncSession, postgres_session_factory: AsyncSessionFactory
) -> None:
    """Owner/device are stored only as pseudonyms; unpaired glasses get no owner."""
    await _pair(db_session, "glasses-a", "user-public-1")
    _, _, paired = attempt("chat", vn("2026-11-16 13:00:00"), vn("2026-11-16 13:00:04"), "success")
    _, _, unpaired = attempt(
        "chat", vn("2026-11-16 13:00:00"), vn("2026-11-16 13:00:04"), "success", device="glasses-b"
    )
    async with usage_client(postgres_session_factory) as (client, token):
        await post_events(client, token, paired + unpaired)

    rows = {r.device_hash: r for r in (await db_session.execute(select(UsageAttempt))).scalars()}
    a = rows[pseudonym(KEY, "device", "glasses-a")]
    b = rows[pseudonym(KEY, "device", "glasses-b")]
    assert a.owner_hash == pseudonym(KEY, "owner", "user-public-1")
    assert b.owner_hash is None
    dumps = {
        "usage_events": "SELECT coalesce(string_agg(t::text, ''), '') FROM usage_events t",
        "usage_attempts": "SELECT coalesce(string_agg(t::text, ''), '') FROM usage_attempts t",
        "usage_tasks": "SELECT coalesce(string_agg(t::text, ''), '') FROM usage_tasks t",
    }
    for query in dumps.values():
        dump = await db_session.scalar(text(query))
        assert "glasses-a" not in dump
        assert "user-public-1" not in dump


async def test_dirty_day_marked_for_start_day(
    db_session: AsyncSession, postgres_session_factory: AsyncSessionFactory
) -> None:
    """A task started 23:59 and closed 00:01 (VN) dirties only its start day."""
    _, task_id, events = attempt(
        "ocr", vn("2026-11-16 23:59:00"), vn("2026-11-17 00:01:00"), "success"
    )
    events.append(
        task_done("ocr", task_id, vn("2026-11-17 00:01:00"), status="success", attempts=1)
    )
    async with usage_client(postgres_session_factory) as (client, token):
        await post_events(client, token, events)
    days = (await db_session.execute(select(UsageDirtyDay.day))).scalars().all()
    assert days == [date(2026, 11, 16)]


async def test_cross_midnight_session_dirties_both_days(
    db_session: AsyncSession, postgres_session_factory: AsyncSessionFactory
) -> None:
    """An alert session 23:50-00:10 dirties both days and counts its alerts by level."""
    sid = uuid4()
    events = [
        ev("session_started", vn("2026-11-16 23:50:00"), session_id=sid, kind="alert_mode"),
        ev("alert_raised", vn("2026-11-16 23:55:00"), session_id=sid, level=2),
        ev("alert_raised", vn("2026-11-17 00:05:00"), session_id=sid, level=2),
        ev(
            "session_ended",
            vn("2026-11-17 00:10:00"),
            session_id=sid,
            kind="alert_mode",
            end_reason="user_off",
        ),
    ]
    async with usage_client(postgres_session_factory) as (client, token):
        await post_events(client, token, events)
    days = sorted((await db_session.execute(select(UsageDirtyDay.day))).scalars().all())
    assert days == [date(2026, 11, 16), date(2026, 11, 17)]
    session = (await db_session.execute(select(UsageSession))).scalar_one()
    assert session.alerts_by_level == {"2": 2}
    assert session.ended_at == vn("2026-11-17 00:10:00")


async def test_missing_token_is_401(postgres_session_factory: AsyncSessionFactory) -> None:
    """The batch endpoint requires the usage writer token."""
    async with usage_client(postgres_session_factory) as (client, _):
        response = await client.post("/api/v1/usage/events", json={"events": [{}]})
    assert response.status_code == 401


async def test_batch_over_500_is_400(postgres_session_factory: AsyncSessionFactory) -> None:
    """More than 500 events in one batch is refused by the app validation envelope (400)."""
    events = [{"type": "session_heartbeat"}] * 501
    async with usage_client(postgres_session_factory) as (client, token):
        response = await client.post(
            "/api/v1/usage/events",
            json={"events": events},
            headers={"Authorization": f"Bearer {token}"},
        )
    assert response.status_code == 400
