"""Acceptance scenarios of the business doc (``SeeVia_Huong_dan_trien_khai_thong_ke.md``).

One test per scenario of its "Kịch bản nghiệm thu" section.

Each test feeds the events VH emits for one scenario through ``POST /api/v1/usage/events``, runs the
rollup worker and reads the admin / owner APIs. The VH half of each rule (60/61 s merge, waiting for
``played`` + phone result, closing once) is covered by VH ``tests/test_usage_tasks.py`` and friends.
"""

from __future__ import annotations

import json
import re
from datetime import UTC, date, datetime, timedelta
from typing import TYPE_CHECKING, Any, cast
from uuid import uuid4

from sqlalchemy import select, text

from app.models import GlassesDevice, GlassesLinkStatus, User
from app.models.usage import UsageDaily, UsageDirtyDay, UsageSos
from app.services.usage_ids import pseudonym
from app.workers.usage_rollup import UsageRollupWorker
from tests.integration.admin_helpers import admin_client, auth, login, make_admin
from tests.integration.test_admin_stats_postgres import _session_token
from tests.integration.usage_helpers import (
    ID_KEY,
    attempt,
    ev,
    post_events,
    task_done,
    usage_client,
    usage_settings,
    vn,
)

if TYPE_CHECKING:
    from httpx import AsyncClient
    from sqlalchemy.ext.asyncio import AsyncSession

    from app.database import AsyncSessionFactory

KEY = bytes.fromhex(ID_KEY)


async def _load(
    factory: AsyncSessionFactory, events: list[dict[str, Any]], now: datetime | None = None
) -> dict[str, Any]:
    async with usage_client(factory) as (client, token):
        body = await post_events(client, token, events)
    assert body["rejected"] == [], body
    await _rollup(factory, now)
    return body


async def _rollup(factory: AsyncSessionFactory, now: datetime | None = None) -> None:
    """Run the worker until no dirty day is left; ``now`` pins the 30-minute UNCLOSED sweep."""
    worker = UsageRollupWorker(factory, usage_settings("unused"))
    while await worker.run_once(now=now):
        pass


async def _stats(
    factory: AsyncSessionFactory, name: str, day_from: str, day_to: str | None = None
) -> dict[str, Any]:
    await make_admin(factory)
    async with admin_client(factory) as client:
        token = await login(client)
        return await _get(client, token, name, day_from, day_to)


async def _get(
    client: AsyncClient, token: str, name: str, day_from: str, day_to: str | None = None
) -> dict[str, Any]:
    params = {"period": "custom", "from": day_from, "to": day_to or day_from}
    response = await client.get(f"/api/v1/admin/stats/{name}", params=params, headers=auth(token))
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return body


async def _daily(db: AsyncSession, day: date, key: str) -> int:
    rows = (await db.execute(select(UsageDaily.metrics).where(UsageDaily.day == day))).scalars()
    return sum(int(str(m.get(key, 0))) for m in rows)


# ── 1. Chụp lại ──────────────────────────────────────────────────────────────────────────────


async def test_retake(
    db_session: AsyncSession, postgres_session_factory: AsyncSessionFactory
) -> None:
    """Retake: 2 attempts, 1 rejected image, 1 successful task, 1 retry, first-try 0 %.

    The OCR attempt is rejected and the retake 40 s later succeeds.
    """
    t0, task = vn("2026-11-10 09:00:00"), uuid4()
    _, _, first = attempt(
        "ocr",
        t0,
        t0 + timedelta(seconds=3),
        "rejected_image",
        task_id=task,
        signals=("image_rejected",),
    )
    second_at = t0 + timedelta(seconds=40)
    _, _, second = attempt(
        "ocr",
        second_at,
        second_at + timedelta(seconds=6),
        "success",
        task_id=task,
        first_audio=second_at + timedelta(seconds=2),
    )
    done = task_done("ocr", task, second_at + timedelta(seconds=6), status="success", attempts=2)
    await _load(postgres_session_factory, [*first, *second, done])

    assert await _daily(db_session, date(2026, 11, 10), "attempts") == 2
    await make_admin(postgres_session_factory)
    async with admin_client(postgres_session_factory) as client:
        token = await login(client)
        quality = await _get(client, token, "quality", "2026-11-10")
        see = {
            f["key"]: f for f in (await _get(client, token, "see-read", "2026-11-10"))["features"]
        }
    assert quality["total_uses"] == 1
    assert quality["completion"]["rate"] == 100.0
    assert quality["completion"]["first_try_rate"] == 0.0
    assert quality["completion"]["avg_retries"] == 1.0
    assert {a["key"]: a["count"] for a in quality["attempts"]}["image_rejected"] == 1
    assert see["ocr"]["uses"] == 1
    assert see["ocr"]["details"]["rejected_images"] == 1


