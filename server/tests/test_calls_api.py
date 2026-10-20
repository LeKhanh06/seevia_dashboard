"""PostgreSQL-backed tests for calls: start/answer/hangup, ring timeout, pushes and callbacks."""

from __future__ import annotations

import hashlib
import json
import logging
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import httpx
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select, text

from app.adapters.callback import CallEventAdapter
from app.adapters.delivery import DeliveryOutcome, DeliveryOutcomeStatus
from app.api.calls import get_call_push_adapter
from app.application import create_app
from app.database import build_session_factory
from app.models.call import Call, CallCallback
from app.models.enums import DevicePlatform
from app.repositories.device import DeviceRegistrationRequest, DeviceRepository
from app.repositories.glasses_device import GlassesDeviceRepository
from app.repositories.user import UserRepository
from app.workers.callback import CallbackWorker
from app.workers.timeout import TimeoutWorker
from tests.helpers import PUBLIC_TOKEN, make_settings
from tests.test_care_api import _link, _session

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from sqlalchemy.ext.asyncio import AsyncEngine

    from app.config import Settings

MEDIA_URL = "wss://vh.test/v2/call"
SVC = {"Authorization": f"Bearer {PUBLIC_TOKEN}"}


class RecordingPush:
    """Delivery adapter double: records data messages instead of calling FCM."""

    def __init__(self) -> None:
        """Start with no recorded messages."""
        self.sent: list[tuple[str, dict[str, str]]] = []

    async def send_data(
        self, *, push_token: str, data: dict[str, str], label: str
    ) -> DeliveryOutcome:
        """Record ``data`` (``label`` is only a log id)."""
        _ = label
        self.sent.append((push_token, data))
        return DeliveryOutcome(status=DeliveryOutcomeStatus.SENT)


@dataclass
class World:
    """An owner with glasses and one linked relative ("Mẹ", alias "má") with a phone."""

    client: AsyncClient
    engine: AsyncEngine
    settings: Settings
    push: RecordingPush
    owner: dict[str, str]
    relative: dict[str, str]
    owner_id: str
    relative_id: str
    glasses_id: str
    extra: dict[str, dict[str, str]] = field(default_factory=dict)

    async def start(
        self,
        alias: str = "má",
        token: str = "media-token-1234",  # noqa: S107
    ) -> httpx.Response:
        """Glasses server starts a call to ``alias``."""
        return await self.client.post(
            "/api/v1/service/call/start",
            json={
                "user_id": self.owner_id,
                "glasses_device_id": self.glasses_id,
                "contact_alias": alias,
                "media_token": token,
                "request_id": str(uuid.uuid4()),
            },
            headers=SVC,
        )

    async def rows(self, model: type) -> list:
        """All persisted rows of ``model`` (oldest first)."""
        async with build_session_factory(self.engine)() as session:
            return list((await session.execute(select(model))).scalars())


async def _register_phone(engine: AsyncEngine, user_id: str, token: str) -> None:
    async with build_session_factory(engine)() as session:
        await DeviceRepository(session).register(
            DeviceRegistrationRequest(
                user_id=user_id,
                device_id=f"phone-{uuid.uuid4()}",
                platform=DevicePlatform.ANDROID,
                push_token_ciphertext=token,
                push_token_fingerprint="a" * 64,
                last_seen_at=datetime.now(UTC),
            )
        )
        await session.commit()


@pytest.fixture
async def world(
    db_engine: AsyncEngine,
    clean_auth_tables: None,  # noqa: ARG001
    caplog: pytest.LogCaptureFixture,
) -> AsyncIterator[World]:
    """Full app on real PostgreSQL with callbacks configured and a recording push adapter."""
    settings = make_settings(
        public_api_scopes={"service:execute", "requests:read", "calls:manage"},
        callback_url="http://vh.test/internal/action-results",
        callback_token="cb-secret-token",
        callback_allowed_hosts="vh.test",
        call_media_url=MEDIA_URL,
    )
    app = create_app(settings)
    # create_app() reconfigures logging and drops pytest handler; _session() reads the OTP there.
    logging.getLogger().addHandler(caplog.handler)
    app.state.session_factory = build_session_factory(db_engine)
    push = RecordingPush()
    app.dependency_overrides[get_call_push_adapter] = lambda: push
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        owner = await _session(client, caplog, "0951000001")
        relative = await _session(client, caplog, "0951000002")
        async with build_session_factory(db_engine)() as session:
            users = UserRepository(session)
            owner_id = (await users.get_by_phone("0951000001")).public_user_id  # type: ignore[union-attr]
            relative_id = (await users.get_by_phone("0951000002")).public_user_id  # type: ignore[union-attr]
            glasses_id = f"glasses-{uuid.uuid4()}"
            await GlassesDeviceRepository(session).link(user_id=owner_id, device_id=glasses_id)
            await session.commit()
        await _register_phone(db_engine, relative_id, "relative-push-token")
        await _link(client, owner, relative, ("0951000002", "Mẹ", ["má"]))
        yield World(
            client, db_engine, settings, push, owner, relative, owner_id, relative_id, glasses_id
        )


