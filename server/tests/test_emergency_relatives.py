"""PostgreSQL-backed tests for the emergency chain: texts to all, calls in priority, 3 rounds."""

from __future__ import annotations

import asyncio
import hashlib
import logging
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select, text

from app.actions import Action
from app.api.calls import get_call_push_adapter
from app.application import create_app
from app.database import build_session_factory
from app.models.call import Call, CallCallback
from app.models.emergency import Emergency
from app.models.enums import CallbackState, RequestState
from app.models.message import Message
from app.models.operation import Operation
from app.repositories.glasses_device import GlassesDeviceRepository
from app.repositories.operation import (
    DeviceReport,
    OperationRepository,
    ReportExecutionState,
)
from app.repositories.user import UserRepository
from app.workers.emergency import EmergencyWorker
from app.workers.timeout import TimeoutWorker
from tests.helpers import PUBLIC_TOKEN, make_settings
from tests.test_calls_api import RecordingPush, _register_phone
from tests.test_care_api import _session

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from sqlalchemy.ext.asyncio import AsyncEngine

    from app.config import Settings

SVC = {"Authorization": f"Bearer {PUBLIC_TOKEN}"}
LOCATION_TEXT = "KHẨN CẤP: Chủ kính cần giúp. Vị trí: https://www.google.com/maps?q=10.77,106.7"
NO_LOCATION_TEXT = "KHẨN CẤP: Chủ kính cần giúp."


@dataclass
class World:
    """An owner with glasses and relatives (name -> headers/id/push token) of mixed priority."""

    client: AsyncClient
    engine: AsyncEngine
    settings: Settings
    push: RecordingPush
    owner_id: str
    glasses_id: str
    sessions: dict[str, dict[str, str]] = field(default_factory=dict)
    worker: EmergencyWorker | None = None

    @property
    def factory(self):  # noqa: ANN201
        """Session factory on the test database."""
        return build_session_factory(self.engine)

    async def start(  # noqa: ANN201
        self,
        n_tokens: int = 6,
        request_id: uuid.UUID | None = None,
        *,
        send_user_id: bool = True,
    ):
        """Glasses server starts an emergency with ``n_tokens`` media tokens."""
        return await self.client.post(
            "/api/v1/service/emergency/start",
            json={
                **({"user_id": self.owner_id} if send_user_id else {}),
                "glasses_device_id": self.glasses_id,
                "request_id": str(request_id or uuid.uuid4()),
                "media_tokens": [f"media-token-{i:04d}" for i in range(n_tokens)],
            },
            headers=SVC,
        )

    async def rows(self, model: type) -> list:
        """All persisted rows of ``model`` (oldest first)."""
        async with self.factory() as session:
            return list((await session.execute(select(model))).scalars())

    async def calls(self) -> list[Call]:
        """Return calls in creation order."""
        async with self.factory() as session:
            result = await session.execute(select(Call).order_by(Call.created_at, Call.id))
            return list(result.scalars())

    async def emergency(self) -> Emergency:
        """Return the only emergency chain."""
        (row,) = await self.rows(Emergency)
        return row

    def pushes_to(self, name: str, kind: str) -> list[dict[str, str]]:
        """FCM data messages of type ``kind`` sent to ``name``'s phone."""
        token = f"push-{name}"
        return [d for t, d in self.push.sent if t == token and d["type"] == kind]

    async def reject_current(self, name: str) -> None:
        """Make the relative called ``name`` decline the call now ringing."""
        call = (await self.calls())[-1]
        res = await self.client.post(f"/api/v1/calls/{call.id}/reject", headers=self.sessions[name])
        assert res.status_code == 200, res.text

    async def phone_reports_location(self, lat: float, lng: float) -> None:
        """Play the owner's phone: report the next ``location_get`` operation that appears."""
        for _ in range(100):
            async with self.factory() as session:
                ops = list(
                    (
                        await session.execute(
                            select(Operation).where(Operation.action == Action.LOCATION_GET)
                        )
                    ).scalars()
                )
                if ops:
                    op = ops[0]
                    await OperationRepository(session).record_device_report(
                        DeviceReport(
                            request_id=op.request_id,
                            execution_state=ReportExecutionState.SUCCEEDED,
                            result={
                                "lat": lat,
                                "lng": lng,
                                "address": None,
                                "captured_at": datetime.now(UTC).isoformat(),
                            },
                            error=None,
                            report_payload_hash="b" * 64,
                            callback_state=CallbackState.NOT_REQUIRED,
                            next_callback_at=None,
                        )
                    )
                    await session.commit()
                    return
            await asyncio.sleep(0.05)
        pytest.fail("no location_get operation appeared")

    async def run_messages(self, *, location: tuple[float, float] | None) -> None:
        """One message-worker pass while the phone answers (or not) the location lookup."""
        assert self.worker is not None
        if location is None:
            await self.worker.run_messages_once()
            return
        await asyncio.gather(
            self.worker.run_messages_once(), self.phone_reports_location(*location)
        )

    async def expire_ringing(self) -> None:
        """Let the current ring time out (timeout worker marks it missed)."""
        later = datetime.now(UTC) + timedelta(seconds=self.settings.call_ring_timeout_s + 1)
        await TimeoutWorker(self.factory, self.settings).run_once(now=later)


