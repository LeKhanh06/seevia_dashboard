"""Admin dashboard request/response schemas (shapes follow seevia_dashboard API_CONTRACT.md)."""

from __future__ import annotations

from datetime import date, datetime  # noqa: TC003
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class AdminLoginIn(BaseModel):
    """``POST /admin/auth/login`` body."""

    model_config = ConfigDict(extra="forbid")

    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=1, max_length=256)


class AdminPasswordIn(BaseModel):
    """``POST /admin/auth/password`` body."""

    model_config = ConfigDict(extra="forbid")

    current_password: str = Field(min_length=1, max_length=256)
    new_password: str = Field(min_length=1, max_length=256)


class AdminUserOut(BaseModel):
    """The signed-in admin's own profile."""

    id: str
    display_name: str
    email_masked: str
    avatar_url: str | None
    role: str
    timezone: str
    created_at: date
    last_login_at: datetime | None


class AdminUserEnvelope(BaseModel):
    """``{"user": {...}}``."""

    user: AdminUserOut


class AdminLoginOut(BaseModel):
    """Login response."""

    token: str
    expires_in: int
    user: AdminUserOut


class OkOut(BaseModel):
    """``{"ok": true}``."""

    ok: bool = True


AccountStatus = Literal["active", "suspended"]
DeviceStatus = Literal["online", "offline"]


class AccountItem(BaseModel):
    """One account row. AB accounts sign in by phone, so ``email_masked`` is always null."""

    id: str
    email_masked: str | None = None
    phone_masked: str
    created_at: date
    status: AccountStatus
    glasses_count: int


class AccountList(BaseModel):
    """Paged accounts."""

    items: list[AccountItem]
    total: int
    page: int
    page_size: int


class AccountGlasses(BaseModel):
    """Glasses summary inside an account."""

    id: str
    firmware_version: str | None
    status: DeviceStatus
    last_seen_at: datetime | None


class AccountDetail(AccountItem):
    """Account plus its glasses."""

    glasses: list[AccountGlasses]


class DeviceItem(BaseModel):
    """One glasses row."""

    id: str
    firmware_version: str | None
    status: DeviceStatus
    last_seen_at: datetime | None
    account_id: str


class DeviceList(BaseModel):
    """Paged glasses."""

    items: list[DeviceItem]
    total: int
    page: int
    page_size: int
    available_firmware_versions: list[str]


class DeviceError(BaseModel):
    """One recent error of a device."""

    code: str
    feature: str | None
    occurred_at: datetime


class DeviceDetail(DeviceItem):
    """Device plus recent errors."""

    recent_errors: list[DeviceError]


class AdminAccountSummary(BaseModel):
    """One admin in ``/admin/config``."""

    email_masked: str
    created_at: date
    last_login_at: datetime | None


class AdminConfigOut(BaseModel):
    """``/admin/config``."""

    admin_accounts: list[AdminAccountSummary]
