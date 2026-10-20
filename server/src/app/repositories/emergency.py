"""Emergency chain persistence primitives. Never commits; the caller owns the transaction."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from sqlalchemy import select

from app.models.emergency import Emergency
from app.models.enums import EmergencyState

if TYPE_CHECKING:
    from uuid import UUID

    from sqlalchemy.ext.asyncio import AsyncSession


class EmergencyRepository:
    """Persistence primitives for ``emergencies``."""

    def __init__(self, session: AsyncSession) -> None:
        """Bind the repository to a caller-owned session/transaction."""
        self._session = session

    async def create(
        self,
        *,
        owner_user_id: str,
        glasses_device_id: str,
        request_id: UUID,
        media_tokens: list[str],
    ) -> Emergency:
        """Insert a ``running`` chain."""
        emergency = Emergency(
            owner_user_id=owner_user_id,
            glasses_device_id=glasses_device_id,
            request_id=request_id,
            state=EmergencyState.RUNNING,
            media_tokens=media_tokens,
        )
        self._session.add(emergency)
        await self._session.flush()
        return emergency

    async def get_by_request_id(self, request_id: UUID) -> Emergency | None:
        """Return the chain started with the glasses' ``request_id``, or ``None``."""
        result = await self._session.execute(
            select(Emergency).where(Emergency.request_id == request_id)
        )
        return result.scalar_one_or_none()

    async def get_locked(self, emergency_id: UUID) -> Emergency | None:
        """Return the chain locked ``FOR UPDATE`` (serialises worker and stop), or ``None``."""
        result = await self._session.execute(
            select(Emergency).where(Emergency.id == emergency_id).with_for_update()
        )
        return result.scalar_one_or_none()

    async def get_running_for_device(self, glasses_device_id: str) -> Emergency | None:
        """Return the chain still running for these glasses (newest), or ``None``."""
        result = await self._session.execute(
            select(Emergency)
            .where(
                Emergency.glasses_device_id == glasses_device_id,
                Emergency.state == EmergencyState.RUNNING,
            )
            .order_by(Emergency.created_at.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def lock_running(self, *, limit: int) -> list[Emergency]:
        """Lock running chains for the worker (other workers skip them)."""
        result = await self._session.execute(
            select(Emergency)
            .where(Emergency.state == EmergencyState.RUNNING)
            .order_by(Emergency.created_at)
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
        return list(result.scalars())

    async def claim_messages_due(
        self, *, now: datetime, lease_until: datetime, limit: int
    ) -> list[Emergency]:
        """Lease chains whose texts are not sent yet (a stopped chain never texts)."""
        result = await self._session.execute(
            select(Emergency)
            .where(
                Emergency.messages_sent.is_(False),
                Emergency.state != EmergencyState.STOPPED,
                (Emergency.messages_locked_until.is_(None))
                | (Emergency.messages_locked_until < now),
            )
            .order_by(Emergency.created_at)
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
        rows = list(result.scalars())
        for row in rows:
            row.messages_locked_until = lease_until
        await self._session.flush()
        return rows

    async def touch(self, emergency: Emergency) -> Emergency:
        """Flush a mutated chain, stamping ``updated_at``."""
        emergency.updated_at = datetime.now(UTC)
        await self._session.flush()
        return emergency
