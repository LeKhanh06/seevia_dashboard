"""PostgreSQL-backed tests for care links: invite/accept flow and spoken-name resolution."""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING

from app.repositories.care_link import CareLinkRepository
from app.repositories.user import UserRepository
from app.services.care import resolve_alias

if TYPE_CHECKING:
    import pytest
    from httpx import AsyncClient
    from sqlalchemy.ext.asyncio import AsyncSession


async def _session(client: AsyncClient, caplog: pytest.LogCaptureFixture, phone: str) -> dict:
    """Register + verify ``phone`` and return its Bearer headers."""
    caplog.clear()
    with caplog.at_level(logging.INFO, logger="app.api.auth"):
        reg = await client.post(
            "/api/v1/auth/register", json={"phone_number": phone, "password": "correct-horse"}
        )
    assert reg.status_code == 200, reg.text
    otp = re.search(rf"Demo OTP for {re.escape(phone)}: (\d{{6}})", caplog.text)
    assert otp is not None
    ver = await client.post(
        "/api/v1/auth/otp/verify", json={"phone_number": phone, "otp_code": otp.group(1)}
    )
    assert ver.status_code == 200, ver.text
    return {"Authorization": f"Bearer {ver.json()['data']['access_token']}"}


async def _link(
    client: AsyncClient, owner: dict, relative: dict, spec: tuple[str, str, list[str]]
) -> str:
    """Owner invites ``spec`` = (phone, name, aliases); relative accepts; return the link id."""
    phone, name, aliases = spec
    inv = await client.post(
        "/api/v1/care/invite",
        json={"phone_number": phone, "display_name": name, "aliases": aliases},
        headers=owner,
    )
    assert inv.status_code == 200, inv.text
    link_id = inv.json()["data"]["id"]
    acc = await client.post(f"/api/v1/care/{link_id}/accept", headers=relative)
    assert acc.status_code == 200, acc.text
    return link_id


async def test_invite_accept_flow(db_client: AsyncClient, caplog: pytest.LogCaptureFixture) -> None:
    """Mời -> pending; chỉ người thân được chấp nhận; sau đó active, cả hai vai đều thấy."""
    owner = await _session(db_client, caplog, "0941000001")
    relative = await _session(db_client, caplog, "0941000002")
    stranger = await _session(db_client, caplog, "0941000003")

    # Số chưa đăng ký -> 404 USER_NOT_FOUND.
    missing = await db_client.post(
        "/api/v1/care/invite",
        json={"phone_number": "0941999999", "display_name": "Ai đó"},
        headers=owner,
    )
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "USER_NOT_FOUND"

    invite = await db_client.post(
        "/api/v1/care/invite",
        json={"phone_number": "0941000002", "display_name": "Mẹ", "emergency": True},
        headers=owner,
    )
    assert invite.status_code == 200, invite.text
    link = invite.json()["data"]
    assert link["status"] == "pending"
    assert link["role"] == "owner"

    # Mời lại cùng cặp -> 409; chủ kính / người lạ không chấp nhận hộ được.
    again = await db_client.post(
        "/api/v1/care/invite",
        json={"phone_number": "0941000002", "display_name": "Mẹ"},
        headers=owner,
    )
    assert again.status_code == 409
    for headers in (owner, stranger):
        denied = await db_client.post(f"/api/v1/care/{link['id']}/accept", headers=headers)
        assert denied.status_code == 404

    accepted = await db_client.post(f"/api/v1/care/{link['id']}/accept", headers=relative)
    assert accepted.status_code == 200
    assert accepted.json()["data"]["status"] == "active"
    assert accepted.json()["data"]["role"] == "relative"

    for headers, role in ((owner, "owner"), (relative, "relative")):
        listed = await db_client.get("/api/v1/care/links", headers=headers)
        assert [(x["id"], x["role"]) for x in listed.json()["data"]["links"]] == [
            (link["id"], role)
        ]
    assert (await db_client.get("/api/v1/care/links", headers=stranger)).json()["data"][
        "links"
    ] == []

    patched = await db_client.patch(
        f"/api/v1/care/{link['id']}", json={"aliases": ["má"], "priority": 2}, headers=owner
    )
    assert patched.json()["data"]["aliases"] == ["má"]
    assert (
        await db_client.patch(f"/api/v1/care/{link['id']}", json={"priority": 1}, headers=relative)
    ).status_code == 404

    revoked = await db_client.delete(f"/api/v1/care/{link['id']}", headers=relative)
    assert revoked.json()["data"]["status"] == "revoked"
    listed = await db_client.get("/api/v1/care/links", headers=owner)
    assert listed.json()["data"]["links"] == []


async def test_alias_match_accent_insensitive(
    db_client: AsyncClient, db_session: AsyncSession, caplog: pytest.LogCaptureFixture
) -> None:
    """Từ "me" khớp "mẹ"; bỏ qua dấu câu/khoảng trắng; link pending/revoked không khớp."""
    owner = await _session(db_client, caplog, "0942000001")
    relative = await _session(db_client, caplog, "0942000002")
    owner_id = (await UserRepository(db_session).get_by_phone("0942000001")).public_user_id  # type: ignore[union-attr]
    links = CareLinkRepository(db_session)

    inv = await db_client.post(
        "/api/v1/care/invite",
        json={"phone_number": "0942000002", "display_name": "Mẹ", "aliases": ["Má  ơi"]},
        headers=owner,
    )
    link_id = inv.json()["data"]["id"]
    assert await resolve_alias(links, owner_id, "me") == []  # còn pending

    await db_client.post(f"/api/v1/care/{link_id}/accept", headers=relative)
    for spoken in ("me", "Mẹ.", " MẸ ", "ma oi", "má ơi"):
        found = await resolve_alias(links, owner_id, spoken)
        assert [str(x.id) for x in found] == [link_id], spoken
    assert await resolve_alias(links, owner_id, "bố") == []

    await db_client.delete(f"/api/v1/care/{link_id}", headers=owner)
    assert await resolve_alias(links, owner_id, "me") == []


async def test_alias_match_multiple(
    db_client: AsyncClient, db_session: AsyncSession, caplog: pytest.LogCaptureFixture
) -> None:
    """Hai người thân cùng alias "nam" -> 2 kết quả để phía gọi hỏi lại, không gọi bừa."""
    owner = await _session(db_client, caplog, "0943000001")
    first = await _session(db_client, caplog, "0943000002")
    second = await _session(db_client, caplog, "0943000003")
    owner_id = (await UserRepository(db_session).get_by_phone("0943000001")).public_user_id  # type: ignore[union-attr]

    await _link(db_client, owner, first, ("0943000002", "Anh Nam", ["nam"]))
    await _link(db_client, owner, second, ("0943000003", "Chú Nam", ["Nam"]))

    found = await resolve_alias(CareLinkRepository(db_session), owner_id, "nam")
    assert sorted(x.display_name for x in found) == ["Anh Nam", "Chú Nam"]
