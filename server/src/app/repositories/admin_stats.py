"""Read-only queries behind ``/api/v1/admin/stats/*`` (layer 3, plus a few layer-2/user counts).

Rows come back as plain tuples/dataclasses; every number is shaped by
:mod:`app.services.admin_stats`. Nothing here returns a raw device id or phone number.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime  # noqa: TC003
from typing import TYPE_CHECKING, Any

from sqlalchemy import distinct, func, select

from app.models.care_link import CareLink
from app.models.enums import CareLinkStatus, GlassesLinkStatus
from app.models.glasses_device import GlassesDevice
from app.models.usage import UsageActiveDay, UsageAttempt, UsageDaily, UsageHourly
from app.models.user import User

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


@dataclass(frozen=True, slots=True)
class DailyRow:
    """One ``usage_daily`` row."""

    day: date
    owner_hash: str
    device_hash: str
    feature: str
    firmware_version: str
    metrics: dict[str, Any]


class AdminStatsRepository:
    """Statistics reads for the admin dashboard."""

    def __init__(self, session: AsyncSession) -> None:
        """Bind to a request session."""
        self._session = session

    async def daily(self, first: date, last: date, owner_hash: str | None = None) -> list[DailyRow]:
        """Every ``usage_daily`` row with ``first <= day <= last`` (optionally one owner)."""
        d = UsageDaily
        stmt = select(
            d.day, d.owner_hash, d.device_hash, d.feature, d.firmware_version, d.metrics
        ).where(d.day >= first, d.day <= last)
        if owner_hash is not None:
            stmt = stmt.where(d.owner_hash == owner_hash)
        result = await self._session.execute(stmt)
        return [DailyRow(r[0], r[1], r[2], r[3], r[4], dict(r[5])) for r in result.all()]

    async def hourly(self, first: date, last: date) -> list[tuple[date, int, str, int]]:
        """``(day, hour, feature, uses)`` for ``first <= day <= last``."""
        h = UsageHourly
        result = await self._session.execute(
            select(h.day, h.hour, h.feature, h.uses).where(h.day >= first, h.day <= last)
        )
        return [(r[0], int(r[1]), r[2], int(r[3])) for r in result.all()]

    async def active_days(
        self, first: date, last: date, owner_hash: str | None = None
    ) -> list[tuple[date, str, str]]:
        """``(day, owner_hash, device_hash)`` with a real use, ``first <= day <= last``."""
        a = UsageActiveDay
        stmt = select(a.day, a.owner_hash, a.device_hash).where(a.day >= first, a.day <= last)
        if owner_hash is not None:
            stmt = stmt.where(a.owner_hash == owner_hash)
        result = await self._session.execute(stmt)
        return [(r[0], r[1], r[2]) for r in result.all()]

    async def owners_first_seen_between(self, first: date, last: date) -> int:
        """Owners whose very first active day falls in ``[first, last]``."""
        a = UsageActiveDay
        firsts = (
            select(a.owner_hash, func.min(a.day).label("first_day"))
            .where(a.owner_hash != "")
            .group_by(a.owner_hash)
            .subquery()
        )
        result = await self._session.execute(
            select(func.count()).where(firsts.c.first_day >= first, firsts.c.first_day <= last)
        )
        return int(result.scalar_one())

    async def owners_by_hour(self, lo: datetime, hi: datetime, tz: str) -> dict[int, int]:
        """Distinct paired owners per local hour among attempts started in ``[lo, hi)``."""
        a = UsageAttempt
        hour = func.extract("hour", func.timezone(tz, a.started_at))
        result = await self._session.execute(
            select(hour, func.count(distinct(a.owner_hash)))
            .where(a.started_at >= lo, a.started_at < hi, a.owner_hash.is_not(None))
            .group_by(hour)
        )
        return {int(r[0]): int(r[1]) for r in result.all()}

    async def new_accounts(self, lo: datetime, hi: datetime) -> int:
        """Count verified accounts created in ``[lo, hi)``."""
        result = await self._session.execute(
            select(func.count()).where(
                User.phone_verified.is_(True), User.created_at >= lo, User.created_at < hi
            )
        )
        return int(result.scalar_one())

    async def total_accounts(self) -> int:
        """Count verified accounts (same set as ``/admin/accounts``)."""
        result = await self._session.execute(
            select(func.count()).where(User.phone_verified.is_(True))
        )
        return int(result.scalar_one())

    async def emergency_contact_accounts(self) -> int:
        """Count verified accounts with at least one active emergency relative."""
        result = await self._session.execute(
            select(func.count(distinct(CareLink.owner_user_id)))
            .join(User, User.public_user_id == CareLink.owner_user_id)
            .where(
                CareLink.status == CareLinkStatus.ACTIVE,
                CareLink.emergency.is_(True),
                User.phone_verified.is_(True),
            )
        )
        return int(result.scalar_one())

    async def total_glasses(self) -> int:
        """Active glasses pairings."""
        result = await self._session.execute(
            select(func.count()).where(GlassesDevice.status == GlassesLinkStatus.ACTIVE)
        )
        return int(result.scalar_one())
