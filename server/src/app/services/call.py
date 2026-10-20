"""Call business rules: state machine, authorization, FCM pushes, callbacks to the glasses server.

Never commits the caller's session. Pushes are collected while the transaction runs and sent by
``dispatch_pushes`` after the router commits, so a phone never hears about a call that rolled back.
Callbacks to the glasses server go through the ``call_callbacks`` outbox (written in the same
transaction), delivered at-least-once and in order by ``CallbackWorker``.

State machine (repeating a transition is an idempotent no-op):
``ringing -> active | rejected | missed | ended(cancelled) | failed``, ``active -> ended``.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from app.adapters.delivery import DeliveryOutcomeStatus
from app.errors import (
    CallNotAllowedError,
    CallNotFoundError,
    CallStateConflictError,
    ContactNotFoundError,
    GlassesDeviceNotLinkedError,
    MultipleContactsFoundError,
    RequestIdConflictError,
)
from app.models.enums import CallDirection, CallState
from app.repositories.call import CallRepository
from app.repositories.care_link import CareLinkRepository
from app.repositories.device import DeviceRepository
from app.repositories.glasses_device import GlassesDeviceRepository
from app.repositories.user import UserRepository
from app.security import hash_token
from app.services.care import resolve_alias

if TYPE_CHECKING:
    from uuid import UUID

    from sqlalchemy.ext.asyncio import AsyncSession

    from app.adapters.delivery import DeliveryAdapter
    from app.config import Settings
    from app.models.call import Call
    from app.models.care_link import CareLink

logger = logging.getLogger("app.services.call")

_TERMINAL = frozenset({CallState.ENDED, CallState.MISSED, CallState.REJECTED, CallState.FAILED})
_NOT_FOUND_MSG = "Call not found"
_NOT_ALLOWED_MSG = "No active care link allows this call"
_NOT_LINKED_MSG = "No active glasses pairing found for this account"
_CONFLICT_MSG = "Call is not in a state that allows this action"
_NO_CONTACT_MSG = "No active relative matches that name"
_MANY_CONTACTS_MSG = "Several relatives match that name"
_REQUEST_ID_MSG = "request_id was already used for a different glasses device"
_OWNER_FALLBACK_NAME = "Chủ kính"
_REASON_OFFLINE = "glasses_offline"
_REASON_REVOKED = "revoked"
# Token media gốc chỉ được giao trong chừng này giây kể từ lúc nghe máy; quá hạn thì xoá.
_TOKEN_HANDOUT_WINDOW = timedelta(seconds=60)


@dataclass(frozen=True, slots=True)
class _Push:
    """One FCM data message waiting for the commit."""

    push_token: str
    data: dict[str, str]
    label: str


class CallService:
    """Coordinate calls for one request/transaction."""

    def __init__(self, session: AsyncSession, settings: Settings, adapter: DeliveryAdapter) -> None:
        """Bind repositories to the caller-owned session."""
        self._settings = settings
        self._adapter = adapter
        self._calls = CallRepository(session)
        self._links = CareLinkRepository(session)
        self._glasses = GlassesDeviceRepository(session)
        self._devices = DeviceRepository(session)
        self._users = UserRepository(session)
        self._pushes: list[_Push] = []

    # ------------------------------------------------------------------ glasses server

    async def start_from_glasses(  # noqa: PLR0913
        self,
        *,
        glasses_device_id: str,
        contact_alias: str,
        media_token: str,
        request_id: UUID,
        user_id: str | None = None,
        emergency: bool = False,
    ) -> Call:
        """Resolve ``contact_alias`` to one active relative and start ringing them.

        The owner is derived from the authenticated glasses; ``user_id`` (if sent) must match.
        """
        paired = await self._glasses.get_active_user_id(glasses_device_id)
        if paired is None or (user_id is not None and paired != user_id):
            raise GlassesDeviceNotLinkedError(
                _NOT_LINKED_MSG, details={"device_id": glasses_device_id}
            )
        user_id = paired
        existing = await self._calls.get_by_request_id(request_id)
        if existing is not None:
            if existing.glasses_device_id != glasses_device_id:
                raise RequestIdConflictError(_REQUEST_ID_MSG)
            return existing  # VH gửi lại cùng request_id: trả cuộc gọi cũ, không đổ chuông lần nữa

        matches = await resolve_alias(self._links, user_id, contact_alias)
        if not matches:
            raise ContactNotFoundError(_NO_CONTACT_MSG)
        if len(matches) > 1:
            raise MultipleContactsFoundError(
                _MANY_CONTACTS_MSG, details={"candidates": [m.display_name for m in matches]}
            )
        return await self.start_to_relative(
            matches[0],
            glasses_device_id=glasses_device_id,
            media_token=media_token,
            request_id=request_id,
            emergency=emergency,
        )

    async def start_to_relative(
        self,
        link: CareLink,
        *,
        glasses_device_id: str,
        media_token: str,
        request_id: UUID | None,
        emergency: bool,
    ) -> Call:
        """Ring the relative of a resolved ``active`` link (also used by the emergency chain)."""
        call = await self._calls.create(
            owner_user_id=link.owner_user_id,
            relative_user_id=link.relative_user_id,
            glasses_device_id=glasses_device_id,
            direction=CallDirection.TO_RELATIVE,
            emergency=emergency,
            peer_display_name=link.display_name,
            request_id=request_id,
            media_token_hash=hash_token(media_token),
            media_token_pending=media_token,
            ring_deadline_at=self._deadline(),
        )
        owner = await self._users.get_by_public_user_id(link.owner_user_id)
        queued = await self._push(
            call.relative_user_id,
            call,
            {
                "type": "call_incoming",
                "call_id": str(call.id),
                "from_display_name": (owner.display_name if owner else None)
                or _OWNER_FALLBACK_NAME,
                "emergency": "true" if emergency else "false",
            },
        )
        if not queued:
            # Không có điện thoại nào để đổ chuông: cho hết hạn ngay, timeout worker báo
            # missed trong một chu kỳ poll thay vì bắt kính và chuỗi khẩn cấp chờ 30 s.
            call.ring_deadline_at = datetime.now(UTC)
            await self._calls.touch(call)
        return call

    async def answer_from_glasses(self, call_id: UUID, *, media_token: str) -> Call:
        """Activate a call the glasses picked up (``ringing -> active``) and tell the relative."""
        call = await self._locked(call_id)
        if call.direction is not CallDirection.TO_GLASSES:
            raise CallStateConflictError(_CONFLICT_MSG)
        if call.state is CallState.ACTIVE:
            return call
        if call.state is not CallState.RINGING:
            raise CallStateConflictError(_CONFLICT_MSG)
        if not await self._has_link(call):
            # Link bị thu hồi lúc đang chuông: không đẩy token cho người đã mất quyền.
            await self._finish(call, CallState.ENDED, _REASON_REVOKED)
            await self._push_ended(call)
            return call
        call.state = CallState.ACTIVE
        call.answered_at = datetime.now(UTC)
        call.media_token_hash = hash_token(media_token)
        call.media_token_pending = media_token  # giữ tới khi app lấy qua GET; FCM đã mang sẵn
        await self._calls.touch(call)
        await self._push(
            call.relative_user_id,
            call,
            {
                "type": "call_answered",
                "call_id": str(call.id),
                "media_url": self.media_url,
                "media_token": media_token,
            },
        )
        return call

    async def hangup_from_glasses(self, call_id: UUID, *, reason: str | None) -> Call:
        """Cancel, decline or hang up for the glasses; the relative is told, VH is not echoed."""
        call = await self._locked(call_id)
        if call.state in _TERMINAL:
            return call
        if call.state is CallState.RINGING:
            callee_declines = call.direction is CallDirection.TO_GLASSES
            state = CallState.REJECTED if callee_declines else CallState.ENDED
            default = "rejected" if callee_declines else "cancelled"
        else:
            state, default = CallState.ENDED, "hangup"
        if reason == _REASON_OFFLINE and call.state is CallState.RINGING:
            state = CallState.FAILED  # kính không có phiên: chưa từng đổ chuông
        await self._finish(call, state, reason or default)
        await self._push_ended(call)
        return call

    # ------------------------------------------------------------------ phone app

    async def create_from_relative(self, *, relative_user_id: str, owner_user_id: str) -> Call:
        """Start a call from a relative to the owner's glasses; needs an ``active`` care link."""
        link = await self._links.get_active_pair(owner_user_id, relative_user_id)
        if link is None:
            raise CallNotAllowedError(_NOT_ALLOWED_MSG)
        glasses_device_id = await self._glasses.get_active_device_id(owner_user_id)
        if glasses_device_id is None:
            raise GlassesDeviceNotLinkedError(_NOT_LINKED_MSG)
        existing = await self._calls.get_ringing_to_glasses(
            owner_user_id, relative_user_id, now=datetime.now(UTC)
        )
        if existing is not None:
            return existing  # bấm đúp "gọi": cùng một cuộc đang đổ chuông, không gọi kính lần nữa
        call = await self._calls.create(
            owner_user_id=owner_user_id,
            relative_user_id=relative_user_id,
            glasses_device_id=glasses_device_id,
            direction=CallDirection.TO_GLASSES,
            emergency=False,
            peer_display_name=link.display_name,
            request_id=None,
            media_token_hash=None,
            media_token_pending=None,
            ring_deadline_at=self._deadline(),
        )
        await self._enqueue(call, "call_incoming")
        return call

    async def answer_from_relative(self, call_id: UUID, *, user_id: str) -> tuple[Call, str | None]:
        """Relative answers a call from the glasses; returns the one-time media token."""
        call = await self._party(call_id, user_id)
        if call.direction is not CallDirection.TO_RELATIVE:
            raise CallStateConflictError(_CONFLICT_MSG)
        if call.state is CallState.ACTIVE:
            return call, None  # nghe lặp: token đã giao, không giao lại
        if call.state is not CallState.RINGING or self._expired(call):
            raise CallStateConflictError(_CONFLICT_MSG)
        await self._require_link(call)
        call.state = CallState.ACTIVE
        call.answered_at = datetime.now(UTC)
        token, call.media_token_pending = call.media_token_pending, None
        await self._calls.touch(call)
        await self._enqueue(call, "call_answered")
        return call, token

    async def reject_from_relative(self, call_id: UUID, *, user_id: str) -> Call:
        """Relative declines a ringing call from the glasses."""
        call = await self._party(call_id, user_id)
        if call.direction is not CallDirection.TO_RELATIVE:
            raise CallStateConflictError(_CONFLICT_MSG)
        if call.state is CallState.REJECTED:
            return call
        if call.state is not CallState.RINGING:
            raise CallStateConflictError(_CONFLICT_MSG)
        await self._finish(call, CallState.REJECTED, "rejected")
        await self._enqueue(call, "call_rejected")
        return call

    async def hangup_from_relative(self, call_id: UUID, *, user_id: str) -> Call:
        """End a call for the relative: cancel their ringing call, decline one, or hang up."""
        call = await self._party(call_id, user_id)
        if call.state in _TERMINAL:
            return call
        if call.state is CallState.RINGING and call.direction is CallDirection.TO_RELATIVE:
            await self._finish(call, CallState.REJECTED, "rejected")
            await self._enqueue(call, "call_rejected")
        else:
            reason = "cancelled" if call.state is CallState.RINGING else "hangup"
            await self._finish(call, CallState.ENDED, reason)
            await self._enqueue(call, "call_ended")
        return call

    async def get_for_relative(self, call_id: UUID, *, user_id: str) -> tuple[Call, str | None]:
        """Read a call; the pending token of an answered call to the glasses is handed out once."""
        call = await self._party(call_id, user_id)
        token = None
        if (
            call.direction is CallDirection.TO_GLASSES
            and call.state is CallState.ACTIVE
            and call.media_token_pending
        ):
            in_window = (
                call.answered_at is not None
                and datetime.now(UTC) - call.answered_at <= _TOKEN_HANDOUT_WINDOW
            )
            if not await self._has_link(call):
                await self._finish(call, CallState.ENDED, _REASON_REVOKED)
                await self._enqueue(call, "call_ended")
            else:
                if in_window:
                    token = call.media_token_pending
                call.media_token_pending = None  # giao một lần, hoặc quá hạn thì huỷ
                await self._calls.touch(call)
        return call, token

    async def end_open_calls_for_pair(self, owner_user_id: str, relative_user_id: str) -> int:
        """End ringing/active calls of a pair whose care link was just revoked.

        VH nhận ``call_ended`` để cắt cầu âm thanh; app nhận FCM ``call_ended``.
        """
        calls = await self._calls.lock_open_for_pair(owner_user_id, relative_user_id)
        for call in calls:
            await self._finish(call, CallState.ENDED, _REASON_REVOKED)
            await self._enqueue(call, "call_ended")
            await self._push_ended(call)
        return len(calls)

    # ------------------------------------------------------------------ timeout worker

    async def expire_ringing(self, *, now: datetime, limit: int) -> int:
        """Mark ringing calls past their deadline ``missed``; tell VH, dismiss the ring."""
        expired = await self._calls.lock_expired_ringing(now=now, limit=limit)
        for call in expired:
            await self._finish(call, CallState.MISSED, "timeout")
            await self._enqueue(call, "call_missed")
            await self._push_ended(call, reason="missed")
        return len(expired)

    async def sweep_active(self, *, now: datetime, limit: int) -> int:
        """Close ``active`` calls older than ``CALL_MAX_ACTIVE_S`` and drop stale raw tokens."""
        await self._calls.clear_stale_tokens(answered_before=now - _TOKEN_HANDOUT_WINDOW)
        stale = await self._calls.lock_overstayed_active(
            answered_before=now - timedelta(seconds=self._settings.call_max_active_s), limit=limit
        )
        for call in stale:
            await self._finish(call, CallState.ENDED, "max_duration")
            await self._enqueue(call, "call_ended")
            await self._push_ended(call)
        return len(stale)

    # ------------------------------------------------------------------ effects

    async def dispatch_pushes(self) -> None:
        """Send queued FCM messages; call after the transaction committed. Never raises."""
        pushes, self._pushes = self._pushes, []
        for push in pushes:
            try:
                outcome = await self._adapter.send_data(
                    push_token=push.push_token, data=push.data, label=push.label
                )
            except Exception:
                logger.exception("Call push failed unexpectedly for %s", push.label)
                continue
            if outcome.status is not DeliveryOutcomeStatus.SENT:
                logger.warning("Call push not delivered for %s: %s", push.label, outcome.status)

    # ------------------------------------------------------------------ helpers

    def _deadline(self) -> datetime:
        return datetime.now(UTC) + timedelta(seconds=self._settings.call_ring_timeout_s)

    @property
    def media_url(self) -> str:
        """WS URL of the audio bridge ("" when ``CALL_MEDIA_URL`` is unset)."""
        return self._settings.call_media_url or ""

    @staticmethod
    def _expired(call: Call) -> bool:
        return datetime.now(UTC) >= call.ring_deadline_at

    async def _locked(self, call_id: UUID) -> Call:
        call = await self._calls.get_locked(call_id)
        if call is None:
            raise CallNotFoundError(_NOT_FOUND_MSG)
        return call

    async def _party(self, call_id: UUID, user_id: str) -> Call:
        """Lock the call; only its relative may act through the app (404 hides other ids)."""
        call = await self._locked(call_id)
        if call.relative_user_id != user_id:
            raise CallNotFoundError(_NOT_FOUND_MSG)
        return call

    async def _has_link(self, call: Call) -> bool:
        return (
            await self._links.get_active_pair(call.owner_user_id, call.relative_user_id) is not None
        )

    async def _require_link(self, call: Call) -> None:
        """Refuse when the link was revoked while the call rang."""
        if not await self._has_link(call):
            raise CallNotAllowedError(_NOT_ALLOWED_MSG)

    async def _finish(self, call: Call, state: CallState, reason: str) -> None:
        call.state = state
        call.end_reason = reason
        call.ended_at = datetime.now(UTC)
        call.media_token_pending = None  # token chưa giao thì huỷ, không để lại bản gốc
        await self._calls.touch(call)

    async def _enqueue(self, call: Call, event: str) -> None:
        """Queue ``event`` for the glasses server (skipped when no callback is configured)."""
        if self._settings.callback_url is None:
            return
        await self._calls.add_callback(
            call.id,
            {
                "event": event,
                "call_id": str(call.id),
                "glasses_device_id": call.glasses_device_id,
                "peer_display_name": call.peer_display_name,
                "direction": call.direction.value,
                "emergency": call.emergency,
                "reason": call.end_reason,
            },
        )

    async def _push(self, user_id: str, call: Call, data: dict[str, str]) -> bool:
        """Queue a push for the user's active phone; ``False`` when there is none."""
        device = await self._devices.get_latest_active_device(user_id)
        if device is None:
            logger.warning("No active phone to push %s for call %s", data["type"], call.id)
            return False
        self._pushes.append(
            _Push(device.push_token_ciphertext, data, f"call {call.id} {data['type']}")
        )
        return True

    async def _push_ended(self, call: Call, *, reason: str | None = None) -> None:
        await self._push(
            call.relative_user_id,
            call,
            {
                "type": "call_ended",
                "call_id": str(call.id),
                "reason": reason or call.end_reason or "ended",
            },
        )
