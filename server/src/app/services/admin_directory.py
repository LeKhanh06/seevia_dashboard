"""Read-only accounts/glasses directory for the admin dashboard.

Ids shown are the existing ``public_user_id`` / glasses ``device_id``; phone numbers are only
ever returned masked. Device status comes from the newest ``connection`` usage session, joined
through the HMAC pseudonym (``USAGE_ID_KEY``); without the key every device reads ``offline``.
Paging is done in memory: the pilot has a handful of accounts, and the query cost stays tiny.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any, Literal
from zoneinfo import ZoneInfo

from app.repositories.admin import AdminDirectoryRepository, ConnectionRow
from app.schemas.admin import (
    AccountDetail,
    AccountGlasses,
    AccountItem,
    AccountList,
    DeviceDetail,
    DeviceError,
    DeviceItem,
    DeviceList,
)
from app.services.usage_ids import pseudonym

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from sqlalchemy.ext.asyncio import AsyncSession

ONLINE_WITHIN = timedelta(seconds=120)
VN = ZoneInfo("Asia/Ho_Chi_Minh")


def mask_phone(phone: str) -> str:
    """``0912345612`` -> ``09******12``; very short numbers are fully masked."""
    digits = phone.strip()
    if len(digits) <= 4:  # noqa: PLR2004
        return "*" * len(digits)
    return f"{digits[:2]}{'*' * (len(digits) - 4)}{digits[-2:]}"


def vn_time(value: datetime | None) -> datetime | None:
    """Render an instant in Vietnam time (ISO output carries ``+07:00``)."""
    return None if value is None else value.astimezone(VN)


class NotFoundError(Exception):
    """Directory item does not exist."""


@dataclass(frozen=True, slots=True)
class ListQuery:
    """Common list parameters (already validated by the router)."""

    page: int = 1
    page_size: int = 10
    sort: str | None = None
    order: Literal["asc", "desc"] = "asc"
    q: str | None = None
    status: str | None = None
    firmware_version: str | None = None


def _page[T](items: Sequence[T], query: ListQuery) -> list[T]:
    start = (query.page - 1) * query.page_size
    return list(items[start : start + query.page_size])


def _sorted[T](items: list[T], key: Callable[[T], Any], order: str) -> list[T]:
    # None sorts last ascending (and first descending), never raises on mixed types.
    return sorted(items, key=lambda i: (key(i) is None, key(i)), reverse=order == "desc")


class AdminDirectoryService:
    """Accounts and glasses for the dashboard admin pages."""

    def __init__(self, session: AsyncSession, *, id_key: bytes | None) -> None:
        """Bind to a request session; ``id_key`` resolves device pseudonyms."""
        self._repo = AdminDirectoryRepository(session)
        self._key = id_key

    async def _devices(self, now: datetime) -> list[DeviceItem]:
        glasses = await self._repo.active_glasses()
        hashes = (
            {g.device_id: pseudonym(self._key, "device", g.device_id) for g in glasses}
            if (self._key)
            else {}
        )
        conns = await self._repo.latest_connections(list(hashes.values()))
        out: list[DeviceItem] = []
        for g in glasses:
            conn: ConnectionRow | None = conns.get(hashes.get(g.device_id, ""))
            online = bool(
                conn
                and conn.open
                and conn.last_seen_at
                and now - conn.last_seen_at <= ONLINE_WITHIN
            )
            out.append(
                DeviceItem(
                    id=g.device_id,
                    firmware_version=conn.firmware_version if conn else None,
                    status="online" if online else "offline",
                    last_seen_at=vn_time(conn.last_seen_at) if conn else None,
                    account_id=g.owner_public_id,
                )
            )
        return out

    async def accounts(self, query: ListQuery) -> AccountList:
        """Return accounts filtered, sorted and paged."""
        rows = await self._repo.accounts()
        items = [
            AccountItem(
                id=r.public_user_id,
                phone_masked=mask_phone(r.phone_number),
                created_at=r.created_at.astimezone(VN).date(),
                status="active",
                glasses_count=r.glasses_count,
            )
            for r in rows
        ]
        if query.q:
            needle = query.q.strip().lower()
            items = [i for i in items if needle in i.id.lower()]
        if query.status:
            items = [i for i in items if i.status == query.status]
        keys: dict[str, Callable[[AccountItem], Any]] = {
            "id": lambda i: i.id,
            "created_at": lambda i: i.created_at,
            "status": lambda i: i.status,
            "glasses_count": lambda i: i.glasses_count,
        }
        items = _sorted(items, keys.get(query.sort or "id", keys["id"]), query.order)
        return AccountList(
            items=_page(items, query), total=len(items), page=query.page, page_size=query.page_size
        )

    async def account(self, account_id: str) -> AccountDetail:
        """One account with its glasses."""
        match = [r for r in await self._repo.accounts() if r.public_user_id == account_id]
        if not match:
            raise NotFoundError
        r = match[0]
        devices = [d for d in await self._devices(datetime.now(UTC)) if d.account_id == account_id]
        return AccountDetail(
            id=r.public_user_id,
            phone_masked=mask_phone(r.phone_number),
            created_at=r.created_at.astimezone(VN).date(),
            status="active",
            glasses_count=r.glasses_count,
            glasses=[
                AccountGlasses(
                    id=d.id,
                    firmware_version=d.firmware_version,
                    status=d.status,
                    last_seen_at=d.last_seen_at,
                )
                for d in devices
            ],
        )

    async def devices(self, query: ListQuery) -> DeviceList:
        """Return glasses filtered, sorted and paged."""
        items = await self._devices(datetime.now(UTC))
        versions = sorted({d.firmware_version for d in items if d.firmware_version})
        if query.q:
            needle = query.q.strip().lower()
            items = [i for i in items if needle in i.id.lower()]
        if query.status:
            items = [i for i in items if i.status == query.status]
        if query.firmware_version:
            items = [i for i in items if i.firmware_version == query.firmware_version]
        keys: dict[str, Callable[[DeviceItem], Any]] = {
            "id": lambda i: i.id,
            "firmware_version": lambda i: i.firmware_version,
            "status": lambda i: i.status,
            "last_seen_at": lambda i: i.last_seen_at,
            "account_id": lambda i: i.account_id,
        }
        items = _sorted(items, keys.get(query.sort or "id", keys["id"]), query.order)
        return DeviceList(
            items=_page(items, query),
            total=len(items),
            page=query.page,
            page_size=query.page_size,
            available_firmware_versions=versions,
        )

    async def device(self, device_id: str) -> DeviceDetail:
        """One glasses with its recent errors."""
        match = [d for d in await self._devices(datetime.now(UTC)) if d.id == device_id]
        if not match:
            raise NotFoundError
        errors = (
            await self._repo.recent_errors(pseudonym(self._key, "device", device_id))
            if self._key
            else []
        )
        return DeviceDetail(
            **match[0].model_dump(),
            recent_errors=[
                DeviceError(
                    code=e.code, feature=e.feature, occurred_at=e.occurred_at.astimezone(VN)
                )
                for e in errors
            ],
        )
