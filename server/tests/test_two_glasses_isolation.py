# ruff: noqa: F811
"""Hai kính, hai chủ: mỗi kính chỉ chạm tới dữ liệu của chủ mình (không mạo danh, không đọc chéo)."""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from app.database import build_session_factory
from app.models.message import Message
from app.repositories.glasses_device import GlassesDeviceRepository
from app.repositories.user import UserRepository
from tests.test_calls_api import SVC, World, _link, _register_phone, _session, world  # noqa: F401

if TYPE_CHECKING:
    import pytest


_SEND = "/api/v1/service/messages/send"
_UNREAD = "/api/v1/service/messages/unread"
_CALL = "/api/v1/service/call/start"
_NOT_LINKED = "GLASSES_DEVICE_NOT_LINKED"


async def _second_owner(world: World, caplog: pytest.LogCaptureFixture) -> tuple[str, str, dict]:
    """Chủ B + kính B + người thân C (cũng được gọi là "má"). Trả (glasses_b, relative_c_id, c)."""
    owner_b = await _session(world.client, caplog, "0951000003")
    relative_c = await _session(world.client, caplog, "0951000004")
    async with build_session_factory(world.engine)() as session:
        users = UserRepository(session)
        owner_b_id = (await users.get_by_phone("0951000003")).public_user_id  # type: ignore[union-attr]
        relative_c_id = (await users.get_by_phone("0951000004")).public_user_id  # type: ignore[union-attr]
        glasses_b = f"glasses-{uuid.uuid4()}"
        await GlassesDeviceRepository(session).link(user_id=owner_b_id, device_id=glasses_b)
        await session.commit()
    await _register_phone(world.engine, relative_c_id, "relative-c-token")
    await _link(world.client, owner_b, relative_c, ("0951000004", "Dì", ["má"]))
    world.extra["b"] = owner_b
    return glasses_b, relative_c_id, relative_c


def _msg(glasses: str, text: str, alias: str = "má", **extra: str) -> dict:
    return {
        "glasses_device_id": glasses,
        "contact_alias": alias,
        "text": text,
        "request_id": str(uuid.uuid4()),
        **extra,
    }


async def test_glasses_b_cannot_message_as_a(world: World, caplog: pytest.LogCaptureFixture) -> None:
    """Kính B nhắn "má": tên được tra trong danh bạ của B, tin mang tên B và không tới mẹ của A."""
    glasses_b, _, _ = await _second_owner(world, caplog)
    sent = await world.client.post(_SEND, json=_msg(glasses_b, "Con về muộn"), headers=SVC)
    assert sent.status_code == 200, sent.text
    assert sent.json()["data"]["relative"] == {"display_name": "Dì"}
    assert [token for token, _ in world.push.sent] == ["relative-c-token"]  # mẹ của A không nhận
    (row,) = await world.rows(Message)
    assert row.owner_user_id != world.owner_id

    # Gửi kèm user_id của A bằng kính B: từ chối, không mạo danh.
    forged = await world.client.post(
        _SEND, json=_msg(glasses_b, "giả danh", user_id=world.owner_id), headers=SVC
    )
    assert (forged.status_code, forged.json()["error"]["code"]) == (404, _NOT_LINKED)
    unknown = await world.client.post(_SEND, json=_msg("glasses-khong-co", "x"), headers=SVC)
    assert (unknown.status_code, unknown.json()["error"]["code"]) == (404, _NOT_LINKED)
    assert len(await world.rows(Message)) == 1


async def test_glasses_b_cannot_read_a_messages(
    world: World, caplog: pytest.LogCaptureFixture
) -> None:
    """Tin của A không hiện trong unread của kính B và kính B không đánh dấu đã đọc được."""
    glasses_b, _, _ = await _second_owner(world, caplog)
    posted = await world.client.post(
        "/api/v1/messages",
        json={"owner_user_id": world.owner_id, "text": "riêng của A", "request_id": str(uuid.uuid4())},
        headers=world.relative,
    )
    assert posted.status_code == 200
    message_id = posted.json()["data"]["message_id"]

    unread_b = await world.client.get(_UNREAD, params={"glasses_device_id": glasses_b}, headers=SVC)
    assert unread_b.json()["data"]["messages"] == []
    stolen = await world.client.post(
        f"/api/v1/service/messages/{message_id}/read",
        json={"glasses_device_id": glasses_b},
        headers=SVC,
    )
    assert (stolen.status_code, stolen.json()["error"]["code"]) == (404, "MESSAGE_NOT_FOUND")
    spoof = await world.client.get(
        _UNREAD,
        params={"glasses_device_id": glasses_b, "user_id": world.owner_id},
        headers=SVC,
    )
    assert (spoof.status_code, spoof.json()["error"]["code"]) == (404, _NOT_LINKED)

    # Kính A vẫn thấy tin và đọc được (user_id không còn bắt buộc).
    unread_a = await world.client.get(
        _UNREAD, params={"glasses_device_id": world.glasses_id}, headers=SVC
    )
    assert [m["text"] for m in unread_a.json()["data"]["messages"]] == ["riêng của A"]
    ok = await world.client.post(
        f"/api/v1/service/messages/{message_id}/read",
        json={"glasses_device_id": world.glasses_id},
        headers=SVC,
    )
    assert ok.status_code == 200


async def test_calls_derive_owner_from_glasses(
    world: World, caplog: pytest.LogCaptureFixture
) -> None:
    """call/start không cần user_id: chủ suy từ kính; kính B gọi "má" thì chuông tới Dì của B."""
    glasses_b, _, _ = await _second_owner(world, caplog)
    body_b = {
        "glasses_device_id": glasses_b,
        "contact_alias": "má",
        "media_token": "media-token-b-1234",
        "request_id": str(uuid.uuid4()),
    }
    started = await world.client.post(_CALL, json=body_b, headers=SVC)
    assert started.status_code == 200, started.text
    assert started.json()["data"]["relative"] == {"display_name": "Dì"}
    assert [token for token, _ in world.push.sent] == ["relative-c-token"]

    forged = await world.client.post(
        _CALL,
        json={**body_b, "user_id": world.owner_id, "request_id": str(uuid.uuid4())},
        headers=SVC,
    )
    assert (forged.status_code, forged.json()["error"]["code"]) == (404, _NOT_LINKED)
    # World.start vẫn gửi user_id đúng chủ: tương thích ngược.
    assert (await world.start()).status_code == 200


async def test_emergency_derives_owner_from_glasses(
    world: World, caplog: pytest.LogCaptureFixture
) -> None:
    """emergency/start không cần user_id; user_id của chủ khác (hoặc kính lạ) bị từ chối."""
    glasses_b, _, _ = await _second_owner(world, caplog)
    base = {"request_id": str(uuid.uuid4()), "media_tokens": ["media-token-1", "media-token-2"]}
    forged = await world.client.post(
        "/api/v1/service/emergency/start",
        json={**base, "glasses_device_id": glasses_b, "user_id": world.owner_id},
        headers=SVC,
    )
    assert (forged.status_code, forged.json()["error"]["code"]) == (404, _NOT_LINKED)
    unknown = await world.client.post(
        "/api/v1/service/emergency/start",
        json={**base, "glasses_device_id": "glasses-khong-co"},
        headers=SVC,
    )
    assert (unknown.status_code, unknown.json()["error"]["code"]) == (404, _NOT_LINKED)
