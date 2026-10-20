"""Care links router (phone app session): invite, accept, list, edit, revoke relatives."""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated, Literal
from uuid import UUID  # noqa: TC003

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.calls import Calls, Wake  # noqa: TC001
from app.api.messages import Messages  # noqa: TC001
from app.auth import UserPrincipal, require_user_session
from app.database import get_db_session
from app.repositories.care_link import CareLinkRepository
from app.repositories.user import UserRepository
from app.schemas.care import CareInviteRequest, CareLinkData, CareLinksData, CareUpdateRequest
from app.schemas.common import OkResponse
from app.services import care as care_service

if TYPE_CHECKING:
    from app.models.care_link import CareLink

router = APIRouter(prefix="/api/v1/care", tags=["care"])

Principal = Annotated[UserPrincipal, Depends(require_user_session)]
Session = Annotated[AsyncSession, Depends(get_db_session)]


async def _to_data(link: CareLink, *, me: str, users: UserRepository) -> CareLinkData:
    """Render ``link`` from ``me``'s point of view (peer = the other party)."""
    role: Literal["owner", "relative"] = "owner" if link.owner_user_id == me else "relative"
    peer_id = link.relative_user_id if role == "owner" else link.owner_user_id
    peer = await users.get_by_public_user_id(peer_id)
    return CareLinkData(
        id=link.id,
        role=role,
        status=link.status,
        display_name=link.display_name,
        aliases=list(link.aliases),
        emergency=link.emergency,
        priority=link.priority,
        owner_user_id=link.owner_user_id,
        relative_user_id=link.relative_user_id,
        peer_display_name=peer.display_name if peer else None,
        peer_phone_number=peer.phone_number if peer else "",
        created_at=link.created_at,
    )


@router.post("/invite", response_model=OkResponse[CareLinkData])
async def invite_relative(
    body: CareInviteRequest, principal: Principal, session: Session
) -> OkResponse[CareLinkData]:
    """Owner invites a registered account as a (pending) relative."""
    users = UserRepository(session)
    link = await care_service.invite(
        CareLinkRepository(session),
        users,
        owner_user_id=principal.public_user_id,
        phone_number=body.phone_number,
        display_name=body.display_name,
        aliases=body.aliases,
        emergency=body.emergency,
        priority=body.priority,
    )
    data = await _to_data(link, me=principal.public_user_id, users=users)
    await session.commit()
    return OkResponse(data=data)


@router.post("/{link_id}/accept", response_model=OkResponse[CareLinkData])
async def accept_invite(
    link_id: UUID, principal: Principal, session: Session
) -> OkResponse[CareLinkData]:
    """Accept the invitation as the invited relative, activating the link."""
    link = await care_service.accept(
        CareLinkRepository(session), link_id=link_id, user_id=principal.public_user_id
    )
    data = await _to_data(link, me=principal.public_user_id, users=UserRepository(session))
    await session.commit()
    return OkResponse(data=data)


@router.get("/links", response_model=OkResponse[CareLinksData])
async def list_links(principal: Principal, session: Session) -> OkResponse[CareLinksData]:
    """List non-revoked links where the caller is owner or relative."""
    users = UserRepository(session)
    me = principal.public_user_id
    links = await CareLinkRepository(session).list_for_user(me)
    return OkResponse(
        data=CareLinksData(links=[await _to_data(link, me=me, users=users) for link in links])
    )


@router.patch("/{link_id}", response_model=OkResponse[CareLinkData])
async def update_link(
    link_id: UUID, body: CareUpdateRequest, principal: Principal, session: Session
) -> OkResponse[CareLinkData]:
    """Owner edits aliases / emergency / priority."""
    link = await care_service.update(
        CareLinkRepository(session),
        link_id=link_id,
        owner_user_id=principal.public_user_id,
        aliases=body.aliases,
        emergency=body.emergency,
        priority=body.priority,
    )
    data = await _to_data(link, me=principal.public_user_id, users=UserRepository(session))
    await session.commit()
    return OkResponse(data=data)


@router.delete("/{link_id}", response_model=OkResponse[CareLinkData])
async def revoke_link(  # noqa: PLR0913, PLR0917
    link_id: UUID,
    principal: Principal,
    session: Session,
    calls: Calls,
    messages: Messages,
    wake: Wake,
) -> OkResponse[CareLinkData]:
    """Either party revokes the link (idempotent); calls open between the pair are ended."""
    link = await care_service.revoke(
        CareLinkRepository(session), link_id=link_id, user_id=principal.public_user_id
    )
    await calls.end_open_calls_for_pair(link.owner_user_id, link.relative_user_id)
    await messages.cancel_pending_for_pair(link.owner_user_id, link.relative_user_id)
    data = await _to_data(link, me=principal.public_user_id, users=UserRepository(session))
    await session.commit()
    wake.callback.set()
    await calls.dispatch_pushes()
    return OkResponse(data=data)
