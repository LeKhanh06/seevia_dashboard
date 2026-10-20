# ruff: noqa: F811
"""PostgreSQL-backed tests for messages: glasses <-> relative, idempotency, unread, callback."""

from __future__ import annotations

import json
import uuid
from typing import TYPE_CHECKING

import httpx

from app.adapters.callback import CallEventAdapter
from app.database import build_session_factory
from app.models.message import Message
from app.workers.callback import CallbackWorker
from tests.test_calls_api import SVC, World, _session, _sql, world  # noqa: F401

if TYPE_CHECKING:
    import pytest


def _send_body(world: World, text: str = "Con đang đi chợ", alias: str = "má") -> dict:
    return {
        "user_id": world.owner_id,
        "contact_alias": alias,
        "text": text,
        "request_id": str(uuid.uuid4()),
    }


def _app_body(world: World, text: str = "Mẹ ăn cơm chưa?") -> dict:
    return {"owner_user_id": world.owner_id, "text": text, "request_id": str(uuid.uuid4())}


def _worker(world: World, handler) -> CallbackWorker:  # noqa: ANN001
    adapter = CallEventAdapter(
        world.settings, client=httpx.AsyncClient(transport=httpx.MockTransport(handler))
    )
    return CallbackWorker(build_session_factory(world.engine), world.settings, call_events=adapter)


async def test_send_from_glasses_pushes_relative(world: World) -> None:
    """Kính nhắn "me"->Mẹ: lưu to_relative, FCM message_in đúng, lịch sử hai phía đúng chiều."""
    body = _send_body(world, alias="me")
    sent = await world.client.post("/api/v1/service/messages/send", json=body, headers=SVC)
    assert sent.status_code == 200, sent.text
    message_id = sent.json()["data"]["message_id"]
    assert sent.json()["data"]["relative"] == {"display_name": "Mẹ"}
    assert world.push.sent == [
        (
            "relative-push-token",
            {
                "type": "message_in",
                "message_id": message_id,
                "from_display_name": "Chủ kính",
                "text": "Con đang đi chợ",
            },
        )
    ]
    (row,) = await world.rows(Message)
    assert (row.direction.value, row.callback_state, row.read_at) == ("to_relative", None, None)

    mine = await world.client.get(
        "/api/v1/messages", params={"with": world.relative_id}, headers=world.owner
    )
    theirs = await world.client.get(
        "/api/v1/messages", params={"with": world.owner_id}, headers=world.relative
    )
    assert [(m["text"], m["from_me"]) for m in mine.json()["data"]["messages"]] == [
        ("Con đang đi chợ", True)
    ]
    assert [(m["text"], m["from_me"]) for m in theirs.json()["data"]["messages"]] == [
        ("Con đang đi chợ", False)
    ]


async def test_send_from_relative_callbacks_glasses(world: World) -> None:
    """Người thân nhắn: lưu to_glasses, không FCM; VH nhận message_received, thử lại khi 503."""
    sent = await world.client.post(
        "/api/v1/messages", json=_app_body(world), headers=world.relative
    )
    assert sent.status_code == 200, sent.text
    message_id = sent.json()["data"]["message_id"]
    assert world.push.sent == []  # kính nhận qua callback VH, không phải FCM

    seen: list[tuple[str, str, str, dict]] = []
    fail_first = {"left": 1}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(
            (
                request.url.path,
                request.headers["authorization"],
                request.headers["idempotency-key"],
                json.loads(request.content),
            )
        )
        if fail_first["left"]:
            fail_first["left"] -= 1
            return httpx.Response(503)
        return httpx.Response(200)

    worker = _worker(world, handler)
    await worker.run_once()
    await worker.run_once()
    assert len(seen) == 1  # chưa tới giờ thử lại
    await _sql(world, "UPDATE messages SET callback_next_attempt_at = now() - interval '1 s'")
    await worker.run_once()
    await worker.run_once()  # đã giao: không gửi lần nữa
    assert len(seen) == 2
    path, auth, key, payload = seen[1]
    assert (path, auth, key) == (
        "/internal/messages",
        "Bearer cb-secret-token",
        f"message-{message_id}",
    )
    assert payload == {
        "event": "message_received",
        "message_id": message_id,
        "glasses_device_id": world.glasses_id,
        "from_display_name": "Mẹ",
        "text": "Mẹ ăn cơm chưa?",
    }
    (row,) = await world.rows(Message)
    assert row.callback_state.value == "delivered"


async def test_revoked_link_message_not_delivered(world: World) -> None:
    """Link bị thu hồi trước khi VH nhận: không callback, không nằm trong unread."""
    await world.client.post("/api/v1/messages", json=_app_body(world), headers=world.relative)
    links = await world.client.get("/api/v1/care/links", headers=world.owner)
    link_id = links.json()["data"]["links"][0]["id"]
    await world.client.delete(f"/api/v1/care/{link_id}", headers=world.owner)

    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path)
        return httpx.Response(200)

    await _worker(world, handler).run_once()
    assert seen == []
    (row,) = await world.rows(Message)
    assert row.callback_state.value == "dead_letter"
    unread = await world.client.get(
        "/api/v1/service/messages/unread", params={"user_id": world.owner_id}, headers=SVC
    )
    assert unread.json()["data"]["messages"] == []