async def test_answer_returns_media_token_once(world: World) -> None:
    """Start khớp "me"->Mẹ, FCM call_incoming đúng; token nghe máy giao một lần, DB chỉ giữ hash."""
    started = await world.start(alias="me", token="media-token-abcd")
    assert started.status_code == 200, started.text
    call_id = started.json()["data"]["call_id"]
    assert started.json()["data"]["relative"] == {"display_name": "Mẹ"}
    assert world.push.sent == [
        (
            "relative-push-token",
            {
                "type": "call_incoming",
                "call_id": call_id,
                "from_display_name": "Chủ kính",
                "emergency": "false",
            },
        )
    ]

    other = await world.client.post(f"/api/v1/calls/{call_id}/answer", headers=world.owner)
    assert other.status_code == 404  # người ngoài cuộc không dò được id

    first = await world.client.post(f"/api/v1/calls/{call_id}/answer", headers=world.relative)
    assert first.status_code == 200, first.text
    assert first.json()["data"] == {
        "call_id": call_id,
        "media_url": MEDIA_URL,
        "media_token": "media-token-abcd",
    }
    again = await world.client.post(f"/api/v1/calls/{call_id}/answer", headers=world.relative)
    assert again.status_code == 200
    assert again.json()["data"]["media_token"] is None

    (call,) = await world.rows(Call)
    assert call.state.value == "active"
    assert call.media_token_hash == hashlib.sha256(b"media-token-abcd").hexdigest()
    assert call.media_token_pending is None
    events = [c.payload for c in await world.rows(CallCallback)]
    assert events == [
        {
            "event": "call_answered",
            "call_id": call_id,
            "glasses_device_id": world.glasses_id,
            "peer_display_name": "Mẹ",
            "direction": "to_relative",
            "emergency": False,
            "reason": None,
        }
    ]


async def test_hangup_idempotent(world: World) -> None:
    """Cúp lặp (kính rồi app) là no-op: một call_ended FCM, không thêm callback sau khi đã ended."""
    call_id = (await world.start()).json()["data"]["call_id"]
    await world.client.post(f"/api/v1/calls/{call_id}/answer", headers=world.relative)
    world.push.sent.clear()

    for _ in range(2):
        res = await world.client.post(
            f"/api/v1/service/call/{call_id}/hangup", json={"reason": "hangup"}, headers=SVC
        )
        assert res.status_code == 200, res.text
        assert res.json()["data"] == {"call_id": call_id, "state": "ended", "end_reason": "hangup"}
    by_app = await world.client.post(f"/api/v1/calls/{call_id}/hangup", headers=world.relative)
    assert by_app.status_code == 200
    assert by_app.json()["data"]["state"] == "ended"

    assert [d["type"] for _, d in world.push.sent] == ["call_ended"]  # đúng một lần
    assert [c.payload["event"] for c in await world.rows(CallCallback)] == ["call_answered"]
    # Nghe máy sau khi đã cúp: từ chối, không sống lại.
    late = await world.client.post(f"/api/v1/calls/{call_id}/answer", headers=world.relative)
    assert late.status_code == 409


async def test_ring_timeout_marks_missed(world: World) -> None:
    """Hết giờ chuông: missed, callback call_missed, FCM call_ended; chạy lại không đổi gì."""
    call_id = (await world.start()).json()["data"]["call_id"]
    world.push.sent.clear()
    worker = TimeoutWorker(build_session_factory(world.engine), world.settings)

    assert await worker.run_once(now=datetime.now(UTC)) == 0  # chưa tới hạn
    later = datetime.now(UTC) + timedelta(seconds=world.settings.call_ring_timeout_s + 1)
    assert await worker.run_once(now=later) == 1
    assert await worker.run_once(now=later) == 0  # idempotent

    got = await world.client.get(f"/api/v1/calls/{call_id}", headers=world.relative)
    assert got.json()["data"]["state"] == "missed"
    assert got.json()["data"]["end_reason"] == "timeout"
    (event,) = [c.payload for c in await world.rows(CallCallback)]
    assert (event["event"], event["peer_display_name"], event["reason"]) == (
        "call_missed",
        "Mẹ",
        "timeout",
    )
    answer = await world.client.post(f"/api/v1/calls/{call_id}/answer", headers=world.relative)
    assert answer.status_code == 409
    # Token chưa giao bị xoá khi hết giờ.
    assert (await world.rows(Call))[0].media_token_pending is None


