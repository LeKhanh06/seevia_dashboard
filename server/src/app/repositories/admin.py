"""Admin account/session persistence and the read-only directory queries for the dashboard.

Repositories never commit; the service owns the transaction.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime  # noqa: TC003
from typing import TYPE_CHECKING
from uuid import UUID  # noqa: TC003

from sqlalchemy import func, select, update

from app.models.admin import AdminSession, AdminUser
from app.models.enums import GlassesLinkStatus
from app.models.glasses_device import GlassesDevice
from app.models.usage import UsageAttempt, UsageSession
from app.models.user import User
from app.repositories.usage_rows import ERROR_STATUSES

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


class AdminRepository:
    """``admin_users`` / ``admin_sessions`` primitives."""

    def __init__(self, session: AsyncSession) -> None:
        """Bind to a caller-owned session."""
        self._session = session

    async def get_by_email(self, email: str) -> AdminUser | None:
        """Return the admin with this (already lower-cased) email."""
        result = await self._session.execute(select(AdminUser).where(AdminUser.email == email))
        return result.scalar_one_or_none()

    async def get(self, admin_id: UUID) -> AdminUser | None:
        """Return the admin by internal id."""
        return await self._session.get(AdminUser, admin_id)

    async def create_admin(self, *, email: str, display_name: str, password_hash: str) -> AdminUser:
        """Insert a new admin. Does not commit."""
        record = AdminUser(email=email, display_name=display_name, password_hash=password_hash)
        self._session.add(record)
        await self._session.flush()
        return record

    async def list_admins(self) -> list[AdminUser]:
        """Every admin, oldest first."""
        result = await self._session.execute(select(AdminUser).order_by(AdminUser.number))
        return list(result.scalars())

    async def create_session(
        self, *, admin_id: UUID, token_hash: str, expires_at: datetime
    ) -> AdminSession:
        """Insert a session row. Does not commit."""
        record = AdminSession(admin_id=admin_id, token_hash=token_hash, expires_at=expires_at)
        self._session.add(record)
        await self._session.flush()
        return record

    async def get_valid_session(
        self, token_hash: str, *, now: datetime
    ) -> tuple[AdminSession, AdminUser] | None:
        """Return the not-revoked, not-expired session for ``token_hash`` with its admin."""
        result = await self._session.execute(
            select(AdminSession, AdminUser)
            .join(AdminUser, AdminUser.id == AdminSession.admin_id)
            .where(
                AdminSession.token_hash == token_hash,
                AdminSession.revoked.is_(False),
                AdminSession.expires_at > now,
            )
        )
        row = result.one_or_none()
        return None if row is None else (row[0], row[1])

    async def revoke_session(self, session_id: UUID) -> None:
        """Revoke one session. Does not commit."""
        await self._session.execute(
            update(AdminSession).where(AdminSession.id == session_id).values(revoked=True)
        )

    async def revoke_other_sessions(self, admin_id: UUID, keep: UUID) -> None:
        """Revoke every session of ``admin_id`` except ``keep``. Does not commit."""
        await self._session.execute(
            update(AdminSession)
            .where(
                AdminSession.admin_id == admin_id,
                AdminSession.id != keep,
                AdminSession.revoked.is_(False),
            )
            .values(revoked=True)
        )


@dataclass(frozen=True, slots=True)
class AccountRow:
    """One verified end-user account with its active glasses count."""

    public_user_id: str
    phone_number: str
    created_at: datetime
    glasses_count: int


@dataclass(frozen=True, slots=True)
class GlassesRow:
    """One active glasses pairing."""

    device_id: str
    owner_public_id: str


@dataclass(frozen=True, slots=True)
class ConnectionRow:
    """Latest connection-session facts for one device pseudonym."""

    device_hash: str
    firmware_version: str | None
    last_seen_at: datetime | None
    open: bool


@dataclass(frozen=True, slots=True)
class ErrorRow:
    """One attempt that ended with an error code."""

    code: str
    feature: str | None
    occurred_at: datetime


class AdminDirectoryRepository:
    """Read-only queries over accounts, glasses pairings and usage sessions."""

    def __init__(self, session: AsyncSession) -> None:
        """Bind to a caller-owned session."""
        self._session = session

    async def accounts(self) -> list[AccountRow]:
        """Every phone-verified account with its active glasses count."""
        glasses = (
            select(GlassesDevice.user_id, func.count().label("n"))
            .where(GlassesDevice.status == GlassesLinkStatus.ACTIVE)
            .group_by(GlassesDevice.user_id)
            .subquery()
        )
        result = await self._session.execute(
            select(
                User.public_user_id,
                User.phone_number,
                User.created_at,
                func.coalesce(glasses.c.n, 0),
            )
            .outerjoin(glasses, glasses.c.user_id == User.public_user_id)
            .where(User.phone_verified.is_(True))
        )
        return [AccountRow(r[0], r[1], r[2], int(r[3])) for r in result.all()]

    async def active_glasses(self) -> list[GlassesRow]:
        """Every active glasses pairing."""
        result = await self._session.execute(
            select(GlassesDevice.device_id, GlassesDevice.user_id).where(
                GlassesDevice.status == GlassesLinkStatus.ACTIVE
            )
        )
        return [GlassesRow(r[0], r[1]) for r in result.all()]

    async def latest_connections(self, device_hashes: list[str]) -> dict[str, ConnectionRow]:
        """Most recent ``connection`` session per device pseudonym."""
        if not device_hashes:
            return {}
        seen = func.coalesce(UsageSession.last_seen_at, UsageSession.started_at)
        ranked = (
            select(
                UsageSession.device_hash,
                UsageSession.firmware_version,
                seen.label("seen"),
                UsageSession.ended_at,
                func.row_number()
                .over(partition_by=UsageSession.device_hash, order_by=seen.desc().nulls_last())
                .label("rn"),
            )
            .where(UsageSession.kind == "connection", UsageSession.device_hash.in_(device_hashes))
            .subquery()
        )
        result = await self._session.execute(
            select(
                ranked.c.device_hash, ranked.c.firmware_version, ranked.c.seen, ranked.c.ended_at
            ).where(ranked.c.rn == 1)
        )
        return {r[0]: ConnectionRow(r[0], r[1], r[3] or r[2], r[3] is None) for r in result.all()}

    async def recent_errors(self, device_hash: str, limit: int = 10) -> list[ErrorRow]:
        """Newest attempts with an error code for one device pseudonym."""
        when = func.coalesce(UsageAttempt.finished_at, UsageAttempt.started_at)
        result = await self._session.execute(
            select(UsageAttempt.error_code, UsageAttempt.feature, when)
            .where(
                UsageAttempt.device_hash == device_hash,
                UsageAttempt.error_code.is_not(None),
                UsageAttempt.status.in_(ERROR_STATUSES),
                when.is_not(None),
            )
            .order_by(when.desc())
            .limit(limit)
        )
        return [ErrorRow(r[0], r[1], r[2]) for r in result.all()]
