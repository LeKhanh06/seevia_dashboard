"""Message business rules: glasses <-> relative texts, FCM push, unread/read, glasses callback.

Never commits the caller's session. The push to a relative's phone is queued and sent by
``dispatch_pushes`` after the router commits; the ``message_received`` event owed to the glasses
server is the ``callback_*`` outbox on the ``to_glasses`` row, written in the same transaction
and delivered at-least-once by ``CallbackWorker``. Text and names are never logged.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from app.errors import (
    ContactNotFoundError,
    GlassesDeviceNotLinkedError,
    MessageNotAllowedError,
    MessageNotFoundError,
    MultipleContactsFoundError,
    RequestIdConflictError,
)
from app.models.enums import MessageDirection
from app.repositories.care_link import CareLinkRepository
from app.repositories.device import DeviceRepository
from app.repositories.glasses_device import GlassesDeviceRepository
from app.repositories.message import MessageRepository
from app.repositories.user import UserRepository
from app.services.care import resolve_alias
from app.services.push import PushQueue, QueuedPush

if TYPE_CHECKING:
    from datetime import datetime
    from uuid import UUID

    from sqlalchemy.ext.asyncio import AsyncSession

    from app.adapters.delivery import DeliveryAdapter
    from app.config import Settings
    from app.models.care_link import CareLink
    from app.models.message import Message

logger = logging.getLogger("app.services.message")

_NOT_ALLOWED_MSG = "No active care link allows messaging"
_NOT_LINKED_MSG = "No active glasses pairing found for this account"
_NOT_FOUND_MSG = "Message not found"
_NO_CONTACT_MSG = "No active relative matches that name"
_MANY_CONTACTS_MSG = "Several relatives match that name"
_REQUEST_ID_MSG = "request_id was already used for a different message sender"
_OWNER_FALLBACK_NAME = "Chủ kính"
UNREAD_LIMIT = 50


class MessageService:
    """Coordinate messages for one request/transaction."""

    def __init__(self, session: AsyncSession, settings: Settings, adapter: DeliveryAdapter) -> None:
        """Bind repositories to the caller-owned session."""
        self._settings = settings
        self._messages = MessageRepository(session)
        self._links = CareLinkRepository(session)
        self._glasses = GlassesDeviceRepository(session)
        self._devices = DeviceRepository(session)
        self._users = UserRepository(session)
        self._pushes = PushQueue(adapter)

    # ------------------------------------------------------------------ glasses server

    async def send_from_glasses(
        self, *, user_id: str, contact_alias: str, text: str, request_id: UUID
    ) -> Message:
        """Resolve ``contact_alias`` to one active relative and send them ``text``."""
        if await self._glasses.get_active_device_id(user_id) is None:
            raise GlassesDeviceNotLinkedError(_NOT_LINKED_MSG)
        existing = await self._messages.get_by_request_id(request_id)
        if existing is not None:
            return self._same_sender(existing, MessageDirection.TO_RELATIVE, user_id)

        matches = await resolve_alias(self._links, user_id, contact_alias)
        if not matches:
            raise ContactNotFoundError(_NO_CONTACT_MSG)
        if len(matches) > 1:
            raise MultipleContactsFoundError(
                _MANY_CONTACTS_MSG, details={"candidates": [m.display_name for m in matches]}
            )
        message = await self.send_to_link(matches[0], text, request_id=request_id)
        return self._same_sender(message, MessageDirection.TO_RELATIVE, user_id)

    async def send_to_link(self, link: CareLink, text: str, *, request_id: UUID) -> Message:
        """Text the relative of a resolved ``active`` link (also used by the emergency chain).

        Idempotent per ``request_id``: a replay returns the stored message without pushing again.
        """
        message, created = await self._messages.create(
            owner_user_id=link.owner_user_id,
            relative_user_id=link.relative_user_id,
            direction=MessageDirection.TO_RELATIVE,
            text=text,
            peer_display_name=link.display_name,
            glasses_device_id=None,
            request_id=request_id,
            needs_callback=False,
        )
        if created:
            await self._push_to_relative(message)
        return message

    async def list_unread_for_glasses(self, user_id: str) -> list[Message]:
        """Unread messages for the owner's glasses (only relatives with an ``active`` link)."""
        return await self._messages.list_unread_for_glasses(user_id, limit=UNREAD_LIMIT)

    async def mark_read(self, message_id: UUID, *, user_id: str | None) -> Message:
        """Mark a relative->glasses message read (idempotent); ``user_id`` must be its owner."""
        message = await self._messages.get_locked(message_id)
        if (
            message is None
            or message.direction is not MessageDirection.TO_GLASSES
            or (user_id is not None and message.owner_user_id != user_id)
        ):
            raise MessageNotFoundError(_NOT_FOUND_MSG)
        self._messages.stamp_read(message)
        return message

    # ------------------------------------------------------------------ phone app

    async def send_from_relative(
        self, *, relative_user_id: str, owner_user_id: str, text: str, request_id: UUID
    ) -> Message:
        """Send a relative's text to the owner's glasses; needs an ``active`` care link."""
        link = await self._links.get_active_pair(owner_user_id, relative_user_id)
        if link is None:
            raise MessageNotAllowedError(_NOT_ALLOWED_MSG)
        glasses_device_id = await self._glasses.get_active_device_id(owner_user_id)
        if glasses_device_id is None:
            raise GlassesDeviceNotLinkedError(_NOT_LINKED_MSG)
        existing = await self._messages.get_by_request_id(request_id)
        if existing is not None:
            return self._same_sender(existing, MessageDirection.TO_GLASSES, relative_user_id)
        message, created = await self._messages.create(
            owner_user_id=owner_user_id,
            relative_user_id=relative_user_id,
            direction=MessageDirection.TO_GLASSES,
            text=text,
            peer_display_name=link.display_name,
            glasses_device_id=glasses_device_id,
            request_id=request_id,
            needs_callback=self._settings.callback_url is not None,
        )
        if not created:
            return self._same_sender(message, MessageDirection.TO_GLASSES, relative_user_id)
        return message

    async def history(
        self, *, user_id: str, with_user_id: str, before: datetime | None, limit: int
    ) -> list[Message]:
        """Return the caller's conversation with ``with_user_id``, newest first (needs a link)."""
        if await self._links.get_active_pair(user_id, with_user_id) is not None:
            owner_id, relative_id = user_id, with_user_id
        elif await self._links.get_active_pair(with_user_id, user_id) is not None:
            owner_id, relative_id = with_user_id, user_id
        else:
            raise MessageNotAllowedError(_NOT_ALLOWED_MSG)
        return await self._messages.list_between(owner_id, relative_id, before=before, limit=limit)

    async def cancel_pending_for_pair(self, owner_user_id: str, relative_user_id: str) -> None:
        """Care link revoked: drop the callbacks still owed to the glasses for that pair."""
        await self._messages.cancel_pending_callbacks(owner_user_id, relative_user_id)

    # ------------------------------------------------------------------ effects

    async def dispatch_pushes(self) -> None:
        """Send queued FCM messages; call after the transaction committed. Never raises."""
        await self._pushes.dispatch()

    # ------------------------------------------------------------------ helpers

    @staticmethod
    def _same_sender(message: Message, direction: MessageDirection, sender_id: str) -> Message:
        """Replay of a ``request_id``: only the original sender gets the stored message back."""
        sender = (
            message.owner_user_id
            if direction is MessageDirection.TO_RELATIVE
            else message.relative_user_id
        )
        if message.direction is not direction or sender != sender_id:
            raise RequestIdConflictError(_REQUEST_ID_MSG)
        return message

    async def _push_to_relative(self, message: Message) -> None:
        """Queue FCM ``message_in`` for the relative's active phone (none: stays in history)."""
        device = await self._devices.get_latest_active_device(message.relative_user_id)
        if device is None:
            logger.warning("No active phone to push message %s", message.id)
            return
        owner = await self._users.get_by_public_user_id(message.owner_user_id)
        self._pushes.add(
            QueuedPush(
                device.push_token_ciphertext,
                {
                    "type": "message_in",
                    "message_id": str(message.id),
                    "from_display_name": (owner.display_name if owner else None)
                    or _OWNER_FALLBACK_NAME,
                    "text": message.text,
                },
                f"message {message.id} message_in",
            )
        )
