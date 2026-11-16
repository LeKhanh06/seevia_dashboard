"""PostgreSQL tests for the read-only admin directory (accounts, devices, config)."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING
from uuid import uuid4

import pytest

from app.models import GlassesDevice, GlassesLinkStatus, User
from tests.integration.admin_helpers import admin_client, auth, login, make_admin
from tests.integration.usage_helpers import attempt, ev, post_events, usage_client

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from app.database import AsyncSessionFactory


async def _account(
    db: AsyncSession, public_id: str, phone: str, *, created: datetime, glasses: list[str]
) -> None:
    db.add(
        User(
            public_user_id=public_id,
            phone_number=phone,
            password_hash="x",
            phone_verified=True,
            created_at=created,
        )
    )
    for device_id in glasses:
        db.add(
            GlassesDevice(
                user_id=public_id,
                device_id=device_id,
                status=GlassesLinkStatus.ACTIVE,
                linked_at=created,
            )
        )
    await db.commit()


async def _seed(db: AsyncSession) -> None:
    base = datetime(2026, 11, 1, 3, tzinfo=UTC)
    await _account(db, "U-A", "0912345612", created=base, glasses=["glasses-a"])
    await _account(
        db,
        "U-B",
        "0987654321",
        created=base + timedelta(days=2),
        glasses=["glasses-b", "glasses-c"],
    )
    await _account(db, "U-C", "0900000099", created=base + timedelta(days=1), glasses=[])
    db.add(
        User(
            public_user_id="U-PENDING", phone_number="0911", password_hash="x", phone_verified=False
        )
    )
    await db.commit()


async def test_accounts_paging_sort_filter(
    db_session: AsyncSession, postgres_session_factory: AsyncSessionFactory
) -> None:
    """Server-side paging/sort/filter; unverified sign-ups are not accounts."""
    await _seed(db_session)
    await make_admin(postgres_session_factory)
    async with admin_client(postgres_session_factory) as client:
        token = await login(client)
        page1 = (
            await client.get(
                "/api/v1/admin/accounts",
                params={"sort": "glasses_count", "order": "desc", "page_size": 2},
                headers=auth(token),
            )
        ).json()
        page2 = (
            await client.get(
                "/api/v1/admin/accounts",
                params={"sort": "glasses_count", "order": "desc", "page_size": 2, "page": 2},
                headers=auth(token),
            )
        ).json()
        found = (
            await client.get("/api/v1/admin/accounts", params={"q": "u-c"}, headers=auth(token))
        ).json()
        suspended = (
            await client.get(
                "/api/v1/admin/accounts", params={"status": "suspended"}, headers=auth(token)
            )
        ).json()
        too_big = await client.get(
            "/api/v1/admin/accounts", params={"page_size": 101}, headers=auth(token)
        )
        detail = await client.get("/api/v1/admin/accounts/U-B", headers=auth(token))
        missing = await client.get("/api/v1/admin/accounts/U-PENDING", headers=auth(token))
    assert page1["total"] == 3
    assert [i["id"] for i in page1["items"]] == ["U-B", "U-A"]
    assert [i["id"] for i in page2["items"]] == ["U-C"]
    assert page1["items"][0] == {
        "id": "U-B",
        "email_masked": None,
        "phone_masked": "09******21",
        "created_at": "2026-11-03",
        "status": "active",
        "glasses_count": 2,
    }
    assert [i["id"] for i in found["items"]] == ["U-C"]
    assert suspended == {"items": [], "total": 0, "page": 1, "page_size": 10}
    assert too_big.status_code == 400
    assert {g["id"] for g in detail.json()["glasses"]} == {"glasses-b", "glasses-c"}
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "not_found"


async def test_phone_masked_never_raw(
    db_session: AsyncSession, postgres_session_factory: AsyncSessionFactory
) -> None:
    """No raw phone number appears in any directory response."""
    await _seed(db_session)
    await make_admin(postgres_session_factory)
    async with admin_client(postgres_session_factory) as client:
        token = await login(client)
        bodies = [
            (await client.get(path, headers=auth(token))).text
            for path in (
                "/api/v1/admin/accounts",
                "/api/v1/admin/accounts/U-A",
                "/api/v1/admin/devices",
                "/api/v1/admin/devices/glasses-a",
                "/api/v1/admin/config",
            )
        ]
    for body in bodies:
        for phone in ("0912345612", "0987654321", "0900000099", "kh@example.com"):
            assert phone not in body


async def test_device_online_offline(
    db_session: AsyncSession, postgres_session_factory: AsyncSessionFactory
) -> None:
    """Open + fresh connection -> online with firmware; ended/stale/none -> offline."""
    await _seed(db_session)
    await make_admin(postgres_session_factory)
    now = datetime.now(UTC)
    fresh, stale, ended = uuid4(), uuid4(), uuid4()
    _, _, failed = attempt(
        "ocr",
        now - timedelta(minutes=5),
        now - timedelta(minutes=4),
        "technical_error",
        device="glasses-a",
        error_code="NET_TIMEOUT",
    )
    _, _, cancelled = attempt(
        "call",
        now - timedelta(minutes=3),
        now - timedelta(minutes=2),
        "cancelled",
        device="glasses-a",
        error_code="superseded",
    )
    events = [
        ev(
            "session_started",
            now - timedelta(minutes=10),
            "glasses-a",
            session_id=fresh,
            kind="connection",
            firmware_version="2.1.0",
        ),
        ev(
            "session_heartbeat",
            now - timedelta(seconds=30),
            "glasses-a",
            session_id=fresh,
            kind="connection",
        ),
        ev(
            "session_started",
            now - timedelta(minutes=30),
            "glasses-b",
            session_id=stale,
            kind="connection",
            firmware_version="2.0.9",
        ),
        ev(
            "session_started",
            now - timedelta(minutes=9),
            "glasses-c",
            session_id=ended,
            kind="connection",
            firmware_version="2.1.0",
        ),
        ev(
            "session_ended",
            now - timedelta(minutes=8),
            "glasses-c",
            session_id=ended,
            kind="connection",
            end_reason="closed",
        ),
        *failed,
        *cancelled,
    ]
    async with usage_client(postgres_session_factory) as (uc, raw):
        await post_events(uc, raw, events)
    async with admin_client(postgres_session_factory) as client:
        token = await login(client)
        devices = (await client.get("/api/v1/admin/devices", headers=auth(token))).json()
        online = (
            await client.get(
                "/api/v1/admin/devices", params={"status": "online"}, headers=auth(token)
            )
        ).json()
        fw = (
            await client.get(
                "/api/v1/admin/devices",
                params={"firmware_version": "2.1.0", "sort": "id", "order": "desc"},
                headers=auth(token),
            )
        ).json()
        detail = (await client.get("/api/v1/admin/devices/glasses-a", headers=auth(token))).json()
    by_id = {d["id"]: d for d in devices["items"]}
    assert by_id["glasses-a"]["status"] == "online"
    assert by_id["glasses-a"]["firmware_version"] == "2.1.0"
    assert by_id["glasses-a"]["account_id"] == "U-A"
    assert by_id["glasses-a"]["last_seen_at"].endswith("+07:00")
    assert by_id["glasses-b"]["status"] == "offline"
    assert by_id["glasses-c"]["status"] == "offline"
    assert devices["available_firmware_versions"] == ["2.0.9", "2.1.0"]
    assert [d["id"] for d in online["items"]] == ["glasses-a"]
    assert [d["id"] for d in fw["items"]] == ["glasses-c", "glasses-a"]
    assert [(e["code"], e["feature"]) for e in detail["recent_errors"]] == [("NET_TIMEOUT", "ocr")]
    assert "device_hash" not in json.dumps(detail)


@pytest.mark.usefixtures("db_session")
async def test_config_lists_admins_masked(postgres_session_factory: AsyncSessionFactory) -> None:
    """/admin/config lists every admin with masked email only; requires a session."""
    await make_admin(postgres_session_factory)
    async with admin_client(postgres_session_factory) as client:
        anonymous = await client.get("/api/v1/admin/config")
        token = await login(client)
        body = (await client.get("/api/v1/admin/config", headers=auth(token))).json()
    assert anonymous.status_code == 401
    (only,) = body["admin_accounts"]
    assert only["email_masked"] == "kh***@example.com"
    assert set(only) == {"email_masked", "created_at", "last_login_at"}
