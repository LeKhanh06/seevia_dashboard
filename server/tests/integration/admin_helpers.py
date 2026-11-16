"""Shared builders for admin dashboard integration tests."""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

from httpx import ASGITransport, AsyncClient

from app.application import create_app
from app.database import get_db_session
from app.services.admin_auth import AdminAuthService, LoginThrottle
from tests.integration.usage_helpers import usage_settings

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, AsyncIterator

    from sqlalchemy.ext.asyncio import AsyncSession

    from app.database import AsyncSessionFactory

EMAIL = "kh@example.com"
PASSWORD = "Matkhau123"


async def make_admin(
    factory: AsyncSessionFactory, email: str = EMAIL, password: str = PASSWORD
) -> None:
    """Insert one admin through the service (same path as the CLI)."""
    async with factory() as session:
        await AdminAuthService(session, ttl_s=3600, throttle=LoginThrottle()).create_admin(
            email=email, display_name="Khánh Hà", password=password
        )


@asynccontextmanager
async def admin_client(factory: AsyncSessionFactory, **extra: object) -> AsyncIterator[AsyncClient]:
    """HTTP client on real PostgreSQL with usage settings (pseudonym key configured)."""
    app = create_app(usage_settings("unused-usage-token", **extra))

    async def _override() -> AsyncGenerator[AsyncSession]:
        async with factory() as session:
            yield session

    app.dependency_overrides[get_db_session] = _override
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
        yield client


def auth(token: str) -> dict[str, str]:
    """Bearer header."""
    return {"Authorization": f"Bearer {token}"}


async def login(client: AsyncClient, *, email: str = EMAIL, password: str = PASSWORD) -> str:
    """Log in and return the token (asserting 200)."""
    response = await client.post(
        "/api/v1/admin/auth/login", json={"email": email, "password": password}
    )
    assert response.status_code == 200, response.text
    token: str = response.json()["token"]
    return token