async def _make_world(
    db_engine: AsyncEngine,
    caplog: pytest.LogCaptureFixture,
    relatives: list[tuple[str, str, bool, int]],
    **overrides: object,
) -> AsyncIterator[World]:
    settings = make_settings(
        public_api_scopes={"service:execute", "requests:read", "calls:manage"},
        callback_url="http://vh.test/internal/action-results",
        callback_token="cb-secret-token",
        callback_allowed_hosts="vh.test",
        **overrides,
    )
    app = create_app(settings)
    logging.getLogger().addHandler(caplog.handler)
    app.state.session_factory = build_session_factory(db_engine)
    push = RecordingPush()
    app.dependency_overrides[get_call_push_adapter] = lambda: push
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        owner = await _session(client, caplog, "0961000001")
        sessions: dict[str, dict[str, str]] = {}
        for i, (name, _, _, _) in enumerate(relatives, start=2):
            sessions[name] = await _session(client, caplog, f"096100000{i}")
        async with build_session_factory(db_engine)() as session:
            users = UserRepository(session)
            owner_id = (await users.get_by_phone("0961000001")).public_user_id  # type: ignore[union-attr]
            glasses_id = f"glasses-{uuid.uuid4()}"
            await GlassesDeviceRepository(session).link(user_id=owner_id, device_id=glasses_id)
            await session.commit()
        world = World(client, db_engine, settings, push, owner_id, glasses_id, sessions)
        for i, (name, _, emergency, priority) in enumerate(relatives, start=2):
            async with build_session_factory(db_engine)() as session:
                rel_id = (
                    await UserRepository(session).get_by_phone(f"096100000{i}")
                ).public_user_id  # type: ignore[union-attr]
            await _register_phone(db_engine, rel_id, f"push-{name}")
            invite = await client.post(
                "/api/v1/care/invite",
                json={
                    "phone_number": f"096100000{i}",
                    "display_name": name,
                    "emergency": emergency,
                    "priority": priority,
                },
                headers=owner,
            )
            assert invite.status_code == 200, invite.text
            accepted = await client.post(
                f"/api/v1/care/{invite.json()['data']['id']}/accept", headers=sessions[name]
            )
            assert accepted.status_code == 200, accepted.text
        world.worker = EmergencyWorker(build_session_factory(db_engine), settings, adapter=push)
        yield world
    # operations không có FK tới users nên TRUNCATE users không dọn: xoá lệnh lấy vị trí của test,
    # kẻo hàng PROCESSING còn lại bị TimeoutWorker của test khác đếm vào.
    async with db_engine.begin() as connection:
        await connection.execute(
            text("DELETE FROM operations WHERE user_id = :uid"), {"uid": owner_id}
        )


RELATIVES = [
    ("Chú", "", True, 3),
    ("Bố", "", True, 1),
    ("Cô", "", False, 0),  # không phải liên hệ khẩn cấp dù priority nhỏ nhất
    ("Mẹ", "", True, 2),
]


@pytest.fixture
async def world(
    db_engine: AsyncEngine,
    clean_auth_tables: None,  # noqa: ARG001
    caplog: pytest.LogCaptureFixture,
) -> AsyncIterator[World]:
    """Three emergency relatives (Bố 1, Mẹ 2, Chú 3) and one ordinary relative (Cô)."""
    async for w in _make_world(db_engine, caplog, RELATIVES):
        yield w


@pytest.fixture
async def world_two(
    db_engine: AsyncEngine,
    clean_auth_tables: None,  # noqa: ARG001
    caplog: pytest.LogCaptureFixture,
) -> AsyncIterator[World]:
    """Two emergency relatives (Bố 1, Mẹ 2)."""
    async for w in _make_world(db_engine, caplog, [RELATIVES[1], RELATIVES[3]]):
        yield w


@pytest.fixture
async def world_no_contact(
    db_engine: AsyncEngine,
    clean_auth_tables: None,  # noqa: ARG001
    caplog: pytest.LogCaptureFixture,
) -> AsyncIterator[World]:
    """Only an ordinary (non-emergency) relative."""
    async for w in _make_world(db_engine, caplog, [RELATIVES[2]]):
        yield w


