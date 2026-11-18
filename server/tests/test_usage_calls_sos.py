"""PostgreSQL: AB records call and SOS statistics in the same transaction as the state change.

Full app on the test database (harness of ``test_emergency_relatives``), then the rollup worker and
``owner_stats`` (what ``GET /api/v1/me/stats`` returns) to check the numbers the owner sees.
"""

from __future__ import annotations

import json
import uuid
from typing import TYPE_CHECKING

import pytest
from sqlalchemy import select, text

from app.database import build_session_factory
from app.models.call import Call
from app.models.usage import UsageCall, UsageEvent, UsageSos
from app.services.admin_stats import owner_stats, resolve_period, today_vn
from app.services.usage_ab import AbUsageRecorder
from app.workers.usage_rollup import UsageRollupWorker
from tests.helpers import PUBLIC_TOKEN
from tests.integration.usage_helpers import ID_KEY, usage_settings
from tests.test_emergency_relatives import RELATIVES, World, _make_world

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from sqlalchemy.ext.asyncio import AsyncEngine

SVC = {"Authorization": f"Bearer {PUBLIC_TOKEN}"}
_USAGE_TABLES = (
    "usage_events",
    "usage_calls",
    "usage_sos",
    "usage_dirty_days",
    "usage_daily",
    "usage_hourly",
    "usage_active_days",
)


async def _clean_usage(engine: AsyncEngine) -> None:
    async with engine.begin() as connection:
        await connection.execute(text("TRUNCATE TABLE " + ", ".join(_USAGE_TABLES)))


@pytest.fixture
async def world(
    db_engine: AsyncEngine,
    clean_auth_tables: None,  # noqa: ARG001
    caplog: pytest.LogCaptureFixture,
) -> AsyncIterator[World]:
    """Owner + one emergency relative "Bố"; statistics on (``USAGE_ID_KEY`` set)."""
    await _clean_usage(db_engine)
    async for w in _make_world(db_engine, caplog, [RELATIVES[1]], usage_id_key=ID_KEY):
        yield w
    await _clean_usage(db_engine)


@pytest.fixture
async def world_off(
    db_engine: AsyncEngine,
    clean_auth_tables: None,  # noqa: ARG001
    caplog: pytest.LogCaptureFixture,
) -> AsyncIterator[World]:
    """Build the same world with statistics off (no ``USAGE_ID_KEY``)."""
    await _clean_usage(db_engine)
    async for w in _make_world(db_engine, caplog, [RELATIVES[1]]):
        yield w


async def _call_glasses_to_relative(world: World) -> str:
    res = await world.client.post(
        "/api/v1/service/call/start",
        json={
            "glasses_device_id": world.glasses_id,
            "contact_alias": "Bố",
            "media_token": f"media-token-{uuid.uuid4().hex[:8]}",
            "request_id": str(uuid.uuid4()),
        },
        headers=SVC,
    )
    assert res.status_code == 200, res.text
    return str(res.json()["data"]["call_id"])


async def _answer(world: World, call_id: str) -> None:
    res = await world.client.post(f"/api/v1/calls/{call_id}/answer", headers=world.sessions["Bố"])
    assert res.status_code == 200, res.text


async def _glasses_hangup(world: World, call_id: str) -> None:
    res = await world.client.post(
        f"/api/v1/service/call/{call_id}/hangup", json={"reason": "hangup"}, headers=SVC
    )
    assert res.status_code == 200, res.text


async def _owner_totals(world: World) -> dict[str, object]:
    factory = build_session_factory(world.engine)
    worker = UsageRollupWorker(factory, usage_settings("unused"))
    while await worker.run_once():
        pass
    today = today_vn()
    async with factory() as session:
        stats = await owner_stats(
            session,
            resolve_period("custom", today, today),
            id_key=bytes.fromhex(ID_KEY),
            public_user_id=world.owner_id,
        )
    totals: dict[str, object] = stats["totals"]
    return totals


async def test_call_answer_rate_and_talk_seconds(world: World) -> None:
    """Một cuộc có người nghe rồi cúp, một cuộc hết giờ chuông: 2 cuộc, 1 có người nghe."""
    answered = await _call_glasses_to_relative(world)
    await _answer(world, answered)
    await _glasses_hangup(world, answered)
    await _call_glasses_to_relative(world)
    await world.expire_ringing()

    rows = {str(r.call_id): r for r in await world.rows(UsageCall)}
    assert len(rows) == 2
    done = rows.pop(answered)
    assert (done.final_state, done.end_reason, done.direction, done.emergency) == (
        "ended",
        "hangup",
        "to_relative",
        False,
    )
    assert done.answered_at is not None
    assert done.ended_at is not None
    assert done.started_at <= done.answered_at <= done.ended_at
    (missed,) = rows.values()
    assert (missed.final_state, missed.answered_at) == ("missed", None)

    totals = await _owner_totals(world)
    assert (totals["internet_calls"], totals["internet_calls_answered"]) == (2, 1)