# ── 2. Biên ghép lượt ────────────────────────────────────────────────────────────────────────


async def test_merge_boundary_tasks(
    db_session: AsyncSession, postgres_session_factory: AsyncSessionFactory
) -> None:
    """Merge boundary: AB counts one use per task as VH grouped the attempts.

    VH merged the attempt at +60 s (same task_id) and opened a new task at +61 s; two independent
    requests a few seconds apart are two tasks.
    """
    t0 = vn("2026-11-11 10:00:00")
    merged, late, indep_a, indep_b = uuid4(), uuid4(), uuid4(), uuid4()
    events: list[dict[str, Any]] = []
    plan = [
        (merged, 0, "rejected_image"),
        (merged, 60, "success"),  # ≤ 60 s while the task waited: same task
        (late, 121, "success"),  # 61 s after the previous call: new task
        (indep_a, 200, "success"),
        (indep_b, 203, "success"),  # independent context: its own task
    ]
    for task_id, offset, status in plan:
        start = t0 + timedelta(seconds=offset)
        _, _, evs = attempt("ask", start, start + timedelta(seconds=2), status, task_id=task_id)
        events += evs
    counts = {merged: 2, late: 1, indep_a: 1, indep_b: 1}
    for task_id, n in counts.items():
        events.append(
            task_done("ask", task_id, t0 + timedelta(seconds=300), status="success", attempts=n)
        )
    await _load(postgres_session_factory, events)

    assert await _daily(db_session, date(2026, 11, 11), "attempts") == 5
    quality = await _stats(postgres_session_factory, "quality", "2026-11-11")
    assert quality["total_uses"] == 4
    # 1 extra attempt / 4 tasks = 0.25, shown with one decimal.
    assert quality["completion"]["avg_retries"] == 0.2


# ── 3. Xác nhận kết quả ──────────────────────────────────────────────────────────────────────


async def test_result_confirmation(
    db_session: AsyncSession, postgres_session_factory: AsyncSessionFactory
) -> None:
    """Result confirmation: open attempts wait, error codes land, repeated callbacks do not count.

    A repeated callback reaches AB as a second ``attempt_finished``; the first status stays.
    """
    t0 = vn("2026-11-12 08:00:00")
    pending_id, pending_task = uuid4(), uuid4()
    open_attempt = ev(
        "attempt_started", t0, attempt_id=pending_id, task_id=pending_task, feature="music"
    )
    attempt_id, task, call = attempt(
        "call",
        t0 + timedelta(minutes=1),
        t0 + timedelta(minutes=1, seconds=9),
        "failed",
        error_code="CONTACT_NOT_FOUND",
    )
    repeat = ev(
        "attempt_finished",
        t0 + timedelta(minutes=1, seconds=12),
        attempt_id=attempt_id,
        task_id=task,
        status="success",
    )
    closed = task_done(
        "call", task, t0 + timedelta(minutes=1, seconds=9), status="failed", attempts=1
    )
    # Worker clock 5 min later: the music attempt still waits for playback + phone result.
    await _load(
        postgres_session_factory,
        [open_attempt, *call, closed, repeat],
        now=t0 + timedelta(minutes=5),
    )

    assert await _daily(db_session, date(2026, 11, 12), "attempts") == 2
    await make_admin(postgres_session_factory)
    async with admin_client(postgres_session_factory) as client:
        token = await login(client)
        quality = await _get(client, token, "quality", "2026-11-12")
        errors = await _get(client, token, "errors", "2026-11-12")
        outcomes = {o["key"]: o["uses"] for o in quality["outcomes"]}
        assert sum(outcomes.values()) == quality["total_uses"]
        # The repeated "success" did not overwrite the failure.
        assert outcomes.get("success", 0) == 0
        assert [(i["code"], i["count"]) for i in errors["items"]] == [("CONTACT_NOT_FOUND", 1)]

        # Never confirmed: after 30 min AB closes it as a technical error (VH died / lost the turn).
        db_session.add(UsageDirtyDay(day=date(2026, 11, 12)))
        await db_session.commit()
        await _rollup(postgres_session_factory, now=t0 + timedelta(minutes=31))
        errors = await _get(client, token, "errors", "2026-11-12")
    assert sorted((i["code"], i["count"]) for i in errors["items"]) == [
        ("CONTACT_NOT_FOUND", 1),
        ("UNCLOSED", 1),
    ]


# ── 4. Hỏi lại, huỷ và SOS ───────────────────────────────────────────────────────────────────


