"""Emergency chain rules: text every emergency relative, then ring them one by one.

Never commits the caller's session. ``start`` rings the first relative at once (lowest latency
for someone in danger); ``EmergencyWorker`` then texts everyone with the location (fetched from
the owner's phone, capped at ``EMERGENCY_LOCATION_TIMEOUT_S``) and moves the chain on whenever the
current call ends unanswered: next relative, back to the top of the list after the last one, at
most ``EMERGENCY_MAX_ROUNDS`` rounds. Someone answering ends the chain; running out of rounds
(or of media tokens) queues ``emergency_exhausted`` for the glasses server on the last call's
callback outbox, so it always arrives after that call's ``call_missed``.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING
from uuid import uuid5

from app.errors import (
    EmergencyNotFoundError,
    GlassesDeviceNotLinkedError,
    NoEmergencyContactError,
    RequestIdConflictError,
)
from app.models.enums import CallState, EmergencyState
from app.repositories.call import CallRepository
from app.repositories.care_link import CareLinkRepository
from app.repositories.emergency import EmergencyRepository
from app.repositories.glasses_device import GlassesDeviceRepository
from app.repositories.user import UserRepository
from app.services.call import CallService
from app.services.message import MessageService
from app.services.usage_ab import AbUsageRecorder

if TYPE_CHECKING:
    from uuid import UUID

    from sqlalchemy.ext.asyncio import AsyncSession

    from app.adapters.delivery import DeliveryAdapter
    from app.config import Settings
    from app.models.call import Call
    from app.models.care_link import CareLink
    from app.models.emergency import Emergency

logger = logging.getLogger("app.services.emergency")

_NOT_LINKED_MSG = "No active glasses pairing found for this account"
_NO_CONTACT_MSG = "No active relative is flagged for emergencies"
_NOT_FOUND_MSG = "Emergency not found"
_REQUEST_ID_MSG = "request_id was already used for a different glasses device"
_OWNER_FALLBACK_NAME = "Chủ kính"
_STOP_REASON = None  # cúp lượt đang chuông như huỷ thường: người thân thấy "cancelled"


@dataclass(frozen=True, slots=True)
class EmergencyStart:
    """Result of ``start``: the chain, the call now ringing and how many relatives it covers."""

    emergency: Emergency
    call: Call | None
    contacts: int


def emergency_text(owner_name: str, location: tuple[float, float] | None) -> str:
    """Build the fixed distress text; drop the location part when the phone gave none."""
    text = f"KHẨN CẤP: {owner_name} cần giúp."
    if location is not None:
        lat, lng = location
        text += f" Vị trí: https://www.google.com/maps?q={lat},{lng}"
    return text


class EmergencyService:
    """Coordinate emergency chains for one request/transaction."""

    def __init__(self, session: AsyncSession, settings: Settings, adapter: DeliveryAdapter) -> None:
        """Bind repositories and the call/message services to the caller-owned session."""
        self._settings = settings
        self._emergencies = EmergencyRepository(session)
        self._calls = CallRepository(session)
        self._links = CareLinkRepository(session)
        self._glasses = GlassesDeviceRepository(session)
        self._users = UserRepository(session)
        self._call_service = CallService(session, settings, adapter)
        self._message_service = MessageService(session, settings, adapter)
        self._usage = AbUsageRecorder(session, settings)

    # ------------------------------------------------------------------ glasses server

    async def start(
        self,
        *,
        glasses_device_id: str,
        request_id: UUID,
        media_tokens: list[str],
        user_id: str | None = None,
    ) -> EmergencyStart:
        """Start a chain (idempotent per ``request_id``) and ring the first relative.

        The owner is derived from the authenticated glasses; ``user_id`` (if sent) must match.
        """
        paired = await self._glasses.get_active_user_id(glasses_device_id)
        if paired is None or (user_id is not None and paired != user_id):
            raise GlassesDeviceNotLinkedError(
                _NOT_LINKED_MSG, details={"device_id": glasses_device_id}
            )
        user_id = paired
        existing = await self._emergencies.get_by_request_id(request_id)
        if existing is not None:
            if existing.glasses_device_id != glasses_device_id:
                raise RequestIdConflictError(_REQUEST_ID_MSG)
            return await self._describe(existing)
        running = await self._emergencies.get_running_for_device(glasses_device_id)
        if running is not None:
            return await self._describe(running)  # bấm khẩn cấp lần nữa: không mở chuỗi thứ hai

        links = await self._emergency_links(user_id)
        if not links:
            raise NoEmergencyContactError(_NO_CONTACT_MSG)
        emergency = await self._emergencies.create(
            owner_user_id=user_id,
            glasses_device_id=glasses_device_id,
            request_id=request_id,
            media_tokens=media_tokens,
        )
        await self._usage.sos(emergency)
        call = await self._ring_next(emergency, links)
        await self._emergencies.touch(emergency)
        return EmergencyStart(emergency, call, len(links))

    async def stop(self, emergency_id: UUID) -> Emergency:
        """Stop the chain now (idempotent); a relative who already answered is not cut off."""
        emergency = await self._emergencies.get_locked(emergency_id)
        if emergency is None:
            raise EmergencyNotFoundError(_NOT_FOUND_MSG)
        if emergency.state is not EmergencyState.RUNNING:
            return emergency
        call = await self._current_call(emergency)
        if call is not None and self._answered(call):
            await self._finish(emergency, EmergencyState.ANSWERED, call)
            return emergency
        await self._finish(emergency, EmergencyState.STOPPED)
        if call is not None and call.state is CallState.RINGING:
            await self._call_service.hangup_from_glasses(call.id, reason=_STOP_REASON)
        return emergency

    # ------------------------------------------------------------------ worker

    async def advance_running(self, *, limit: int) -> int:
        """Move every running chain whose current call ended; return how many changed."""
        changed = 0
        for emergency in await self._emergencies.lock_running(limit=limit):
            if await self._advance(emergency):
                changed += 1
        return changed

    async def send_messages(self, emergency_id: UUID, location: tuple[float, float] | None) -> bool:
        """Text every active emergency relative once; ``False`` when nothing is owed anymore."""
        emergency = await self._emergencies.get_locked(emergency_id)
        if (
            emergency is None
            or emergency.messages_sent
            or emergency.state is EmergencyState.STOPPED
        ):
            return False
        owner = await self._users.get_by_public_user_id(emergency.owner_user_id)
        text = emergency_text(
            (owner.display_name if owner else None) or _OWNER_FALLBACK_NAME, location
        )
        for link in await self._emergency_links(emergency.owner_user_id):
            # request_id suy từ (chuỗi, người nhận): worker chạy lại cũng không nhắn đôi.
            await self._message_service.send_to_link(
                link, text, request_id=uuid5(emergency.id, link.relative_user_id)
            )
        emergency.messages_sent = True
        emergency.messages_locked_until = None
        await self._emergencies.touch(emergency)
        return True

    async def dispatch_pushes(self) -> None:
        """Send queued FCM messages (call rings, texts); call after the commit. Never raises."""
        await self._call_service.dispatch_pushes()
        await self._message_service.dispatch_pushes()

    # ------------------------------------------------------------------ helpers

    async def _emergency_links(self, owner_user_id: str) -> list[CareLink]:
        """Active emergency relatives in priority order."""
        return [
            link
            for link in await self._links.list_active_for_owner(owner_user_id)
            if link.emergency
        ]

    async def _current_call(self, emergency: Emergency) -> Call | None:
        if emergency.current_call_id is None:
            return None
        return await self._calls.get_locked(emergency.current_call_id)

    @staticmethod
    def _answered(call: Call) -> bool:
        return call.answered_at is not None or call.state is CallState.ACTIVE

    async def _describe(self, emergency: Emergency) -> EmergencyStart:
        call = (
            await self._calls.get(emergency.current_call_id) if emergency.current_call_id else None
        )
        links = await self._emergency_links(emergency.owner_user_id)
        return EmergencyStart(emergency, call, len(links))

    async def _ring_next(self, emergency: Emergency, links: list[CareLink]) -> Call | None:
        """Ring the next relative in the cycle; ``None`` when rounds or tokens ran out."""
        if emergency.pos >= len(links):
            emergency.round += 1
            emergency.pos = 0
        if (
            not links
            or emergency.round >= self._settings.emergency_max_rounds
            or emergency.tokens_used >= len(emergency.media_tokens)
        ):
            return None
        link = links[emergency.pos]
        emergency.pos += 1
        token = emergency.media_tokens[emergency.tokens_used]
        emergency.tokens_used += 1
        call = await self._call_service.start_to_relative(
            link,
            glasses_device_id=emergency.glasses_device_id,
            media_token=token,
            request_id=None,
            emergency=True,
        )
        emergency.current_call_id = call.id
        return call

    async def _advance(self, emergency: Emergency) -> bool:
        call = await self._current_call(emergency)
        if call is None:
            await self._finish(emergency, EmergencyState.EXHAUSTED)
            return True
        if self._answered(call):
            await self._finish(emergency, EmergencyState.ANSWERED, call)
            return True
        if call.state is CallState.RINGING:
            return False
        links = await self._emergency_links(emergency.owner_user_id)
        if await self._ring_next(emergency, links) is None:
            await self._finish(emergency, EmergencyState.EXHAUSTED)
            await self._enqueue_exhausted(emergency, call)
        else:
            await self._emergencies.touch(emergency)
        return True

    async def _finish(
        self, emergency: Emergency, state: EmergencyState, answered: Call | None = None
    ) -> None:
        emergency.state = state
        emergency.media_tokens = []  # token chưa dùng thì huỷ, không để lại trong DB
        await self._emergencies.touch(emergency)
        # Thống kê: cùng transaction; thời gian tới khi có người nghe = lúc cuộc gọi được nghe.
        await self._usage.sos(emergency, answered_at=answered.answered_at if answered else None)

    async def _enqueue_exhausted(self, emergency: Emergency, last_call: Call) -> None:
        """Tell the glasses server nobody answered (skipped when no callback is configured)."""
        if self._settings.callback_url is None:
            return
        await self._calls.add_callback(
            last_call.id,
            {
                "event": "emergency_exhausted",
                "call_id": str(last_call.id),
                "emergency_id": str(emergency.id),
                "request_id": str(emergency.request_id),
                "glasses_device_id": emergency.glasses_device_id,
                "peer_display_name": last_call.peer_display_name,
                "direction": last_call.direction.value,
                "emergency": True,
                "reason": "exhausted",
            },
        )
