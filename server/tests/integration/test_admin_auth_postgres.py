"""PostgreSQL tests for dashboard admin sign-in, sessions and own-profile edits."""

from __future__ import annotations

import asyncio
import io
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

import pytest
from scripts.create_admin import main as create_admin_main
from sqlalchemy import select, update

from app.application import create_app
from app.models.admin import AdminSession, AdminUser
from app.services.admin_auth import LoginThrottle
from tests.integration.admin_helpers import PASSWORD, admin_client, auth, login, make_admin
from tests.integration.usage_helpers import usage_settings

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from app.database import AsyncSessionFactory

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64


def _error(body: dict[str, Any]) -> tuple[str, str]:
    assert set(body) == {"error"}
    assert set(body["error"]) == {"code", "message"}
    return body["error"]["code"], body["error"]["message"]


async def test_login_ok_and_me(
    db_session: AsyncSession, postgres_session_factory: AsyncSessionFactory
) -> None:
    """Login returns a token + contract-shaped user; /me returns the same user."""
    await make_admin(postgres_session_factory)
    async with admin_client(postgres_session_factory) as client:
        response = await client.post(
            "/api/v1/admin/auth/login", json={"email": " KH@Example.com ", "password": PASSWORD}
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["expires_in"] == 3600
        user = body["user"]
        assert user["id"].startswith("AD-")
        assert len(user["id"]) == 7
        assert user["email_masked"] == "kh***@example.com"
        assert user["role"] == "admin_readonly"
        assert user["timezone"] == "Asia/Ho_Chi_Minh"
        assert user["avatar_url"] is None
        assert user["last_login_at"].endswith("+07:00")
        me = await client.get("/api/v1/admin/auth/me", headers=auth(body["token"]))
    assert me.status_code == 200
    assert me.json() == {"user": user}
    stored = (await db_session.execute(select(AdminSession))).scalar_one()
    assert stored.token_hash != body["token"]


@pytest.mark.usefixtures("db_session")
async def test_login_wrong_401(postgres_session_factory: AsyncSessionFactory) -> None:
    """Wrong password and unknown email look the same: 401 invalid_credentials."""
    await make_admin(postgres_session_factory)
    async with admin_client(postgres_session_factory) as client:
        wrong = await client.post(
            "/api/v1/admin/auth/login", json={"email": "kh@example.com", "password": "Sai12345"}
        )
        unknown = await client.post(
            "/api/v1/admin/auth/login", json={"email": "no@example.com", "password": PASSWORD}
        )
    assert wrong.status_code == unknown.status_code == 401
    assert _error(wrong.json())[0] == _error(unknown.json())[0] == "invalid_credentials"


@pytest.mark.usefixtures("db_session")
async def test_login_throttled_after_repeated_failures(
    postgres_session_factory: AsyncSessionFactory,
) -> None:
    """Five failures for one email lock it for the window, even with the right password."""
    await make_admin(postgres_session_factory)
    async with admin_client(postgres_session_factory) as client:
        for _ in range(5):
            await client.post(
                "/api/v1/admin/auth/login", json={"email": "kh@example.com", "password": "x"}
            )
        locked = await client.post(
            "/api/v1/admin/auth/login", json={"email": "kh@example.com", "password": PASSWORD}
        )
    assert locked.status_code == 429
    assert _error(locked.json())[0] == "too_many_attempts"


async def test_token_expired_401(
    db_session: AsyncSession, postgres_session_factory: AsyncSessionFactory
) -> None:
    """An expired session is rejected with the dashboard envelope."""
    await make_admin(postgres_session_factory)
    async with admin_client(postgres_session_factory) as client:
        token = await login(client)
        await db_session.execute(
            update(AdminSession).values(expires_at=datetime.now(UTC) - timedelta(seconds=1))
        )
        await db_session.commit()
        response = await client.get("/api/v1/admin/auth/me", headers=auth(token))
        missing = await client.get("/api/v1/admin/auth/me")
    assert response.status_code == missing.status_code == 401
    assert _error(response.json())[0] == "unauthorized"


@pytest.mark.usefixtures("db_session")
async def test_logout_revokes(postgres_session_factory: AsyncSessionFactory) -> None:
    """After logout the same token is 401."""
    await make_admin(postgres_session_factory)
    async with admin_client(postgres_session_factory) as client:
        token = await login(client)
        out = await client.post("/api/v1/admin/auth/logout", headers=auth(token))
        after = await client.get("/api/v1/admin/auth/me", headers=auth(token))
    assert out.json() == {"ok": True}
    assert after.status_code == 401


@pytest.mark.usefixtures("db_session")
async def test_password_rules(postgres_session_factory: AsyncSessionFactory) -> None:
    """Wrong current -> 400 (never 401); weak/same -> 422 with the contract codes."""
    await make_admin(postgres_session_factory)
    async with admin_client(postgres_session_factory) as client:
        token = await login(client)

        async def change(current: str, new: str) -> tuple[int, str]:
            r = await client.post(
                "/api/v1/admin/auth/password",
                json={"current_password": current, "new_password": new},
                headers=auth(token),
            )
            return r.status_code, _error(r.json())[0]

        assert await change("Sai12345", "Moi12345") == (400, "invalid_current_password")
        assert await change(PASSWORD, "short1") == (422, "weak_password")
        assert await change(PASSWORD, "khongcoso") == (422, "weak_password")
        assert await change(PASSWORD, "12345678") == (422, "weak_password")
        assert await change(PASSWORD, PASSWORD) == (422, "same_password")


@pytest.mark.usefixtures("db_session")
async def test_password_change_keeps_current_session(
    postgres_session_factory: AsyncSessionFactory,
) -> None:
    """Changing the password revokes other sessions but keeps the current one."""
    await make_admin(postgres_session_factory)
    async with admin_client(postgres_session_factory) as client:
        current = await login(client)
        other = await login(client)
        ok = await client.post(
            "/api/v1/admin/auth/password",
            json={"current_password": PASSWORD, "new_password": "Moi123456"},
            headers=auth(current),
        )
        assert ok.json() == {"ok": True}
        assert (await client.get("/api/v1/admin/auth/me", headers=auth(current))).status_code == 200
        assert (await client.get("/api/v1/admin/auth/me", headers=auth(other))).status_code == 401
        assert await login(client, password="Moi123456")


def _multipart(data: bytes, filename: str = "a.jpg") -> dict[str, Any]:
    return {"files": {"file": (filename, data, "image/jpeg")}}


@pytest.mark.usefixtures("db_session")
async def test_avatar_type_and_size(postgres_session_factory: AsyncSessionFactory) -> None:
    """PNG accepted as data URL; non-image 422; > 256 KB 413; delete -> null."""
    await make_admin(postgres_session_factory)
    async with admin_client(postgres_session_factory) as client:
        token = await login(client)
        put = await client.put("/api/v1/admin/auth/avatar", headers=auth(token), **_multipart(PNG))
        assert put.status_code == 200, put.text
        assert put.json()["user"]["avatar_url"].startswith("data:image/png;base64,")

        bad = await client.put(
            "/api/v1/admin/auth/avatar", headers=auth(token), **_multipart(b"GIF89a....")
        )
        assert (bad.status_code, _error(bad.json())[0]) == (422, "invalid_image")

        big = await client.put(
            "/api/v1/admin/auth/avatar",
            headers=auth(token),
            **_multipart(b"\xff\xd8\xff" + b"\x00" * (256 * 1024)),
        )
        assert (big.status_code, _error(big.json())[0]) == (413, "image_too_large")

        none = await client.put("/api/v1/admin/auth/avatar", headers=auth(token), content=b"x")
        assert none.status_code == 422

        gone = await client.delete("/api/v1/admin/auth/avatar", headers=auth(token))
        assert gone.json()["user"]["avatar_url"] is None


@pytest.mark.usefixtures("db_session")
async def test_error_envelope_shape(postgres_session_factory: AsyncSessionFactory) -> None:
    """Admin routes use {"error": {code, message}}; other routes keep their envelope."""
    async with admin_client(postgres_session_factory) as client:
        invalid = await client.post("/api/v1/admin/auth/login", json={"email": 1})
        unknown = await client.get("/api/v1/admin/nope", headers=auth("x"))
        public = await client.post("/api/v1/auth/login", json={})
    assert (invalid.status_code, _error(invalid.json())[0]) == (400, "invalid_request")
    assert (unknown.status_code, _error(unknown.json())[0]) == (404, "not_found")
    assert public.json()["status"] == "error"


async def test_create_admin_script_reads_stdin(
    db_session: AsyncSession,
    postgres_session_factory: AsyncSessionFactory,
    test_database_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The script takes the password from stdin, rejects weak ones and stores a scrypt hash."""
    monkeypatch.setenv("DATABASE_URL", test_database_url)
    argv = ["--email", "New@Example.com", "--name", "Quản trị"]
    assert await asyncio.to_thread(create_admin_main, argv, io.StringIO("yeu\n")) == 1
    assert await asyncio.to_thread(create_admin_main, argv, io.StringIO("Manh12345\n")) == 0
    assert await asyncio.to_thread(create_admin_main, argv, io.StringIO("Manh12345\n")) == 1
    admin = (await db_session.execute(select(AdminUser))).scalar_one()
    assert admin.email == "new@example.com"
    assert admin.password_hash.startswith("scrypt$")
    assert "Manh12345" not in admin.password_hash
    async with admin_client(postgres_session_factory) as client:
        assert await login(client, email="new@example.com", password="Manh12345")


@pytest.mark.usefixtures("db_session")
async def test_every_admin_route_requires_a_session(
    postgres_session_factory: AsyncSessionFactory,
) -> None:
    """Every /api/v1/admin route except login answers 401 without a valid token."""
    async with admin_client(postgres_session_factory) as client:
        app = create_app(usage_settings("unused"))
        routes = [
            (method.upper(), path)
            for path, item in app.openapi()["paths"].items()
            if path.startswith("/api/v1/admin") and path != "/api/v1/admin/auth/login"
            for method in item
        ]
        assert len(routes) >= 20
        for method, path in routes:
            url = path.replace("{account_id}", "x").replace("{device_id}", "x")
            for headers in ({}, auth("not-a-token")):
                response = await client.request(method, url, headers=headers)
                assert response.status_code == 401, (method, path, response.status_code)
                assert response.json()["error"]["code"] == "unauthorized"


def test_login_throttle_memory_is_bounded() -> None:
    """Spraying many emails cannot grow the failure table without limit."""
    throttle = LoginThrottle()
    for i in range(10_050):
        throttle.fail(f"user{i}@example.com")
    assert len(throttle._failures) <= 10_000  # noqa: SLF001


def test_login_throttle_limits_one_ip_across_emails() -> None:
    """One client spraying many emails is stopped by the per-IP limit; other clients are not."""
    throttle = LoginThrottle(max_failures_ip=50)
    for i in range(50):
        throttle.fail(f"user{i}@example.com", "203.0.113.7")
    assert throttle.blocked("fresh@example.com", "203.0.113.7")
    assert not throttle.blocked("fresh@example.com", "198.51.100.1")
    assert not throttle.blocked("fresh@example.com")


def test_login_throttle_spray_keeps_locked_email() -> None:
    """Filling the table with one-off emails must not unlock an email that is already locked."""
    throttle = LoginThrottle()
    for _ in range(5):
        throttle.fail("kh@example.com")
    for i in range(10_050):
        throttle.fail(f"user{i}@example.com")
    assert throttle.blocked("kh@example.com")
