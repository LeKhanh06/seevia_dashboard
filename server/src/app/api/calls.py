"""Call routers: the glasses server's call endpoints and the phone app's call endpoints."""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated
from uuid import UUID  # noqa: TC003

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.delivery import DeliveryAdapter, get_delivery_adapter
from app.auth import UserPrincipal, require_public_scope, require_user_session
from app.config import PublicApiScope, Settings, get_app_settings
from app.database import get_db_session
from app.models.enums import CallState
from app.schemas.call import (
    CallAnswerData,
    CallCreatedData,
    CallCreateRequest,
    CallData,
    CallHangupRequest,
    CallRelativeData,
    CallServiceAnswerRequest,
    CallStartData,
    CallStartRequest,
    CallStateData,
)
from app.schemas.common import OkResponse
from app.services.call import CallService
from app.workers.wake import WorkerWakeSignals, get_worker_wake_signals

if TYPE_CHECKING:
    from app.models.call import Call

Session = Annotated[AsyncSession, Depends(get_db_session)]
Wake = Annotated[WorkerWakeSignals, Depends(get_worker_wake_signals)]


def get_call_push_adapter(
    settings: Annotated[Settings, Depends(get_app_settings)],
) -> DeliveryAdapter:
    """Push transport for call events (tests override this to capture payloads)."""
    return get_delivery_adapter(settings)


def get_call_service(
    session: Session,
    settings: Annotated[Settings, Depends(get_app_settings)],
    adapter: Annotated[DeliveryAdapter, Depends(get_call_push_adapter)],
) -> CallService:
    """Build the per-request call service."""
    return CallService(session, settings, adapter)


Calls = Annotated[CallService, Depends(get_call_service)]


async def _commit_and_notify(
    session: AsyncSession, service: CallService, wake: WorkerWakeSignals
) -> None:
    """Commit, then wake the callback worker and send the queued pushes."""
    await session.commit()
    wake.callback.set()
    await service.dispatch_pushes()


def _call_data(call: Call, *, media_url: str, media_token: str | None) -> CallData:
    active = call.state is CallState.ACTIVE
    return CallData(
        call_id=call.id,
        direction=call.direction,
        state=call.state,
        end_reason=call.end_reason,
        emergency=call.emergency,
        peer_display_name=call.peer_display_name,
        media_url=media_url if active else None,
        media_token=media_token,
        created_at=call.created_at,
        answered_at=call.answered_at,
        ended_at=call.ended_at,
    )


# --------------------------------------------------------------------- glasses server

service_router = APIRouter(
    prefix="/call",
    tags=["calls"],
    dependencies=[Depends(require_public_scope(PublicApiScope.CALLS_MANAGE))],
)


@service_router.post("/start", response_model=OkResponse[CallStartData])
async def start_call(
    body: CallStartRequest, service: Calls, session: Session, wake: Wake
) -> OkResponse[CallStartData]:
    """Call the relative named ``contact_alias`` (409/404 when ambiguous or unknown)."""
    call = await service.start_from_glasses(
        user_id=body.user_id,
        glasses_device_id=body.glasses_device_id,
        contact_alias=body.contact_alias,
        media_token=body.media_token,
        request_id=body.request_id,
        emergency=body.emergency,
    )
    data = CallStartData(
        call_id=call.id, relative=CallRelativeData(display_name=call.peer_display_name)
    )
    await _commit_and_notify(session, service, wake)
    return OkResponse(data=data)


@service_router.post("/{call_id}/hangup", response_model=OkResponse[CallStateData])
async def hangup_service_call(
    call_id: UUID,
    body: CallHangupRequest,
    service: Calls,
    session: Session,
    wake: Wake,
) -> OkResponse[CallStateData]:
    """Cancel, decline or end the call on behalf of the glasses (idempotent)."""
    call = await service.hangup_from_glasses(call_id, reason=body.reason)
    data = CallStateData(call_id=call.id, state=call.state, end_reason=call.end_reason)
    await _commit_and_notify(session, service, wake)
    return OkResponse(data=data)


@service_router.post("/{call_id}/answer", response_model=OkResponse[CallStateData])
async def answer_service_call(
    call_id: UUID,
    body: CallServiceAnswerRequest,
    service: Calls,
    session: Session,
    wake: Wake,
) -> OkResponse[CallStateData]:
    """Record that the glasses picked up a call from a relative (idempotent)."""
    call = await service.answer_from_glasses(call_id, media_token=body.media_token)
    data = CallStateData(call_id=call.id, state=call.state, end_reason=call.end_reason)
    await _commit_and_notify(session, service, wake)
    return OkResponse(data=data)


# --------------------------------------------------------------------- phone app

router = APIRouter(prefix="/api/v1/calls", tags=["calls"])

Principal = Annotated[UserPrincipal, Depends(require_user_session)]


@router.post("", response_model=OkResponse[CallCreatedData])
async def create_call(
    body: CallCreateRequest, principal: Principal, service: Calls, session: Session, wake: Wake
) -> OkResponse[CallCreatedData]:
    """Call the glasses of ``owner_user_id`` as a relative (needs an active care link)."""
    call = await service.create_from_relative(
        relative_user_id=principal.public_user_id, owner_user_id=body.owner_user_id
    )
    data = CallCreatedData(call_id=call.id)
    await _commit_and_notify(session, service, wake)
    return OkResponse(data=data)


@router.post("/{call_id}/answer", response_model=OkResponse[CallAnswerData])
async def answer_call(
    call_id: UUID,
    principal: Principal,
    service: Calls,
    session: Session,
    wake: Wake,
) -> OkResponse[CallAnswerData]:
    """Answer a call from the glasses; returns the audio WS URL and its one-time token."""
    call, token = await service.answer_from_relative(call_id, user_id=principal.public_user_id)
    data = CallAnswerData(call_id=call.id, media_url=service.media_url, media_token=token)
    await _commit_and_notify(session, service, wake)
    return OkResponse(data=data)


@router.post("/{call_id}/reject", response_model=OkResponse[CallData])
async def reject_call(
    call_id: UUID,
    principal: Principal,
    service: Calls,
    session: Session,
    wake: Wake,
) -> OkResponse[CallData]:
    """Decline a ringing call from the glasses (idempotent)."""
    call = await service.reject_from_relative(call_id, user_id=principal.public_user_id)
    data = _call_data(call, media_url=service.media_url, media_token=None)
    await _commit_and_notify(session, service, wake)
    return OkResponse(data=data)


@router.post("/{call_id}/hangup", response_model=OkResponse[CallData])
async def hangup_call(
    call_id: UUID,
    principal: Principal,
    service: Calls,
    session: Session,
    wake: Wake,
) -> OkResponse[CallData]:
    """End the call or cancel one still ringing (idempotent)."""
    call = await service.hangup_from_relative(call_id, user_id=principal.public_user_id)
    data = _call_data(call, media_url=service.media_url, media_token=None)
    await _commit_and_notify(session, service, wake)
    return OkResponse(data=data)


@router.get("/{call_id}", response_model=OkResponse[CallData])
async def get_call(
    call_id: UUID,
    principal: Principal,
    service: Calls,
    session: Session,
    wake: Wake,
) -> OkResponse[CallData]:
    """Poll a call; an answered call to the glasses hands out its media token once."""
    call, token = await service.get_for_relative(call_id, user_id=principal.public_user_id)
    data = _call_data(call, media_url=service.media_url, media_token=token)
    await _commit_and_notify(session, service, wake)
    return OkResponse(data=data)
