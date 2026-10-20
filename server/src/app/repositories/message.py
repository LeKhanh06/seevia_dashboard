"""Message persistence and the ``message_received`` outbox; the caller owns the transaction."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from sqlalchemy import exists, select, update
from sqlalchemy.exc import IntegrityError

from app.models.care_link import CareLink
from app.models.enums import CareLinkStatus, MessageCallbackState, MessageDirection
from app.models.message import Message

if TYPE_CHECKING:
    from uuid import UUID

    from sqlalchemy import ColumnElement
    from sqlalchemy.ext.asyncio import AsyncSession


def _active_link_exists() -> ColumnElement[bool]:
    """Correlated EXISTS: the message's (owner, relative) pair still has an ``active`` link."""
    return exists().where(
        CareLink.owner_user_id == Message.owner_user_id,
        CareLink.relative_user_id == Message.relative_user_id,
        CareLink.status == CareLinkStatus.ACTIVE,
    )


class MessageRepository:
    """Persistence primitives for ``messages``."""

    def __init__(self, session: AsyncSession) -> None:
        """Bind the repository to a caller-owned session/transaction."""
        self._session = session

    async def get_locked(self, message_id: UUID) -> Message | None:
        """Return the message locked ``FOR UPDATE``, or ``None``."""
        result = await self._session.execute(
            select(Message).where(Message.id == message_id).with_for_update()
        )
        return result.scalar_one_or_none()

    async def get_by_request_id(self, request_id: UUID) -> Message | None:
        """Return the message stored for the sender's ``request_id``, or ``None``."""
        result = await self._session.execute(
            select(Message).where(Message.request_id == request_id)
        )
        return result.scalar_one_or_none()

    async def create(  # noqa: PLR0913
        self,
        *,
        owner_user_id: str,
        relative_user_id: str,
        direction: MessageDirection,
        text: str,
        peer_display_name: str,
        glasses_device_id: str | None,
        request_id: UUID,
        needs_callback: bool,
    ) -> tuple[Message, bool]:
        """Insert a message; ``(existing, False)`` when ``request_id`` already won a race.

        Chèn trong savepoint: hai lần gửi đồng thời cùng ``request_id`` thì một bên đụng unique
        index, bên đó nhận lại bản đã lưu thay vì lỗi 500.
        """
        message = Message(
            owner_user_id=owner_user_id,
            relative_user_id=relative_user_id,
            direction=direction,
            text=text,
            peer_display_name=peer_display_name,
            glasses_device_id=glasses_device_id,
            request_id=request_id,
            callback_state=MessageCallbackState.PENDING if needs_callback else None,
        )
        try:
            async with self._session.begin_nested():
                self._session.add(message)
                await self._session.flush()
        except IntegrityError:
            existing = await self.get_by_request_id(request_id)
            if existing is None:
                raise
            return existing, False
        return message, True

    async def list_unread_for_glasses(self, owner_user_id: str, *, limit: int) -> list[Message]:
        """Unread relative->glasses messages whose care link is still ``active``, oldest first."""
        result = await self._session.execute(
            select(Message)
            .where(
                Message.owner_user_id == owner_user_id,
                Message.direction == MessageDirection.TO_GLASSES,
                Message.read_at.is_(None),
                _active_link_exists(),
            )
            .order_by(Message.created_at, Message.id)
            .limit(limit)
        )
        return list(result.scalars())

    async def list_between(
        self,
        owner_user_id: str,
        relative_user_id: str,
        *,
        before: datetime | None,
        limit: int,
    ) -> list[Message]:
        """Messages of one pair, newest first, strictly older than ``before`` when given."""
        query = select(Message).where(
            Message.owner_user_id == owner_user_id, Message.relative_user_id == relative_user_id
        )
        if before is not None:
            query = query.where(Message.created_at < before)
        result = await self._session.execute(
            query.order_by(Message.created_at.desc(), Message.id.desc()).limit(limit)
        )
        return list(result.scalars())

    async def cancel_pending_callbacks(self, owner_user_id: str, relative_user_id: str) -> None:
        """Stop owing ``message_received`` for a pair whose care link was just revoked."""
        await self._session.execute(
            update(Message)
            .where(
                Message.owner_user_id == owner_user_id,
                Message.relative_user_id == relative_user_id,
                Message.callback_state == MessageCallbackState.PENDING,
            )
            .values(callback_state=MessageCallbackState.DEAD_LETTER, callback_locked_until=None)
        )

    async def claim_due_callbacks(
        self, *, now: datetime, lease_until: datetime, limit: int
    ) -> list[Message]:
        """Lease due ``message_received`` events, oldest first.

        Bỏ qua tin đã đọc (kính đã lấy qua ``GET unread``) và tin của link không còn ``active``.
        """
        result = await self._session.execute(
            select(Message)
            .where(
                Message.callback_state == MessageCallbackState.PENDING,
                Message.callback_next_attempt_at <= now,
                (Message.callback_locked_until.is_(None)) | (Message.callback_locked_until < now),
                Message.read_at.is_(None),
                _active_link_exists(),
            )
            .order_by(Message.created_at)
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
        rows = list(result.scalars())
        for row in rows:
            row.callback_locked_until = lease_until
        await self._session.flush()
        return rows

    async def record_callback_delivered(self, message_id: UUID, *, now: datetime) -> None:
        """Mark the event delivered."""
        row = await self._session.get(Message, message_id)
        if row is not None:
            row.callback_state = MessageCallbackState.DELIVERED
            row.callback_delivered_at = now
            row.callback_locked_until = None

    async def record_callback_retry(self, message_id: UUID, *, next_attempt_at: datetime) -> None:
        """Count a failed attempt and schedule the next one."""
        row = await self._session.get(Message, message_id)
        if row is not None:
            row.callback_attempts += 1
            row.callback_next_attempt_at = next_attempt_at
            row.callback_locked_until = None

    async def record_callback_dead_letter(self, message_id: UUID) -> None:
        """Give up on the event (permanent error or attempts exhausted)."""
        row = await self._session.get(Message, message_id)
        if row is not None:
            row.callback_attempts += 1
            row.callback_state = MessageCallbackState.DEAD_LETTER
            row.callback_locked_until = None

    @staticmethod
    def stamp_read(message: Message) -> None:
        """Mark a message read (first read wins)."""
        if message.read_at is None:
            message.read_at = datetime.now(UTC)
