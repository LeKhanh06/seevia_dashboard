"""PostgreSQL tests for ``/api/v1/admin/stats/*`` against the dashboard's consistency rules."""

from __future__ import annotations

import json
import re
import secrets
from datetime import UTC, date, datetime, timedelta
from typing import TYPE_CHECKING, Any
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.models import GlassesDevice, GlassesLinkStatus, Session, User
from app.models.usage import UsageCall, UsageDirtyDay, UsageSos
from app.security import hash_token
from app.services.usage_ids import pseudonym
from app.workers.usage_rollup import UsageRollupWorker
from tests.integration.admin_helpers import admin_client, auth, login, make_admin
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
OWNER = "U-A"
WEEK = {"period": "week", "from": "2026-11-09", "to": "2026-11-15"}
PAGES = {"see_read": "see-read", "find": "find", "connect": "connect", "go": "go"}


def _task(  # noqa: PLR0913 -- test builder
    feature: str,
    start: str,
    status: str,
    *,
    signals: tuple[str, ...] = (),
    error_code: str | None = None,
    first_audio_ms: int | None = 1500,
) -> list[dict[str, Any]]:
    """One single-attempt task, closed."""
    begin = vn(start)
    first = begin + timedelta(milliseconds=first_audio_ms) if first_audio_ms else None
    _, task_id, events = attempt(
        feature,
        begin,
        begin + timedelta(seconds=6),
        status,
        signals=signals,
        error_code=error_code,
        first_audio=first,
    )
    events.append(
        task_done(feature, task_id, begin + timedelta(seconds=6), status=status, attempts=1)
    )
    return events


def _dataset() -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    # Tue 10/11 08:xx: OCR retake (2 attempts, 1 task), OCR with live framing, money, chat error.
    t0 = vn("2026-11-10 08:00:00")
    _, task, first = attempt(
        "ocr", t0, t0 + timedelta(seconds=3), "rejected_image", signals=("image_rejected",)
    )
    _, _, second = attempt(
        "ocr",
        t0 + timedelta(seconds=20),
        t0 + timedelta(seconds=26),
        "success",
        task_id=task,
        first_audio=t0 + timedelta(seconds=22),
    )
    events += [*first, *second]
    events.append(task_done("ocr", task, t0 + timedelta(seconds=26), status="success", attempts=2))
    t1 = vn("2026-11-10 08:10:00")
    live_attempt, live_task, live = attempt(
        "ocr", t1, t1 + timedelta(seconds=12), "success", first_audio=t1 + timedelta(seconds=9)
    )
    events += live
    events.append(
        ev(
            "attempt_marks",
            t1 + timedelta(seconds=8),
            attempt_id=live_attempt,
            align_ms=4000,
            processing_ms=3000,
        )
    )
    events.append(
        task_done("ocr", live_task, t1 + timedelta(seconds=12), status="success", attempts=1)
    )
    events += _task("money", "2026-11-10 08:20:00", "success", signals=("money_decided",))
    events += _task(
        "chat",
        "2026-11-10 08:30:00",
        "technical_error",
        error_code="NET_TIMEOUT",
        signals=("needs_web_decided", "needs_web"),
    )
    # Thu 12/11 18:xx — family (call cancelled in window + message), music, navigation, SOS via VH.
    events += _task(
        "call",
        "2026-11-12 18:00:00",
        "cancelled",
        signals=("reprompt", "cancel_window_open", "cancelled_in_window"),
    )
    events += _task("message_send", "2026-11-12 18:05:00", "success")
    events += _task("music", "2026-11-12 18:10:00", "success")
    events += _task("navigation", "2026-11-12 18:20:00", "success")
    events += _task("emergency_call", "2026-11-12 18:30:00", "success")  # not a "use": SOS is AB's
    # Thu 12/11 19:00-19:30 alert mode, two alerts.
    alert = uuid4()
    events += [
        ev("session_started", vn("2026-11-12 19:00:00"), session_id=alert, kind="alert_mode"),
        ev("alert_raised", vn("2026-11-12 19:05:00"), session_id=alert, level=1),
        ev("alert_raised", vn("2026-11-12 19:06:00"), session_id=alert, level=2),
        ev(
            "session_ended",
            vn("2026-11-12 19:30:00"),
            session_id=alert,
            kind="alert_mode",
            end_reason="user_off",
        ),
    ]
    # Previous week: the same chat error once, an OCR success.
    events += _task("chat", "2026-11-05 09:00:00", "technical_error", error_code="NET_TIMEOUT")
    events += _task("ocr", "2026-11-05 09:10:00", "success")
    return events


