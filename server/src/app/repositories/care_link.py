"""Care link persistence primitives. Never commits; the caller owns the transaction."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from sqlalchemy import or_, select

from app.models.care_link import CareLink
from app.models.enums import CareLinkStatus

if TYPE_CHECKING:
    from uuid import UUID

    from sqlalchemy.ext.asyncio import AsyncSession


class CareLinkRepository:
    """Persistence primitives for ``care_links``."""

    def __init__(self, session: AsyncSession) -> None:
        """Bind the repository to a caller-owned session/transaction."""
        self._session = session

    async def get_pair_locked(self, owner_user_id: str, relative_user_id: str) -> CareLink | None:
        """Return the link of the (owner, relative) pair locked ``FOR UPDATE``, or ``None``."""
        result = await self._session.execute(
            select(CareLink)
            .where(
                CareLink.owner_user_id == owner_user_id,
                CareLink.relative_user_id == relative_user_id,
            )
            .with_for_update()
        )
        return result.scalar_one_or_none()

    async def get_locked(self, link_id: UUID) -> CareLink | None:
        """Return the link locked ``FOR UPDATE``, or ``None``."""
        result = await self._session.execute(
            select(CareLink).where(CareLink.id == link_id).with_for_update()
        )
        return result.scalar_one_or_none()

    async def create(  # noqa: PLR0913
        self,
        *,
        owner_user_id: str,
        relative_user_id: str,
        display_name: str,
        aliases: list[str],
        emergency: bool,
        priority: int,
    ) -> CareLink:
        """Insert a ``pending`` link."""
        link = CareLink(
            owner_user_id=owner_user_id,
            relative_user_id=relative_user_id,
            display_name=display_name,
            aliases=aliases,
            emergency=emergency,
            priority=priority,
            status=CareLinkStatus.PENDING,
        )
        self._session.add(link)
        await self._session.flush()
        return link

    async def touch(self, link: CareLink) -> CareLink:
        """Flush a mutated link, stamping ``updated_at``."""
        link.updated_at = datetime.now(UTC)
        await self._session.flush()
        return link

    async def list_for_user(self, user_id: str) -> list[CareLink]:
        """Return non-revoked links where ``user_id`` is owner or relative."""
        result = await self._session.execute(
            select(CareLink)
            .where(
                or_(CareLink.owner_user_id == user_id, CareLink.relative_user_id == user_id),
                CareLink.status != CareLinkStatus.REVOKED,
            )
            .order_by(CareLink.priority, CareLink.created_at)
        )
        return list(result.scalars())

    async def list_active_for_owner(self, owner_user_id: str) -> list[CareLink]:
        """Return the owner's ``active`` links ordered by priority."""
        result = await self._session.execute(
            select(CareLink)
            .where(
                CareLink.owner_user_id == owner_user_id,
                CareLink.status == CareLinkStatus.ACTIVE,
            )
            .order_by(CareLink.priority, CareLink.created_at)
        )
        return list(result.scalars())

    async def get_active_pair(self, owner_user_id: str, relative_user_id: str) -> CareLink | None:
        """Return the ``active`` link of the (owner, relative) pair, or ``None``."""
        result = await self._session.execute(
            select(CareLink).where(
                CareLink.owner_user_id == owner_user_id,
                CareLink.relative_user_id == relative_user_id,
                CareLink.status == CareLinkStatus.ACTIVE,
            )
        )
        return result.scalar_one_or_none()
