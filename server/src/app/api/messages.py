"""Message routers: the glasses server's message endpoints and the phone app's message endpoints."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated
from uuid import UUID  # noqa: TC003

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.delivery import DeliveryAdapter  # noqa: TC001
from app.api.calls import Wake, get_call_push_adapter
from app.auth import UserPrincipal, require_public_scope, require_user_session
from app.config import PublicApiScope, Settings, get_app_settings
from app.database import get_db_session
from app.models.enums import MessageDirection
from app.models.message import Message  # noqa: TC001
from app.schemas.common import OkResponse, TrimmedNonEmptyStr
from app.schemas.message import (
    MessageCreatedData,
    MessageCreateRequest,
    MessageItem,
    MessageReadData,
    MessageReadRequest,
    MessageRelativeData,
    MessagesData,
    MessageSendData,
    MessageSendRequest,
    UnreadMessageData,
    UnreadMessagesData,
)
from app.services.message import MessageService
from app.workers.wake import WorkerWakeSignals  # noqa: TC001

Session = Annotated[AsyncSession, Depends(get_db_session)]
HISTORY_DEFAULT_LIMIT = 50
HISTORY_MAX_LIMIT = 100


def get_message_service(
    session: Session,
    settings: Annotated[Settings, Depends(get_app_settings)],
    adapter: Annotated[DeliveryAdapter, Depends(get_call_push_adapter)],
) -> MessageService:
    """Build the per-request message service (same push transport as calls)."""
    return MessageService(session, settings, adapter)


Messages = Annotated[MessageService, Depends(get_message_service)]


async def _commit_and_notify(
    session: AsyncSession, service: MessageService, wake: WorkerWakeSignals
) -> None:
    """Commit, then wake the callback worker and send the queued pushes."""
    await session.commit()
    wake.callback.set()
    await service.dispatch_pushes()


# --------------------------------------------------------------------- glasses server

service_router = APIRouter(
    prefix="/messages",
    tags=["messages"],
    dependencies=[Depends(require_public_scope(PublicApiScope.CALLS_MANAGE))],
)


@service_router.post("/send", response_model=OkResponse[MessageSendData])
async def send_message(
    body: MessageSendRequest, service: Messages, session: Session, wake: Wake
) -> OkResponse[MessageSendData]:
    """Text the relative named ``contact_alias`` (409/404 when ambiguous or unknown)."""
    message = await service.send_from_glasses(
        glasses_device_id=body.glasses_device_id,
        user_id=body.user_id,
        contact_alias=body.contact_alias,
        text=body.text,
        request_id=body.request_id,
    )
    data = MessageSendData(
        message_id=message.id,
        relative=MessageRelativeData(display_name=message.peer_display_name),
        created_at=message.created_at,
    )
    await _commit_and_notify(session, service, wake)
    return OkResponse(data=data)


@service_router.get("/unread", response_model=OkResponse[UnreadMessagesData])
async def list_unread(
    glasses_device_id: Annotated[TrimmedNonEmptyStr, Query()],
    service: Messages,
    user_id: Annotated[TrimmedNonEmptyStr | None, Query()] = None,
) -> OkResponse[UnreadMessagesData]:
    """Messages from relatives the glasses have not read out yet (oldest first)."""
    rows = await service.list_unread_for_glasses(glasses_device_id, user_id)
    return OkResponse(
        data=UnreadMessagesData(
            messages=[
                UnreadMessageData(
                    message_id=m.id,
                    from_display_name=m.peer_display_name,
                    text=m.text,
                    created_at=m.created_at,
                )
                for m in rows
            ]
        )
    )


@service_router.post("/{message_id}/read", response_model=OkResponse[MessageReadData])
async def mark_message_read(
    message_id: UUID,
    service: Messages,
    session: Session,
    wake: Wake,
    body: MessageReadRequest,
) -> OkResponse[MessageReadData]:
    """Record that the glasses read the message out (idempotent)."""
    message = await service.mark_read(
        message_id, glasses_device_id=body.glasses_device_id, user_id=body.user_id
    )
    data = MessageReadData(message_id=message.id, read_at=message.read_at)
    await _commit_and_notify(session, service, wake)
    return OkResponse(data=data)


# --------------------------------------------------------------------- phone app

router = APIRouter(prefix="/api/v1/messages", tags=["messages"])

Principal = Annotated[UserPrincipal, Depends(require_user_session)]


@router.post("", response_model=OkResponse[MessageCreatedData])
async def create_message(
    body: MessageCreateRequest,
    principal: Principal,
    service: Messages,
    session: Session,
    wake: Wake,
) -> OkResponse[MessageCreatedData]:
    """Text the glasses of ``owner_user_id`` as a relative (needs an active care link)."""
    message = await service.send_from_relative(
        relative_user_id=principal.public_user_id,
        owner_user_id=body.owner_user_id,
        text=body.text,
        request_id=body.request_id,
    )
    data = MessageCreatedData(message_id=message.id, created_at=message.created_at)
    await _commit_and_notify(session, service, wake)
    return OkResponse(data=data)


def _item(message: Message, *, me: str) -> MessageItem:
    sender = (
        message.owner_user_id
        if message.direction is MessageDirection.TO_RELATIVE
        else message.relative_user_id
    )
    return MessageItem(
        id=message.id,
        direction=message.direction,
        from_me=sender == me,
        text=message.text,
        created_at=message.created_at,
        read_at=message.read_at,
    )


@router.get("", response_model=OkResponse[MessagesData])
async def list_messages(
    principal: Principal,
    service: Messages,
    with_user_id: Annotated[TrimmedNonEmptyStr, Query(alias="with")],
    before: Annotated[datetime | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=HISTORY_MAX_LIMIT)] = HISTORY_DEFAULT_LIMIT,
) -> OkResponse[MessagesData]:
    """Conversation with ``with`` (newest first); page back with ``before=<created_at>``."""
    if before is not None and before.tzinfo is None:
        before = before.replace(tzinfo=UTC)
    me = principal.public_user_id
    rows = await service.history(user_id=me, with_user_id=with_user_id, before=before, limit=limit)
    return OkResponse(data=MessagesData(messages=[_item(m, me=me) for m in rows]))
