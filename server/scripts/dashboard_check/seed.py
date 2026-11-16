# ruff: noqa: INP001, I001, D103, S106, SLF001 -- e2e seeding kit with fake credentials, reuses test helpers
"""Seed the side AB (18011, test DB) with the stats test dataset over real HTTP + an admin."""

import asyncio
import os
from datetime import UTC, date, datetime, timedelta
from uuid import uuid4

import httpx
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from app.database import build_session_factory
from app.models import GlassesDevice, GlassesLinkStatus, User
from app.models.usage import UsageCall, UsageDirtyDay, UsageSos
from app.services.admin_auth import AdminAuthService, LoginThrottle
from app.services.usage_ids import pseudonym
from tests.integration import test_admin_stats_postgres as t
from tests.integration.usage_helpers import vn

BASE = "http://127.0.0.1:18011"


async def main() -> None:
    engine = create_async_engine(os.environ["DATABASE_URL"], poolclass=NullPool)
    factory = build_session_factory(engine)
    key = bytes.fromhex(os.environ["USAGE_ID_KEY"])
    async with factory() as db:
        db.add(
            User(
                public_user_id="U-A",
                phone_number="0912345612",
                password_hash="x",
                phone_verified=True,
                created_at=vn("2026-11-11 10:00:00"),
            )
        )
        db.add(
            GlassesDevice(
                user_id="U-A",
                device_id="glasses-a",
                status=GlassesLinkStatus.ACTIVE,
                linked_at=datetime(2026, 11, 1, tzinfo=UTC),
            )
        )
        await db.commit()
        await AdminAuthService(db, ttl_s=3600, throttle=LoginThrottle()).create_admin(
            email="e2e@example.com", display_name="E2E", password="E2epass123"
        )
    async with httpx.AsyncClient(base_url=BASE, timeout=120) as client:
        r = await client.post(
            "/api/v1/usage/events",
            json={"events": t._dataset()},
            headers={"Authorization": f"Bearer {os.environ['USAGE_API_TOKEN']}"},
        )
        print(
            "ingest",
            r.status_code,
            {k: v for k, v in r.json().items() if k != "rejected"},
            "rejected",
            len(r.json()["rejected"]),
        )
    owner, device = pseudonym(key, "owner", "U-A"), pseudonym(key, "device", "glasses-a")
    s0 = vn("2026-11-13 10:00:00")
    async with factory() as db:
        db.add_all(
            [
                UsageSos(
                    sos_id=uuid4(),
                    owner_hash=owner,
                    device_hash=device,
                    started_at=s0,
                    finished_at=s0 + timedelta(seconds=3),
                    status="cancelled",
                ),
                UsageSos(
                    sos_id=uuid4(),
                    owner_hash=owner,
                    device_hash=device,
                    started_at=s0 + timedelta(hours=1),
                    answered_at=s0 + timedelta(hours=1, seconds=9),
                    finished_at=s0 + timedelta(hours=1, seconds=60),
                    status="success",
                ),
                UsageCall(
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
                UsageDirtyDay(day=date(2026, 11, 13)),
                UsageDirtyDay(day=date(2026, 11, 14)),
            ]
        )
        await db.commit()
    await engine.dispose()


asyncio.run(main())