@pytest.fixture
async def world_no_location(
    db_engine: AsyncEngine,
    clean_auth_tables: None,  # noqa: ARG001
    caplog: pytest.LogCaptureFixture,
) -> AsyncIterator[World]:
    """One emergency relative and a 0.3 s location cap (the phone never answers)."""
    async for w in _make_world(db_engine, caplog, [RELATIVES[1]], emergency_location_timeout_s=0.3):
        yield w


async def test_emergency_messages_all_then_calls_in_priority(world: World) -> None:
    """Tin có vị trí tới đủ 3 người khẩn cấp (không tới "Cô"); gọi Bố -> Mẹ -> Chú theo priority."""
    started = await world.start()
    assert started.status_code == 200, started.text
    data = started.json()["data"]
    assert (data["state"], data["relative"], data["contacts"]) == (
        "running",
        {"display_name": "Bố"},
        3,
    )
    # Gọi ngay, người đầu tiên là priority nhỏ nhất, cờ emergency tới app.
    (ring,) = world.pushes_to("Bố", "call_incoming")
    assert (ring["call_id"], ring["emergency"]) == (data["call_id"], "true")
    replay = await world.start(request_id=(await world.emergency()).request_id)
    assert replay.json()["data"]["emergency_id"] == data["emergency_id"]  # phát lại: cùng chuỗi
    assert len(await world.calls()) == 1

    await world.run_messages(location=(10.77, 106.7))
    for name in ("Bố", "Mẹ", "Chú"):
        (msg,) = world.pushes_to(name, "message_in")
        assert (msg["from_display_name"], msg["text"]) == ("Chủ kính", LOCATION_TEXT)
    assert world.pushes_to("Cô", "message_in") == []
    assert len(await world.rows(Message)) == 3
    await world.run_messages(location=None)  # chạy lại không nhắn đôi
    assert len(await world.rows(Message)) == 3
    (location_op,) = await world.rows(Operation)  # lấy vị trí không phát callback tới VH
    assert location_op.request_state is RequestState.SUCCEEDED
    assert location_op.callback_state is CallbackState.NOT_REQUIRED

    for name in ("Bố", "Mẹ"):
        await world.reject_current(name)
        assert await world.worker.run_chain_once() == 1  # type: ignore[union-attr]
    assert await world.worker.run_chain_once() == 0  # type: ignore[union-attr]  # Chú đang chuông
    calls = await world.calls()
    assert [(c.peer_display_name, c.emergency) for c in calls] == [
        ("Bố", True),
        ("Mẹ", True),
        ("Chú", True),
    ]
    assert [c.media_token_hash for c in calls] == [
        hashlib.sha256(f"media-token-{i:04d}".encode()).hexdigest() for i in range(3)
    ]
    assert [d["emergency"] for d in world.pushes_to("Chú", "call_incoming")] == ["true"]


async def test_emergency_stops_on_answer(world: World) -> None:
    """Có người nghe máy thì chuỗi dừng: không gọi thêm ai, token chưa dùng bị xoá."""
    assert (await world.start()).status_code == 200
    await world.reject_current("Bố")
    await world.worker.run_chain_once()  # type: ignore[union-attr]
    call = (await world.calls())[-1]
    assert call.peer_display_name == "Mẹ"
    answered = await world.client.post(
        f"/api/v1/calls/{call.id}/answer", headers=world.sessions["Mẹ"]
    )
    assert answered.status_code == 200, answered.text

    assert await world.worker.run_chain_once() == 1  # type: ignore[union-attr]
    assert await world.worker.run_chain_once() == 0  # type: ignore[union-attr]
    emergency = await world.emergency()
    assert (emergency.state.value, emergency.media_tokens) == ("answered", [])
    assert len(await world.calls()) == 2  # Chú không bị gọi
    events = [c.payload for c in await world.rows(CallCallback)]
    assert [e["event"] for e in events] == ["call_rejected", "call_answered"]
    assert all(e["emergency"] is True for e in events)
    # Hết giờ chuông sau đó cũng không mở lại chuỗi.
    await world.expire_ringing()
    await world.worker.run_chain_once()  # type: ignore[union-attr]
    assert len(await world.calls()) == 2