async def _seed(db: AsyncSession, factory: AsyncSessionFactory) -> None:
    db.add(
        User(
            public_user_id=OWNER,
            phone_number="0912345612",
            password_hash="x",
            phone_verified=True,
            created_at=vn("2026-11-11 10:00:00"),
        )
    )
    db.add(
        GlassesDevice(
            user_id=OWNER,
            device_id="glasses-a",
            status=GlassesLinkStatus.ACTIVE,
            linked_at=datetime(2026, 11, 1, tzinfo=UTC),
        )
    )
    await db.commit()
    async with usage_client(factory) as (client, token):
        body = await post_events(client, token, _dataset())
    assert body["rejected"] == [], body
    owner, device = pseudonym(KEY, "owner", OWNER), pseudonym(KEY, "device", "glasses-a")
    sos_start = vn("2026-11-13 10:00:00")
    db.add_all(
        [
            UsageSos(  # cancelled after 3 s: false start
                sos_id=uuid4(),
                owner_hash=owner,
                device_hash=device,
                started_at=sos_start,
                finished_at=sos_start + timedelta(seconds=3),
                status="cancelled",
            ),
            UsageSos(
                sos_id=uuid4(),
                owner_hash=owner,
                device_hash=device,
                started_at=sos_start + timedelta(hours=1),
                answered_at=sos_start + timedelta(hours=1, seconds=9),
                finished_at=sos_start + timedelta(hours=1, seconds=60),
                status="success",
            ),
            UsageCall(  # relative calls the glasses: one internet_call use
                call_id=uuid4(),
                owner_hash=owner,
                device_hash=device,
                direction="to_glasses",
                emergency=False,
                started_at=vn("2026-11-14 20:00:00"),
                answered_at=vn("2026-11-14 20:00:10"),
                ended_at=vn("2026-11-14 20:02:10"),
                final_state="ended",
            ),
            UsageCall(  # outgoing: part of answer rate, not a use (the "family" task is)
                call_id=uuid4(),
                owner_hash=owner,
                device_hash=device,
                direction="to_relative",
                emergency=False,
                started_at=vn("2026-11-14 21:00:00"),
                final_state="missed",
                ended_at=vn("2026-11-14 21:00:30"),
            ),
            UsageDirtyDay(day=date(2026, 11, 13)),
            UsageDirtyDay(day=date(2026, 11, 14)),
        ]
    )
    await db.commit()
    worker = UsageRollupWorker(factory, usage_settings("unused"))
    while await worker.run_once():
        pass


async def _get(client: AsyncClient, token: str, name: str, **params: str | int) -> dict[str, Any]:
    response = await client.get(
        f"/api/v1/admin/stats/{name}", params={**WEEK, **params}, headers=auth(token)
    )
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return body


def _sum(values: list[Any]) -> int:
    return sum(v or 0 for v in values)


