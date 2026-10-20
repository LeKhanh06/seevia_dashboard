"""Call persistence primitives and the callback outbox; the caller owns the transaction."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from sqlalchemy import and_, exists, select, update
from sqlalchemy.orm import aliased

from app.models.call import Call, CallCallback
from app.models.enums import CallCallbackState, CallDirection, CallState

if TYPE_CHECKING:
    from uuid import UUID

    from sqlalchemy.ext.asyncio import AsyncSession


class CallRepository:
    """Persistence primitives for ``calls`` and ``call_callbacks``."""

    def __init__(self, session: AsyncSession) -> None:
        """Bind the repository to a caller-owned session/transaction."""
        self._session = session

    async def get(self, call_id: UUID) -> Call | None:
        """Return the call without locking, or ``None``."""
        return await self._session.get(Call, call_id)

    async def get_locked(self, call_id: UUID) -> Call | None:
        """Return the call locked ``FOR UPDATE`` (serialises state transitions), or ``None``."""
        result = await self._session.execute(
            select(Call).where(Call.id == call_id).with_for_update()
        )
        return result.scalar_one_or_none()

    async def get_by_request_id(self, request_id: UUID) -> Call | None:
        """Return the call created for the glasses' ``request_id``, or ``None``."""
        result = await self._session.execute(select(Call).where(Call.request_id == request_id))
        return result.scalar_one_or_none()

    async def create(  # noqa: PLR0913
        self,
        *,
        owner_user_id: str,
        relative_user_id: str,
        glasses_device_id: str,
        direction: CallDirection,
        emergency: bool,
        peer_display_name: str,
        request_id: UUID | None,
        media_token_hash: str | None,
        media_token_pending: str | None,
        ring_deadline_at: datetime,
    ) -> Call:
        """Insert a ``ringing`` call."""
        call = Call(
            owner_user_id=owner_user_id,
            relative_user_id=relative_user_id,
            glasses_device_id=glasses_device_id,
            direction=direction,
            emergency=emergency,
            state=CallState.RINGING,
            peer_display_name=peer_display_name,
            request_id=request_id,
            media_token_hash=media_token_hash,
            media_token_pending=media_token_pending,
            ring_deadline_at=ring_deadline_at,
        )
        self._session.add(call)
        await self._session.flush()
        return call

    async def touch(self, call: Call) -> Call:
        """Flush a mutated call, stamping ``updated_at``."""
        call.updated_at = datetime.now(UTC)
        await self._session.flush()
        return call

    async def lock_expired_ringing(self, *, now: datetime, limit: int) -> list[Call]:
        """Lock ringing calls past their deadline; rows held by another worker are skipped."""
        result = await self._session.execute(
            select(Call)
            .where(Call.state == CallState.RINGING, Call.ring_deadline_at <= now)
            .order_by(Call.ring_deadline_at)
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
        return list(result.scalars())

    async def lock_open_for_pair(self, owner_user_id: str, relative_user_id: str) -> list[Call]:
        """Lock the ``ringing`` / ``active`` calls between an owner and a relative."""
        result = await self._session.execute(
            select(Call)
            .where(
                Call.owner_user_id == owner_user_id,
                Call.relative_user_id == relative_user_id,
                Call.state.in_((CallState.RINGING, CallState.ACTIVE)),
            )
            .with_for_update()
        )
        return list(result.scalars())

    async def get_ringing_to_glasses(
        self, owner_user_id: str, relative_user_id: str, *, now: datetime
    ) -> Call | None:
        """Return the not-yet-expired ringing call this relative already placed to the glasses."""
        result = await self._session.execute(
            select(Call)
            .where(
                Call.owner_user_id == owner_user_id,
                Call.relative_user_id == relative_user_id,
                Call.direction == CallDirection.TO_GLASSES,
                Call.state == CallState.RINGING,
                Call.ring_deadline_at > now,
            )
            .order_by(Call.created_at.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def lock_overstayed_active(self, *, answered_before: datetime, limit: int) -> list[Call]:
        """Lock ``active`` calls answered before the cutoff (orphans after a lost hangup)."""
        result = await self._session.execute(
            select(Call)
            .where(Call.state == CallState.ACTIVE, Call.answered_at <= answered_before)
            .order_by(Call.answered_at)
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
        return list(result.scalars())

    async def clear_stale_tokens(self, *, answered_before: datetime) -> None:
        """Drop pending raw media tokens of calls answered before the cutoff."""
        await self._session.execute(
            update(Call)
            .where(Call.media_token_pending.is_not(None), Call.answered_at <= answered_before)
            .values(media_token_pending=None)
        )

    async def add_callback(self, call_id: UUID, payload: dict[str, object]) -> CallCallback:
        """Queue one event for the glasses server."""
        row = CallCallback(call_id=call_id, payload=payload)
        self._session.add(row)
        await self._session.flush()
        return row

    async def claim_due_callbacks(
        self, *, now: datetime, lease_until: datetime, limit: int
    ) -> list[CallCallback]:
        """Lease due pending events, oldest first, at most one in flight per call.

        Một cuộc gọi chỉ giao sự kiện cũ nhất chưa xong: nếu ``call_answered`` đang retry
        thì ``call_ended`` phải chờ, tránh VH nhận ngược thứ tự.
        """
        earlier = aliased(CallCallback)
        result = await self._session.execute(
            select(CallCallback)
            .where(
                CallCallback.state == CallCallbackState.PENDING,
                CallCallback.next_attempt_at <= now,
                (CallCallback.locked_until.is_(None)) | (CallCallback.locked_until < now),
                ~exists().where(
                    and_(
                        earlier.call_id == CallCallback.call_id,
                        earlier.id < CallCallback.id,
                        earlier.state == CallCallbackState.PENDING,
                    )
                ),
            )
            .order_by(CallCallback.id)
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
        rows = list(result.scalars())
        for row in rows:
            row.locked_until = lease_until
        await self._session.flush()
        return rows

    async def record_callback_delivered(self, callback_id: int, *, now: datetime) -> None:
        """Mark an event delivered."""
        row = await self._session.get(CallCallback, callback_id)
        if row is not None:
            row.state = CallCallbackState.DELIVERED
            row.delivered_at = now
            row.locked_until = None

    async def record_callback_retry(self, callback_id: int, *, next_attempt_at: datetime) -> None:
        """Count a failed attempt and schedule the next one."""
        row = await self._session.get(CallCallback, callback_id)
        if row is not None:
            row.attempts += 1
            row.next_attempt_at = next_attempt_at
            row.locked_until = None

    async def record_callback_dead_letter(self, callback_id: int) -> None:
        """Give up on an event (permanent error or attempts exhausted)."""
        row = await self._session.get(CallCallback, callback_id)
        if row is not None:
            row.attempts += 1
            row.state = CallCallbackState.DEAD_LETTER
            row.locked_until = None