async def test_reask_cancel_and_sos(
    db_session: AsyncSession, postgres_session_factory: AsyncSessionFactory
) -> None:
    """Re-ask, cancel and SOS feed their own metrics.

    Re-ask then cancel in the window feeds reask and cancel-in-window; an answered SOS records
    time-to-confirm, a failed SOS is a failure.
    """
    t0, task = vn("2026-11-13 15:00:00"), uuid4()
    ask_id, _, asked = attempt(
        "message_send",
        t0,
        t0 + timedelta(seconds=4),
        "success",
        task_id=task,
        signals=("reprompt",),
    )
    yes_at = t0 + timedelta(seconds=10)
    yes_id, _, yes = attempt(
        "message_send",
        yes_at,
        yes_at + timedelta(seconds=3),
        "cancelled",
        task_id=task,
        signals=("cancel_window_open", "cancelled_in_window"),
    )
    done = task_done(
        "message_send", task, yes_at + timedelta(seconds=3), status="cancelled", attempts=2
    )
    await _load(postgres_session_factory, [*asked, *yes, done])
    assert ask_id != yes_id

    owner, device = pseudonym(KEY, "owner", "U-SOS"), pseudonym(KEY, "device", "glasses-a")
    sos_at = vn("2026-11-13 16:00:00")
    db_session.add_all(
        [
            # AB's recorder writes these rows; tests/test_usage_calls_sos.py drives it end to end.
            UsageSos(
                sos_id=uuid4(),
                owner_hash=owner,
                device_hash=device,
                started_at=sos_at,
                answered_at=sos_at + timedelta(seconds=12),
                finished_at=sos_at + timedelta(seconds=90),
                status="success",
            ),
            UsageSos(
                sos_id=uuid4(),
                owner_hash=owner,
                device_hash=device,
                started_at=sos_at + timedelta(minutes=10),
                finished_at=sos_at + timedelta(minutes=12),
                status="failed",
                error_code="NO_ANSWER",
            ),
            UsageDirtyDay(day=date(2026, 11, 13)),
        ]
    )
    await db_session.commit()
    await _rollup(postgres_session_factory)

    await make_admin(postgres_session_factory)
    async with admin_client(postgres_session_factory) as client:
        token = await login(client)
        connect = {
            f["key"]: f for f in (await _get(client, token, "connect", "2026-11-13"))["features"]
        }
        safety = await _get(client, token, "safety", "2026-11-13")
    family = connect["family"]
    assert family["details"]["reask_pct"] == 50.0  # 1 re-ask over 2 attempts
    assert family["details"]["cancel_in_window_pct"] == 100.0  # 1 cancel over 1 open window
    assert safety["sos"]["count"] == 2
    assert (safety["sos"]["success"], safety["sos"]["failed"]) == (1, 1)
    assert 10 <= safety["sos"]["confirm_p50_seconds"] <= 15


# ── 5. Gửi lô và dữ liệu đến muộn ────────────────────────────────────────────────────────────


async def test_resend_late_and_midnight(
    db_session: AsyncSession, postgres_session_factory: AsyncSessionFactory
) -> None:
    """Resend, late data and midnight give stable numbers.

    Resent batch and finish-before-start give the same result; a task 23:59 -> 00:01 counts on
    its start day; an attempt without first audio is counted but outside the p50/p90 sample.
    """
    start = vn("2026-11-14 23:59:00")
    end = start + timedelta(minutes=2)
    _, task, night = attempt(
        "space", start, end, "success", first_audio=start + timedelta(seconds=1)
    )
    night_done = task_done("space", task, end, status="success", attempts=1)
    noon = vn("2026-11-14 12:00:00")
    _, silent_task, silent = attempt("space", noon, noon + timedelta(seconds=5), "success")
    silent_done = task_done(
        "space", silent_task, noon + timedelta(seconds=5), status="success", attempts=1
    )
    batch = [night[-1], *night[:-1], night_done, *silent, silent_done]  # finish first

    first = await _load(postgres_session_factory, batch)
    async with usage_client(postgres_session_factory) as (client, token):
        again = await post_events(client, token, batch)
    await _rollup(postgres_session_factory)
    assert first["accepted"] == len(batch)
    assert (again["accepted"], again["duplicates"]) == (0, len(batch))

    day = date(2026, 11, 14)
    assert await _daily(db_session, day, "tasks_closed") == 2
    assert await _daily(db_session, day + timedelta(days=1), "tasks_closed") == 0
    assert await _daily(db_session, day, "attempts") == 2
    hist = (
        await db_session.execute(select(UsageDaily.metrics).where(UsageDaily.day == day))
    ).scalars()
    # Only the attempt with a first-audio mark is in the latency sample.
    samples = [int(str(n)) for m in hist for n in cast("list[object]", m["first_audio_hist"])]
    assert sum(samples) == 1
    quality = await _stats(postgres_session_factory, "quality", "2026-11-14")
    assert quality["completion"]["rate"] == 100.0
    assert quality["speed"]["first_audio_p50_ms"] is not None
    assert quality["speed"]["first_audio_p50_ms"] <= 1500