async def test_call_counted_once_even_if_both_sides_report(world: World) -> None:
    """Nghe lặp, hai bên cùng cúp, ghi lại cùng trạng thái: một hàng, một sự kiện mỗi trạng thái."""
    call_id = await _call_glasses_to_relative(world)
    await _answer(world, call_id)
    await _answer(world, call_id)  # nghe lặp: no-op
    await _glasses_hangup(world, call_id)
    res = await world.client.post(f"/api/v1/calls/{call_id}/hangup", headers=world.sessions["Bố"])
    assert res.status_code == 200, res.text  # đã kết thúc: no-op
    factory = build_session_factory(world.engine)
    async with factory() as session:  # phát lại đúng trạng thái cuối (vd. worker chạy lại)
        call = await session.get(Call, uuid.UUID(call_id))
        assert call is not None
        await AbUsageRecorder(session, world.settings).call(call)
        await session.commit()

    assert len(await world.rows(UsageCall)) == 1
    states = sorted(e.body["state"] for e in await world.rows(UsageEvent))
    assert states == ["active", "ended", "ringing"]
    totals = await _owner_totals(world)
    assert (totals["internet_calls"], totals["internet_calls_answered"]) == (1, 1)


async def test_emergency_calls_excluded_from_call_stats(world: World) -> None:
    """Cuộc gọi trong chuỗi SOS vẫn lưu (emergency=true) nhưng không vào thống kê gọi thường."""
    assert (await world.start()).status_code == 200
    call = (await world.calls())[-1]
    await _answer(world, str(call.id))
    await world.worker.run_chain_once()  # type: ignore[union-attr]

    (row,) = await world.rows(UsageCall)
    assert row.emergency is True
    totals = await _owner_totals(world)
    assert (totals["internet_calls"], totals["sos"]) == (0, 1)


async def test_sos_answered_time_to_answer(world: World) -> None:
    """SOS có người nghe: success, có thời điểm nghe máy (thời gian tới khi có người nghe)."""
    assert (await world.start()).status_code == 200
    call = (await world.calls())[-1]
    await _answer(world, str(call.id))
    assert await world.worker.run_chain_once() == 1  # type: ignore[union-attr]

    (sos,) = await world.rows(UsageSos)
    assert (sos.status, sos.error_code) == ("success", None)
    assert sos.answered_at is not None
    assert sos.started_at <= sos.answered_at
    assert sos.finished_at is not None
    totals = await _owner_totals(world)
    assert (totals["sos"], totals["uses"]) == (1, 1)


async def test_sos_exhausted_is_failure(world: World) -> None:
    """Không ai nghe hết các vòng: failed / NO_ANSWER; bấm dừng ngay: cancelled."""
    assert (await world.start(n_tokens=9)).status_code == 200
    for _ in range(world.settings.emergency_max_rounds):
        await world.expire_ringing()
        await world.worker.run_chain_once()  # type: ignore[union-attr]
    (sos,) = await world.rows(UsageSos)
    assert (sos.status, sos.error_code, sos.answered_at) == ("failed", "NO_ANSWER", None)

    started = await world.start()
    stop = await world.client.post(
        f"/api/v1/service/emergency/{started.json()['data']['emergency_id']}/stop", headers=SVC
    )
    assert stop.status_code == 200, stop.text
    statuses = sorted(r.status for r in await world.rows(UsageSos))
    assert statuses == ["cancelled", "failed"]
    totals = await _owner_totals(world)
    assert totals["sos"] == 2


async def test_statistics_off_without_key(world_off: World) -> None:
    """Không có USAGE_ID_KEY: cuộc gọi/SOS vẫn chạy bình thường, không ghi gì vào bảng thống kê."""
    call_id = await _call_glasses_to_relative(world_off)
    await _answer(world_off, call_id)
    await _glasses_hangup(world_off, call_id)
    assert (await world_off.start()).status_code == 200
    assert await world_off.rows(UsageEvent) == []
    assert await world_off.rows(UsageCall) == []
    assert await world_off.rows(UsageSos) == []


async def test_no_names_numbers_or_raw_ids_stored(world: World) -> None:
    """Thống kê không lưu tên người thân, số điện thoại, mã kính hay mã người dùng thô."""
    call_id = await _call_glasses_to_relative(world)
    await _answer(world, call_id)
    await _glasses_hangup(world, call_id)
    assert (await world.start()).status_code == 200
    async with build_session_factory(world.engine)() as session:
        events = [e.body for e in (await session.execute(select(UsageEvent))).scalars()]
        rows = [
            (r.owner_hash, r.device_hash)
            for r in (await session.execute(select(UsageCall))).scalars()
        ]
    dump = json.dumps(events, ensure_ascii=False) + json.dumps(rows)
    for private in (
        "Bố",
        "0961000001",
        "0961000002",
        world.glasses_id,
        world.owner_id,
        "media-token",
    ):
        assert private not in dump