async def test_relative_without_link_cannot_call(
    world: World, caplog: pytest.LogCaptureFixture
) -> None:
    """Người lạ hoặc link pending không gọi được kính; người thân có link thì gọi và kính nghe."""
    stranger = await _session(world.client, caplog, "0951000003")
    denied = await world.client.post(
        "/api/v1/calls", json={"owner_user_id": world.owner_id}, headers=stranger
    )
    assert denied.status_code == 403
    assert denied.json()["error"]["code"] == "CALL_NOT_ALLOWED"
    invited = await world.client.post(
        "/api/v1/care/invite",
        json={"phone_number": "0951000003", "display_name": "Chú"},
        headers=world.owner,
    )
    assert invited.status_code == 200  # còn pending, chưa accept
    pending = await world.client.post(
        "/api/v1/calls", json={"owner_user_id": world.owner_id}, headers=stranger
    )
    assert pending.status_code == 403
    assert await world.rows(Call) == []

    created = await world.client.post(
        "/api/v1/calls", json={"owner_user_id": world.owner_id}, headers=world.relative
    )
    assert created.status_code == 200, created.text
    call_id = created.json()["data"]["call_id"]
    (event,) = [c.payload for c in await world.rows(CallCallback)]
    assert event["event"] == "call_incoming"
    assert (event["peer_display_name"], event["direction"]) == ("Mẹ", "to_glasses")

    answered = await world.client.post(
        f"/api/v1/service/call/{call_id}/answer",
        json={"media_token": "glasses-token-1"},
        headers=SVC,
    )
    assert answered.status_code == 200, answered.text
    assert answered.json()["data"]["state"] == "active"
    assert world.push.sent[-1] == (
        "relative-push-token",
        {
            "type": "call_answered",
            "call_id": call_id,
            "media_url": MEDIA_URL,
            "media_token": "glasses-token-1",
        },
    )
    # Thăm dò (khi lỡ FCM): token giao một lần.
    polls = [
        (await world.client.get(f"/api/v1/calls/{call_id}", headers=world.relative)).json()["data"]
        for _ in range(2)
    ]
    assert [p["media_token"] for p in polls] == ["glasses-token-1", None]


async def test_start_errors_and_callback_order(
    world: World, caplog: pytest.LogCaptureFixture
) -> None:
    """Tên lạ/trùng bị từ chối; callback giao đúng thứ tự, sự kiện sau chờ sự kiện trước thử lại."""
    unknown = await world.start(alias="bố")
    assert (unknown.status_code, unknown.json()["error"]["code"]) == (404, "CONTACT_NOT_FOUND")
    twin = await _session(world.client, caplog, "0951000004")
    await _link(world.client, world.owner, twin, ("0951000004", "Mẹ Hai", ["má"]))
    many = await world.start(alias="má")
    assert many.status_code == 409
    assert many.json()["error"]["code"] == "MULTIPLE_CONTACTS_FOUND"
    assert sorted(many.json()["error"]["details"]["candidates"]) == ["Mẹ", "Mẹ Hai"]
    assert await world.rows(Call) == []

    call_id = (await world.start(alias="mẹ hai")).json()["data"]["call_id"]
    # "mẹ hai" không có phone -> không push, nhưng call vẫn ringing; relative thứ hai nghe máy.
    answer = await world.client.post(f"/api/v1/calls/{call_id}/answer", headers=twin)
    assert answer.status_code == 200
    await world.client.post(f"/api/v1/calls/{call_id}/hangup", headers=twin)

    seen: list[tuple[str, str, str]] = []
    fail_first = {"left": 1}

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        seen.append((request.url.path, request.headers["authorization"], body["event"]))
        if fail_first["left"]:
            fail_first["left"] -= 1
            return httpx.Response(503)
        return httpx.Response(200)

    adapter = CallEventAdapter(
        world.settings, client=httpx.AsyncClient(transport=httpx.MockTransport(handler))
    )
    worker = CallbackWorker(
        build_session_factory(world.engine), world.settings, call_events=adapter
    )
    await worker.run_once()
    assert seen == [("/internal/calls", "Bearer cb-secret-token", "call_answered")]  # ended chờ
    await worker.run_once()
    assert len(seen) == 1  # chưa tới giờ thử lại
    async with world.engine.begin() as conn:
        await conn.execute(
            text("UPDATE call_callbacks SET next_attempt_at = now() - interval '1 s'")
        )
    await worker.run_once()
    await worker.run_once()
    assert [e for _, _, e in seen] == ["call_answered", "call_answered", "call_ended"]
    assert {c.state.value for c in await world.rows(CallCallback)} == {"delivered"}