async def test_emergency_three_rounds_then_exhausted(world_two: World) -> None:
    """Không ai nghe: Bố, Mẹ, Bố, Mẹ, Bố, Mẹ (3 vòng) rồi dừng và báo kính đúng một lần."""
    assert (await world_two.start(n_tokens=9)).status_code == 200
    for _ in range(6):
        await world_two.expire_ringing()
        await world_two.worker.run_chain_once()  # type: ignore[union-attr]
    calls = await world_two.calls()
    assert [c.peer_display_name for c in calls] == ["Bố", "Mẹ"] * 3
    assert {c.state.value for c in calls} == {"missed"}
    emergency = await world_two.emergency()
    assert (emergency.state.value, emergency.media_tokens) == ("exhausted", [])

    await world_two.expire_ringing()
    assert await world_two.worker.run_chain_once() == 0  # type: ignore[union-attr]
    assert len(await world_two.calls()) == 6  # không có lượt thứ 7
    events = [c.payload for c in await world_two.rows(CallCallback)]
    assert [e["event"] for e in events][-2:] == ["call_missed", "emergency_exhausted"]
    assert [e["event"] for e in events].count("emergency_exhausted") == 1
    assert events[-1] == {
        "event": "emergency_exhausted",
        "call_id": str(calls[-1].id),
        "emergency_id": str(emergency.id),
        "request_id": str(emergency.request_id),
        "glasses_device_id": world_two.glasses_id,
        "peer_display_name": "Mẹ",
        "direction": "to_relative",
        "emergency": True,
        "reason": "exhausted",
    }


async def test_emergency_stop(world: World) -> None:
    """Bấm dừng: huỷ cuộc đang chuông ngay, không nhắn, không gọi tiếp; dừng lặp là no-op."""
    started = await world.start()
    emergency_id = started.json()["data"]["emergency_id"]
    world.push.sent.clear()
    for _ in range(2):
        res = await world.client.post(f"/api/v1/service/emergency/{emergency_id}/stop", headers=SVC)
        assert res.status_code == 200, res.text
        assert res.json()["data"] == {"emergency_id": emergency_id, "state": "stopped", "call_id": None}
    (ended,) = world.pushes_to("Bố", "call_ended")
    assert ended["reason"] == "cancelled"

    await world.worker.run_chain_once()  # type: ignore[union-attr]
    await world.run_messages(location=None)
    assert [c.state.value for c in await world.calls()] == ["ended"]
    assert await world.rows(Message) == []
    assert [d for _, d in world.push.sent if d["type"] == "message_in"] == []
    missing = await world.client.post(f"/api/v1/service/emergency/{uuid.uuid4()}/stop", headers=SVC)
    assert (missing.status_code, missing.json()["error"]["code"]) == (404, "EMERGENCY_NOT_FOUND")


async def test_location_failure_still_messages(world_no_location: World) -> None:
    """Điện thoại không trả vị trí trong trần: vẫn nhắn, bỏ phần " Vị trí: …"."""
    world = world_no_location
    assert (await world.start()).status_code == 200
    began = asyncio.get_running_loop().time()
    await world.run_messages(location=None)
    assert asyncio.get_running_loop().time() - began < 3  # trần 0,3 s, không chờ 30 s
    (msg,) = world.pushes_to("Bố", "message_in")
    assert msg["text"] == NO_LOCATION_TEXT
    assert [m.text for m in await world.rows(Message)] == [NO_LOCATION_TEXT]


async def test_start_without_emergency_contact(world_no_contact: World) -> None:
    """Không có người thân khẩn cấp: 404 ngay (kính báo ngay), không tạo chuỗi hay cuộc gọi."""
    res = await world_no_contact.start()
    assert (res.status_code, res.json()["error"]["code"]) == (404, "EMERGENCY_CONTACT_NOT_FOUND")
    assert await world_no_contact.rows(Emergency) == []
    assert await world_no_contact.calls() == []


async def test_start_without_user_id_derives_owner(world: World) -> None:
    """Không gửi user_id: AB suy chủ từ kính đã xác thực và vẫn chạy chuỗi bình thường."""
    started = await world.start(send_user_id=False)
    assert started.status_code == 200, started.text
    assert (await world.emergency()).owner_user_id == world.owner_id


async def test_stop_after_answer_returns_active_call_id(world: World) -> None:
    """Minor 7: người thân vừa nghe máy rồi VH mới bấm dừng -> state `answered` + call_id để VH cúp cuộc vẫn active."""
    started = await world.start()
    emergency_id = started.json()["data"]["emergency_id"]
    call = (await world.calls())[-1]
    peer = call.peer_display_name
    answered = await world.client.post(f"/api/v1/calls/{call.id}/answer", headers=world.sessions[peer])
    assert answered.status_code == 200, answered.text
    res = await world.client.post(f"/api/v1/service/emergency/{emergency_id}/stop", headers=SVC)
    assert res.status_code == 200, res.text
    assert res.json()["data"] == {"emergency_id": emergency_id, "state": "answered", "call_id": str(call.id)}
    assert (await world.calls())[-1].state.value == "active"  # AB không tự cúp người đã nghe máy