# ── 6. Phiên qua ngày ────────────────────────────────────────────────────────────────────────


async def test_alert_session_across_midnight(
    postgres_session_factory: AsyncSessionFactory,
    db_session: AsyncSession,  # noqa: ARG001 -- truncates the tables after the test
) -> None:
    """Alert mode 23:50 -> 00:10 = 20 min, 10 min per report day; 2 alerts -> 6 alerts per hour."""
    start = vn("2026-11-15 23:50:00")
    sid = uuid4()
    events = [
        ev("session_started", start, session_id=sid, kind="alert_mode"),
        ev("alert_raised", start + timedelta(minutes=5), session_id=sid, level=2),
        ev("alert_raised", start + timedelta(minutes=15), session_id=sid, level=3),
        ev(
            "session_ended",
            start + timedelta(minutes=20),
            session_id=sid,
            kind="alert_mode",
            end_reason="user_off",
        ),
    ]
    await _load(postgres_session_factory, events)
    await make_admin(postgres_session_factory)
    async with admin_client(postgres_session_factory) as client:
        token = await login(client)
        day1 = await _get(client, token, "go", "2026-11-15")
        day2 = await _get(client, token, "go", "2026-11-16")
        both = await _get(client, token, "go", "2026-11-15", "2026-11-16")
    assert day1["summary"]["alert_mode_seconds"] == 600
    assert day2["summary"]["alert_mode_seconds"] == 600
    assert both["summary"]["alert_mode_seconds"] == 1200
    assert both["summary"]["alerts_per_hour"] == 6.0


# ── 7. Phân quyền và dữ liệu ─────────────────────────────────────────────────────────────────


async def test_owner_isolation_and_payload(
    db_session: AsyncSession, postgres_session_factory: AsyncSessionFactory
) -> None:
    """Owner isolation and payload: each owner sees only their numbers, nothing raw is stored.

    Admin sees the sum; layer 1-3 rows and responses hold no raw device / owner id or phone number.
    """
    now = datetime(2026, 11, 1, tzinfo=UTC)
    for owner, device, phone in (
        ("U-ONE", "glasses-one", "0911000001"),
        ("U-TWO", "glasses-two", "0911000002"),
    ):
        db_session.add(
            User(public_user_id=owner, phone_number=phone, password_hash="x", phone_verified=True)
        )
        db_session.add(
            GlassesDevice(
                user_id=owner, device_id=device, status=GlassesLinkStatus.ACTIVE, linked_at=now
            )
        )
    await db_session.commit()
    events: list[dict[str, Any]] = []
    t0 = vn("2026-11-12 09:00:00")
    for device, n in (("glasses-one", 3), ("glasses-two", 1)):
        for i in range(n):
            start = t0 + timedelta(minutes=i)
            _, task, evs = attempt(
                "chat", start, start + timedelta(seconds=2), "success", device=device
            )
            events += [
                *evs,
                task_done("chat", task, start + timedelta(seconds=2), status="success", attempts=1),
            ]
            events[-1]["device_id"] = device
    await _load(postgres_session_factory, events)

    period = {"from": "2026-11-12", "to": "2026-11-12"}
    await make_admin(postgres_session_factory)
    async with admin_client(postgres_session_factory) as client:
        one = (
            await client.get(
                "/api/v1/me/stats",
                params=period,
                headers=auth(await _session_token(db_session, "U-ONE")),
            )
        ).json()
        two = (
            await client.get(
                "/api/v1/me/stats",
                params=period,
                headers=auth(await _session_token(db_session, "U-TWO")),
            )
        ).json()
        token = await login(client)
        overview = await _get(client, token, "overview", "2026-11-12")
    assert (one["totals"]["uses"], two["totals"]["uses"]) == (3, 1)
    assert overview["totals"]["uses"] == 4
    assert overview["totals"]["active_owners"] == 2

    raw = ("glasses-one", "glasses-two", "U-ONE", "U-TWO", "0911000001", "0911000002")
    for table in (
        "usage_events",
        "usage_attempts",
        "usage_tasks",
        "usage_daily",
        "usage_hourly",
        "usage_active_days",
    ):
        dump = json.dumps(
            [
                list(map(str, r))
                # Table names are the constants above, not input.
                for r in (await db_session.execute(text(f"SELECT * FROM {table}"))).all()  # noqa: S608
            ]
        )
        for value in raw:
            assert value not in dump, (table, value)
    for body in (json.dumps(one), json.dumps(two), json.dumps(overview)):
        for value in raw:
            assert value not in body
        assert not re.search(r"[0-9a-f]{64}", body)
