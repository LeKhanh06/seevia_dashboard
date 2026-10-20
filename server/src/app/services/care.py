"""Care-link business rules: invite, accept, edit, revoke, and spoken-name resolution.

Never builds an HTTP response and never commits the caller's session.
"""

from __future__ import annotations

import re
import unicodedata
from typing import TYPE_CHECKING

from app.errors import CareLinkConflictError, CareLinkNotFoundError, UserNotFoundError
from app.models.enums import CareLinkStatus

if TYPE_CHECKING:
    from uuid import UUID

    from app.models.care_link import CareLink
    from app.repositories.care_link import CareLinkRepository
    from app.repositories.user import UserRepository

_NOT_FOUND_MSG = "Care link not found"
_NO_USER_MSG = "No account is registered for this phone number"
_SELF_MSG = "Cannot create a care link with yourself"
_EXISTS_MSG = "A care link with this person already exists"
_STATE_MSG = "Care link is not in a state that allows this action"

_PUNCT = re.compile(r"[^\w\s]", re.UNICODE)
_SPACES = re.compile(r"\s+")


def normalize_exact(text: str) -> str:
    """Chuẩn hoá giữ dấu tiếng Việt: lower (NFC), bỏ dấu câu, gộp khoảng trắng."""
    lowered = unicodedata.normalize("NFC", text).lower()
    return _SPACES.sub(" ", _PUNCT.sub(" ", lowered)).strip()


def normalize_name(text: str) -> str:
    """Chuẩn hoá bỏ dấu (đ->d) để so khớp lỏng: "me" khớp "mẹ".

    STT tiếng Việt hay trả về không dấu hoặc sai dấu nên khớp lỏng là chủ ý;
    nhưng chỉ dùng khi không có khớp giữ dấu (xem ``resolve_alias``).
    """
    lowered = normalize_exact(text).replace("đ", "d")
    return "".join(
        ch for ch in unicodedata.normalize("NFD", lowered) if unicodedata.category(ch) != "Mn"
    )


async def invite(  # noqa: PLR0913
    links: CareLinkRepository,
    users: UserRepository,
    *,
    owner_user_id: str,
    phone_number: str,
    display_name: str,
    aliases: list[str],
    emergency: bool,
    priority: int,
) -> CareLink:
    """Owner invites the account owning ``phone_number``; creates (or re-opens) a pending link."""
    relative = await users.get_by_phone(phone_number)
    if relative is None:
        raise UserNotFoundError(_NO_USER_MSG)
    if relative.public_user_id == owner_user_id:
        raise CareLinkConflictError(_SELF_MSG)

    existing = await links.get_pair_locked(owner_user_id, relative.public_user_id)
    if existing is None:
        return await links.create(
            owner_user_id=owner_user_id,
            relative_user_id=relative.public_user_id,
            display_name=display_name,
            aliases=aliases,
            emergency=emergency,
            priority=priority,
        )
    if existing.status is not CareLinkStatus.REVOKED:
        raise CareLinkConflictError(_EXISTS_MSG)
    # Mời lại sau khi đã thu hồi: dùng lại hàng cũ (cặp là duy nhất), về pending.
    existing.display_name = display_name
    existing.aliases = aliases
    existing.emergency = emergency
    existing.priority = priority
    existing.status = CareLinkStatus.PENDING
    return await links.touch(existing)


async def accept(links: CareLinkRepository, *, link_id: UUID, user_id: str) -> CareLink:
    """Only the invited relative can accept; accepting an active link is a no-op."""
    link = await links.get_locked(link_id)
    # 404 (không phải 403) để người ngoài không dò được id nào tồn tại.
    if link is None or link.relative_user_id != user_id:
        raise CareLinkNotFoundError(_NOT_FOUND_MSG)
    if link.status is CareLinkStatus.REVOKED:
        raise CareLinkConflictError(_STATE_MSG)
    if link.status is CareLinkStatus.PENDING:
        link.status = CareLinkStatus.ACTIVE
        await links.touch(link)
    return link


async def update(  # noqa: PLR0913
    links: CareLinkRepository,
    *,
    link_id: UUID,
    owner_user_id: str,
    aliases: list[str] | None,
    emergency: bool | None,
    priority: int | None,
) -> CareLink:
    """Owner edits aliases/emergency/priority of a non-revoked link."""
    link = await links.get_locked(link_id)
    if link is None or link.owner_user_id != owner_user_id:
        raise CareLinkNotFoundError(_NOT_FOUND_MSG)
    if link.status is CareLinkStatus.REVOKED:
        raise CareLinkConflictError(_STATE_MSG)
    if aliases is not None:
        link.aliases = aliases
    if emergency is not None:
        link.emergency = emergency
    if priority is not None:
        link.priority = priority
    return await links.touch(link)


async def revoke(links: CareLinkRepository, *, link_id: UUID, user_id: str) -> CareLink:
    """Either party revokes the link; idempotent."""
    link = await links.get_locked(link_id)
    if link is None or user_id not in (link.owner_user_id, link.relative_user_id):
        raise CareLinkNotFoundError(_NOT_FOUND_MSG)
    if link.status is not CareLinkStatus.REVOKED:
        link.status = CareLinkStatus.REVOKED
        await links.touch(link)
    return link


async def resolve_alias(
    links: CareLinkRepository, owner_user_id: str, alias: str
) -> list[CareLink]:
    """Return the owner's ``active`` links whose alias or display_name equals ``alias``.

    Chỉ link ``active`` (pending/revoked không bao giờ được gọi). Ưu tiên khớp giữ
    dấu: "Lân" chỉ ra link "Lân", không kéo theo "Lan". Chỉ khi không có khớp giữ dấu
    mới hạ xuống khớp bỏ dấu. Nhiều kết quả = trùng tên, phía gọi phải hỏi lại.
    """
    if not normalize_exact(alias):
        return []
    active = await links.list_active_for_owner(owner_user_id)
    for normalize in (normalize_exact, normalize_name):
        wanted = normalize(alias)
        found = [
            link
            for link in active
            if wanted in {normalize(n) for n in (link.display_name, *link.aliases)}
        ]
        if found:
            return found
    return []