async def test_dashboard_consistency_rules(
    db_session: AsyncSession, postgres_session_factory: AsyncSessionFactory
) -> None:
    """Every cross-check of the dashboard's validate.js holds on real rolled-up data."""
    await _seed(db_session, postgres_session_factory)
    await make_admin(postgres_session_factory)
    async with admin_client(postgres_session_factory) as client:
        token = await login(client)
        for params in (WEEK, {"period": "today", "from": "2026-11-12", "to": "2026-11-12"}):
            q = dict(params)
            ov = await _get(client, token, "overview", **q)
            tm = await _get(client, token, "time", **q)
            ql = await _get(client, token, "quality", **q)
            sf = await _get(client, token, "safety", **q)
            total = ov["totals"]["uses"]
            assert _sum([p["uses"] for p in ov["series"]["points"]]) == total
            assert _sum([g["uses"] for g in ov["groups"]]) + ov["sos_uses"] == total
            assert _sum([d["uses"] for d in tm["by_day"]]) == total
            assert sum(sum(row) for row in tm["heatmap"]["cells"]) == total
            assert _sum([h["uses"] for h in tm["by_hour"]]) == total
            assert sum(o["uses"] for o in ql["outcomes"]) == total == ql["total_uses"]
            assert sf["sos"]["count"] == ov["sos_uses"]
            assert sf["sos"]["count"] == (
                sf["sos"]["success"] + sf["sos"]["failed"] + sf["sos"]["cancelled_within_5s"]
            )
            errors_on_pages = 0
            for group, page in PAGES.items():
                report = await _get(client, token, page, **q)
                group_uses = next(g["uses"] for g in ov["groups"] if g["key"] == group)
                assert report["summary"]["uses"] == group_uses
                assert sum(f["uses"] for f in report["features"]) == group_uses
                errors_on_pages += sum(f["errors"] for f in report["features"])
            table = await _get(client, token, "errors", page_size=100, **q)
            assert sum(i["count"] for i in table["items"]) == errors_on_pages

        ov = await _get(client, token, "overview")
        assert ov["totals"]["uses"] == 12
        assert {g["key"]: g["uses"] for g in ov["groups"]} == {
            "see_read": 3,
            "find": 0,
            "connect": 5,
            "go": 2,
        }
        assert ov["sos_uses"] == 2
        assert ov["totals"]["active_owners"] == 1
        assert ov["totals"]["uses_change_pct"] == 500.0  # 2 uses the week before


async def test_feature_details_and_rates(
    db_session: AsyncSession, postgres_session_factory: AsyncSessionFactory
) -> None:
    """Retake, live framing, family signals, calls, alert mode and SOS land in the right fields."""
    await _seed(db_session, postgres_session_factory)
    await make_admin(postgres_session_factory)
    async with admin_client(postgres_session_factory) as client:
        token = await login(client)
        see = {f["key"]: f for f in (await _get(client, token, "see-read"))["features"]}
        connect = {f["key"]: f for f in (await _get(client, token, "connect"))["features"]}
        go = await _get(client, token, "go")
        ql = await _get(client, token, "quality")
        sf = await _get(client, token, "safety")
        eng = await _get(client, token, "engagement")
    assert see["ocr"]["uses"] == 1
    assert see["ocr"]["details"]["rejected_images"] == 1
    assert see["ocr"]["details"]["avg_retries"] == 1.0
    assert see["ocr_live"]["uses"] == 1
    assert see["ocr_live"]["uses_unit"] == "sessions"
    assert 3.0 <= see["ocr_live"]["details"]["framing_p50_seconds"] <= 4.0
    assert see["money"]["details"]["uncertain_rate_pct"] == 0.0
    assert connect["chat"]["errors"] == 1
    assert connect["chat"]["top_error"] == {"code": "NET_TIMEOUT", "count": 1}
    assert connect["chat"]["details"]["web_lookup_rate_pct"] == 100.0
    family = connect["family"]
    assert family["uses"] == 2
    assert family["details"]["cancel_in_window_pct"] == 100.0
    assert family["details"]["reask_pct"] == 50.0
    call = connect["internet_call"]
    assert (call["uses"], call["uses_unit"], call["completion_rate"]) == (1, "calls", None)
    assert call["details"]["answer_rate_pct"] == 50.0
    assert call["details"]["avg_call_seconds"] == 120.0
    assert go["summary"]["alert_mode_seconds"] == 1800
    assert go["summary"]["alerts_per_hour"] == 4.0
    assert ql["completion"]["first_try_rate"] == pytest.approx(100 * 5 / 8, abs=0.1)
    assert ql["completion"]["rate"] == pytest.approx(100 * 6 / 8, abs=0.1)
    assert {a["key"]: a["count"] for a in ql["attempts"]} == {
        "reask": 1,
        "image_rejected": 1,
        "interrupted": 0,
    }
    assert sf["sos"] == {
        "count": 2,
        "success": 1,
        "failed": 0,
        "confirm_p50_seconds": sf["sos"]["confirm_p50_seconds"],
        "cancelled_within_5s": 1,
    }
    assert 7.5 <= sf["sos"]["confirm_p50_seconds"] <= 10
    levels = sf["alert_live"]["levels"]
    assert [lv["count"] for lv in levels] == [0, 1, 1, 0]
    assert sum(lv["share_pct"] for lv in levels) == 100
    assert eng["active_owners"] == 1
    assert eng["new_users"] == 1
    assert eng["period_days"] == 7


