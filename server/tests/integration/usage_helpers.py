"""Shared builders for usage-statistics integration tests (client, tokens, events)."""

from __future__ import annotations

import hashlib
import hmac
import secrets
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid4

from httpx import ASGITransport, AsyncClient

from app.application import create_app
from app.config import AppEnvironment, DeliveryTransport, Settings
from app.database import get_db_session

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, AsyncIterator

    from sqlalchemy.ext.asyncio import AsyncSession

    from app.database import AsyncSessionFactory

USAGE_DOMAIN = b"app-demo-auth/v1/usage-api"
ID_KEY = "ab" * 32
VN = timedelta(hours=7)


def usage_settings(raw_token: str, **extra: object) -> Settings:
    """Build valid settings with a usage writer token and pseudonym key."""
    return Settings.model_validate(
        {
            "app_env": AppEnvironment.TEST,
            "http_port": 8000,
            "database_url": "postgresql+asyncpg://test:test@localhost:5432/app_test",
            "public_api_token_hash": "1" * 64,
            "public_api_client_id": "usage-test-client",
            "public_api_scopes": {"service:execute"},
            "device_api_token_hash": "2" * 64,
            "field_encryption_key": "test-only-field-key",
            "delivery_transport": DeliveryTransport.FAKE,
            "usage_api_token_hash": hmac.new(
                raw_token.encode(), USAGE_DOMAIN, hashlib.sha256
            ).hexdigest(),
            "usage_id_key": ID_KEY,
            **extra,
        }
    )


@asynccontextmanager
async def usage_client(
    factory: AsyncSessionFactory, **extra: object
) -> AsyncIterator[tuple[AsyncClient, str]]:
    """HTTP client on real PostgreSQL plus the raw usage token."""
    raw = secrets.token_urlsafe(32)
    app = create_app(usage_settings(raw, **extra))

    async def _override() -> AsyncGenerator[AsyncSession]:
        async with factory() as session:
            yield session

    app.dependency_overrides[get_db_session] = _override
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
        yield client, raw


async def post_events(
    client: AsyncClient, token: str, events: list[dict[str, Any]]
) -> dict[str, Any]:
    """POST a batch and return the decoded JSON (asserting 200)."""
    response = await client.post(
        "/api/v1/usage/events",
        json={"events": events},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return body


def vn(local: str) -> datetime:
    """Turn a Vietnam wall-clock ``"YYYY-MM-DD HH:MM[:SS]"`` into an aware UTC datetime."""
    return datetime.fromisoformat(local).replace(tzinfo=UTC) - VN


def ev(etype: str, at: datetime, device: str = "glasses-a", **fields: object) -> dict[str, Any]:
    """Build one event dict as VH would send it."""
    return {
        "event_id": str(uuid4()),
        "type": etype,
        "occurred_at": at.isoformat(),
        "device_id": device,
        **{k: (str(v) if isinstance(v, UUID) else v) for k, v in fields.items()},
    }


def attempt(  # noqa: PLR0913 -- test builder mirrors the event fields
    feature: str,
    start: datetime,
    end: datetime,
    status: str,
    *,
    task_id: UUID | None = None,
    device: str = "glasses-a",
    first_audio: datetime | None = None,
    signals: tuple[str, ...] = (),
    error_code: str | None = None,
) -> tuple[UUID, UUID, list[dict[str, Any]]]:
    """Build the events of one attempt (start, signals, marks, finish), without task_finished."""
    attempt_id, task = uuid4(), task_id or uuid4()
    out = [
        ev("attempt_started", start, device, attempt_id=attempt_id, task_id=task, feature=feature)
    ]
    out += [ev("attempt_signal", start, device, attempt_id=attempt_id, signal=s) for s in signals]
    if first_audio is not None:
        out.append(
            ev(
                "attempt_marks",
                first_audio,
                device,
                attempt_id=attempt_id,
                first_audio_at=first_audio.isoformat(),
            )
        )
    finished: dict[str, Any] = {"attempt_id": attempt_id, "task_id": task, "status": status}
    if error_code is not None:
        finished["error_code"] = error_code
    out.append(ev("attempt_finished", end, device, **finished))
    return attempt_id, task, out


def task_done(
    feature: str, task_id: UUID, at: datetime, *, status: str, attempts: int
) -> dict[str, Any]:
    """Build the task_finished event VH emits when a task closes."""
    return ev(
        "task_finished", at, task_id=task_id, feature=feature, status=status, attempts=attempts
    )
