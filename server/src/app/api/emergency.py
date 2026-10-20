"""Emergency router: the glasses server starts and stops the "call my relatives" chain."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID  # noqa: TC003

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.delivery import DeliveryAdapter  # noqa: TC001
from app.api.calls import get_call_push_adapter
from app.auth import require_public_scope
from app.config import PublicApiScope, Settings, get_app_settings
from app.database import get_db_session
from app.models.enums import EmergencyState
from app.schemas.call import CallRelativeData
from app.schemas.common import OkResponse
from app.schemas.emergency import EmergencyStartData, EmergencyStartRequest, EmergencyStateData
from app.services.emergency import EmergencyService
from app.workers.wake import WorkerWakeSignals, get_worker_wake_signals

Session = Annotated[AsyncSession, Depends(get_db_session)]
Wake = Annotated[WorkerWakeSignals, Depends(get_worker_wake_signals)]


def get_emergency_service(
    session: Session,
    settings: Annotated[Settings, Depends(get_app_settings)],
    adapter: Annotated[DeliveryAdapter, Depends(get_call_push_adapter)],
) -> EmergencyService:
    """Build the per-request emergency service."""
    return EmergencyService(session, settings, adapter)


Emergencies = Annotated[EmergencyService, Depends(get_emergency_service)]

service_router = APIRouter(
    prefix="/emergency",
    tags=["emergency"],
    dependencies=[Depends(require_public_scope(PublicApiScope.CALLS_MANAGE))],
)


@service_router.post("/start", response_model=OkResponse[EmergencyStartData])
async def start_emergency(
    body: EmergencyStartRequest, service: Emergencies, session: Session, wake: Wake
) -> OkResponse[EmergencyStartData]:
    """Ring the first emergency relative now; the worker texts everyone and keeps calling."""
    started = await service.start(
        user_id=body.user_id,
        glasses_device_id=body.glasses_device_id,
        request_id=body.request_id,
        media_tokens=body.media_tokens,
    )
    call = started.call
    data = EmergencyStartData(
        emergency_id=started.emergency.id,
        state=started.emergency.state,
        call_id=call.id if call else None,
        relative=CallRelativeData(display_name=call.peer_display_name) if call else None,
        contacts=started.contacts,
    )
    await session.commit()
    wake.callback.set()
    wake.emergency_messages.set()
    wake.emergency_chain.set()
    await service.dispatch_pushes()
    return OkResponse(data=data)


@service_router.post("/{emergency_id}/stop", response_model=OkResponse[EmergencyStateData])
async def stop_emergency(
    emergency_id: UUID, service: Emergencies, session: Session, wake: Wake
) -> OkResponse[EmergencyStateData]:
    """Stop the chain now and cancel the call still ringing (idempotent)."""
    emergency = await service.stop(emergency_id)
    answered = emergency.state is EmergencyState.ANSWERED
    data = EmergencyStateData(
        emergency_id=emergency.id,
        state=emergency.state,
        call_id=emergency.current_call_id if answered else None,
    )
    await session.commit()
    wake.callback.set()
    await service.dispatch_pushes()
    return OkResponse(data=data)