@pytest.mark.usefixtures("db_session")
async def test_null_for_unsourced_metrics_and_empty_period(
    postgres_session_factory: AsyncSessionFactory,
) -> None:
    """No data -> zeros/[]/null, never 404/500; install/battery always null."""
    await make_admin(postgres_session_factory)
    async with admin_client(postgres_session_factory) as client:
        token = await login(client)
        community = await _get(client, token, "community")
        ov = await _get(client, token, "overview")
        ql = await _get(client, token, "quality")
        sf = await _get(client, token, "safety")
        errors = await _get(client, token, "errors")
    for key in (
        "install_attempts",
        "install_success_rate",
        "install_p50_seconds",
        "battery_to_20_p50_seconds",
    ):
        assert community[key] is None
    assert community["install_errors"] == []
    assert ov["totals"]["uses"] == 0
    assert ov["totals"]["completion_rate"] is None
    assert ql["speed"]["first_audio_p50_ms"] is None
    assert all(lv["share_pct"] is None for lv in sf["alert_live"]["levels"])
    assert errors["items"] == []


async def test_errors_list_paging_and_change(
    db_session: AsyncSession, postgres_session_factory: AsyncSessionFactory
) -> None:
    """Errors table: change vs previous week, filters, VN timestamps."""
    await _seed(db_session, postgres_session_factory)
    await make_admin(postgres_session_factory)
    async with admin_client(postgres_session_factory) as client:
        token = await login(client)
        table = await _get(client, token, "errors")
        connect = await _get(client, token, "errors", group="Connect")
        go = await _get(client, token, "errors", group="go")
    (row,) = table["items"]
    assert (row["code"], row["feature"], row["group"], row["count"], row["change"]) == (
        "NET_TIMEOUT",
        "chat",
        "Connect",
        1,
        0,
    )
    assert row["last_seen"].endswith("+07:00")
    assert table["available_groups"] == ["Connect"]
    assert connect["total"] == 1
    assert go["total"] == 0