async def test_idempotent_request_id(world: World) -> None:
    """Gửi lại cùng request_id: trả đúng bản cũ, một dòng, một push; người khác dùng id -> 409."""
    body = _send_body(world)
    first = await world.client.post("/api/v1/service/messages/send", json=body, headers=SVC)
    again = await world.client.post("/api/v1/service/messages/send", json=body, headers=SVC)
    assert first.status_code == again.status_code == 200
    assert again.json()["data"] == first.json()["data"]
    assert len(world.push.sent) == 1
    assert len(await world.rows(Message)) == 1

    clash = await world.client.post(
        "/api/v1/messages",
        json={**_app_body(world), "request_id": body["request_id"]},
        headers=world.relative,
    )
    assert (clash.status_code, clash.json()["error"]["code"]) == (409, "REQUEST_ID_CONFLICT")

    app_body = _app_body(world)
    one = await world.client.post("/api/v1/messages", json=app_body, headers=world.relative)
    two = await world.client.post("/api/v1/messages", json=app_body, headers=world.relative)
    assert one.json()["data"] == two.json()["data"]
    assert len(await world.rows(Message)) == 2


async def test_unread_and_mark_read(world: World) -> None:
    """Unread liệt kê cũ->mới đúng tên người nhắn; đã đọc thì biến mất; sai chủ kính -> 404."""
    for text in ("một", "hai"):
        resp = await world.client.post(
            "/api/v1/messages", json=_app_body(world, text), headers=world.relative
        )
        assert resp.status_code == 200
    params = {"user_id": world.owner_id}
    unread = await world.client.get("/api/v1/service/messages/unread", params=params, headers=SVC)
    rows = unread.json()["data"]["messages"]
    assert [(m["from_display_name"], m["text"]) for m in rows] == [("Mẹ", "một"), ("Mẹ", "hai")]

    first_id = rows[0]["message_id"]
    wrong = await world.client.post(
        f"/api/v1/service/messages/{first_id}/read", json={"user_id": "someone-else"}, headers=SVC
    )
    assert wrong.status_code == 404
    for _ in range(2):  # lặp lại vẫn 200
        read = await world.client.post(
            f"/api/v1/service/messages/{first_id}/read",
            json={"user_id": world.owner_id},
            headers=SVC,
        )
        assert read.status_code == 200
        assert read.json()["data"]["read_at"] is not None
    unread = await world.client.get("/api/v1/service/messages/unread", params=params, headers=SVC)
    assert [m["text"] for m in unread.json()["data"]["messages"]] == ["hai"]


async def test_no_link_forbidden(world: World, caplog: pytest.LogCaptureFixture) -> None:
    """Không có link active: app bị 403, kính không khớp tên; link thu hồi cũng chặn cả hai."""
    stranger = await _session(world.client, caplog, "0951000009")
    denied = await world.client.post("/api/v1/messages", json=_app_body(world), headers=stranger)
    assert (denied.status_code, denied.json()["error"]["code"]) == (403, "MESSAGE_NOT_ALLOWED")
    peek = await world.client.get(
        "/api/v1/messages", params={"with": world.owner_id}, headers=stranger
    )
    assert peek.status_code == 403
    unknown = await world.client.post(
        "/api/v1/service/messages/send", json=_send_body(world, alias="bố"), headers=SVC
    )
    assert (unknown.status_code, unknown.json()["error"]["code"]) == (404, "CONTACT_NOT_FOUND")

    links = await world.client.get("/api/v1/care/links", headers=world.owner)
    link_id = links.json()["data"]["links"][0]["id"]
    await world.client.delete(f"/api/v1/care/{link_id}", headers=world.owner)
    after = await world.client.post(
        "/api/v1/messages", json=_app_body(world), headers=world.relative
    )
    assert after.status_code == 403
    gone = await world.client.post(
        "/api/v1/service/messages/send", json=_send_body(world), headers=SVC
    )
    assert gone.status_code == 404
    history = await world.client.get(
        "/api/v1/messages", params={"with": world.owner_id}, headers=world.relative
    )
    assert history.status_code == 403
    assert world.push.sent == []
    assert await world.rows(Message) == []


async def test_text_length_validated(world: World) -> None:
    """1-1000 ký tự; rỗng/toàn khoảng trắng/1001 ký tự bị 400, 1000 ký tự qua được."""
    for bad in ("", "   ", "x" * 1001):
        glasses = await world.client.post(
            "/api/v1/service/messages/send", json=_send_body(world, bad), headers=SVC
        )
        app = await world.client.post(
            "/api/v1/messages", json=_app_body(world, bad), headers=world.relative
        )
        assert glasses.status_code == app.status_code == 400
    ok = await world.client.post(
        "/api/v1/service/messages/send", json=_send_body(world, "x" * 1000), headers=SVC
    )
    assert ok.status_code == 200
    assert len(await world.rows(Message)) == 1