@pytest.mark.usefixtures("db_session")
async def test_period_validation_and_previous_same_length(
    postgres_session_factory: AsyncSessionFactory,
) -> None:
    """Reject from > to and > 400 days with 400; range echoes the same-length previous period."""
    await make_admin(postgres_session_factory)
    async with admin_client(postgres_session_factory) as client:
        token = await login(client)
        backwards = await client.get(
            "/api/v1/admin/stats/overview",
            params={"period": "custom", "from": "2026-11-10", "to": "2026-11-01"},
            headers=auth(token),
        )
        too_long = await client.get(
            "/api/v1/admin/stats/overview",
            params={"period": "custom", "from": "2025-01-01", "to": "2026-11-01"},
            headers=auth(token),
        )
        month = await _get(
            client, token, "overview", period="month", **{"from": "2026-11-01", "to": "2026-11-30"}
        )
        anonymous = await client.get("/api/v1/admin/stats/overview", params=WEEK)
    assert backwards.status_code == too_long.status_code == 400
    assert backwards.json()["error"]["code"] == "invalid_request"
    assert month["range"]["compare_from"] == "2026-10-02"
    assert month["range"]["compare_to"] == "2026-10-31"
    assert len(month["series"]["points"]) == 30
    assert anonymous.status_code == 401


async def test_admin_payload_has_no_identifiers(
    db_session: AsyncSession, postgres_session_factory: AsyncSessionFactory
) -> None:
    """No pseudonym (64-hex), raw phone or owner id appears in any stats response."""
    await _seed(db_session, postgres_session_factory)
    await make_admin(postgres_session_factory)
    async with admin_client(postgres_session_factory) as client:
        token = await login(client)
        bodies = [
            json.dumps(await _get(client, token, name))
            for name in (
                "overview",
                "see-read",
                "find",
                "connect",
                "go",
                "community",
                "time",
                "quality",
                "engagement",
                "safety",
                "errors",
            )
        ]
    for body in bodies:
        assert not re.search(r"[0-9a-f]{64}", body)
        assert "0912345612" not in body
        assert OWNER not in body
        assert '"owner_hash"' not in body
        assert '"device_hash"' not in body


async def _session_token(db: AsyncSession, public_id: str, phone: str | None = None) -> str:
    """Session token for ``public_id`` (created as a verified user when ``phone`` is given)."""
    if phone is not None:
        db.add(
            User(
                public_user_id=public_id, phone_number=phone, password_hash="x", phone_verified=True
            )
        )
        await db.commit()
    user = (await db.execute(select(User).where(User.public_user_id == public_id))).scalar_one()
    token = secrets.token_urlsafe(24)
    db.add(
        Session(
            user_id=user.id,
            token_hash=hash_token(token),
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        )
    )
    await db.commit()
    return token


async def test_me_stats_owner_isolated(
    db_session: AsyncSession, postgres_session_factory: AsyncSessionFactory
) -> None:
    """The owner sees exactly their uses; another account sees zeros, never 404/500."""
    await _seed(db_session, postgres_session_factory)
    async with admin_client(postgres_session_factory) as client:
        own = await client.get(
            "/api/v1/me/stats",
            params={"from": "2026-11-09", "to": "2026-11-15"},
            headers=auth(await _session_token(db_session, OWNER)),
        )
        other = await client.get(
            "/api/v1/me/stats",
            params={"from": "2026-11-09", "to": "2026-11-15"},
            headers=auth(await _session_token(db_session, "U-B", "0987654321")),
        )
        anonymous = await client.get(
            "/api/v1/me/stats", params={"from": "2026-11-09", "to": "2026-11-15"}
        )
        backwards = await client.get(
            "/api/v1/me/stats",
            params={"from": "2026-11-15", "to": "2026-11-09"},
            headers=auth(await _session_token(db_session, OWNER)),
        )
    assert own.status_code == 200, own.text
    mine = own.json()
    assert mine["totals"]["uses"] == 12
    assert mine["totals"]["sos"] == 2
    assert mine["totals"]["alert_mode_seconds"] == 1800
    assert sum(d["uses"] for d in mine["by_day"]) == 12
    theirs = other.json()
    assert theirs["totals"]["uses"] == 0
    assert theirs["totals"]["completion_rate"] is None
    assert theirs["features"] == []
    assert anonymous.status_code == 401
    assert backwards.status_code == 400
    assert backwards.json()["error"]["code"] == "INVALID_REQUEST"
