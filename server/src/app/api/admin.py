"""Admin dashboard API (``/api/v1/admin``): sign-in, own profile, read-only directory.

Errors under this prefix use the dashboard envelope ``{"error": {"code", "message"}}``
(see :mod:`app.errors`). Statistics endpoints live in :mod:`app.api.admin_stats`.
"""

from __future__ import annotations

from email import policy
from email.parser import BytesParser
from typing import TYPE_CHECKING, Annotated, Literal

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import BearerCredentials  # noqa: TC001 -- FastAPI resolves it at runtime
from app.database import get_db_session
from app.errors import AdminApiError
from app.schemas.admin import (
    AccountDetail,
    AccountList,
    AdminAccountSummary,
    AdminConfigOut,
    AdminLoginIn,
    AdminLoginOut,
    AdminPasswordIn,
    AdminUserEnvelope,
    AdminUserOut,
    DeviceDetail,
    DeviceList,
    OkOut,
)
from app.services.admin_auth import (
    AVATAR_MAX_BYTES,
    AdminAuthService,
    AdminPrincipal,
    LoginThrottle,
    admin_public_id,
    avatar_url,
    mask_email,
)
from app.services.admin_directory import VN, AdminDirectoryService, ListQuery, NotFoundError

if TYPE_CHECKING:
    from app.models.admin import AdminUser

router = APIRouter(prefix="/api/v1/admin", tags=["admin"])

# Multipart overhead allowed on top of the avatar itself before we stop reading.
_MULTIPART_SLACK = 16 * 1024


def _throttle(request: Request) -> LoginThrottle:
    cached = getattr(request.app.state, "admin_login_throttle", None)
    if cached is None:
        cached = LoginThrottle()
        request.app.state.admin_login_throttle = cached
    return cached


def _service(request: Request, session: AsyncSession) -> AdminAuthService:
    return AdminAuthService(
        session,
        ttl_s=request.app.state.settings.admin_session_ttl_s,
        throttle=_throttle(request),
    )


def _unauthorized() -> AdminApiError:
    return AdminApiError(401, "unauthorized", "Phiên đăng nhập đã hết hạn hoặc không hợp lệ.")


async def require_admin_session(
    request: Request,
    credentials: BearerCredentials,
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> AdminPrincipal:
    """Resolve the dashboard bearer token (401 ``unauthorized`` otherwise)."""
    if credentials is None:
        raise _unauthorized()
    principal = await _service(request, session).authenticate(credentials.credentials)
    if principal is None:
        raise _unauthorized()
    return principal


Admin = Annotated[AdminPrincipal, Depends(require_admin_session)]
Db = Annotated[AsyncSession, Depends(get_db_session)]


def _user_out(admin: AdminUser) -> AdminUserOut:
    return AdminUserOut(
        id=admin_public_id(admin),
        display_name=admin.display_name,
        email_masked=mask_email(admin.email),
        avatar_url=avatar_url(admin),
        role=admin.role,
        timezone=admin.timezone,
        created_at=admin.created_at.astimezone(VN).date(),
        last_login_at=admin.last_login_at.astimezone(VN) if admin.last_login_at else None,
    )


@router.post("/auth/login", response_model=AdminLoginOut)
async def admin_login(body: AdminLoginIn, request: Request, session: Db) -> AdminLoginOut:
    """Email + password -> bearer token (``expires_in`` seconds)."""
    client_ip = request.client.host if request.client else None
    result = await _service(request, session).login(body.email, body.password, client_ip)
    return AdminLoginOut(
        token=result.token, expires_in=result.expires_in, user=_user_out(result.admin)
    )


@router.post("/auth/logout", response_model=OkOut)
async def admin_logout(admin: Admin, request: Request, session: Db) -> OkOut:
    """Revoke the current token."""
    await _service(request, session).logout(admin)
    return OkOut()


@router.get("/auth/me", response_model=AdminUserEnvelope)
async def admin_me(admin: Admin, request: Request, session: Db) -> AdminUserEnvelope:
    """Own profile."""
    return AdminUserEnvelope(user=_user_out(await _service(request, session).get(admin)))


@router.post("/auth/password", response_model=OkOut)
async def admin_password(
    body: AdminPasswordIn, admin: Admin, request: Request, session: Db
) -> OkOut:
    """Change own password (other sessions are revoked)."""
    await _service(request, session).change_password(
        admin, body.current_password, body.new_password
    )
    return OkOut()


async def _read_capped(request: Request, cap: int) -> bytes:
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > cap:
        raise AdminApiError(413, "image_too_large", "Ảnh quá lớn (tối đa 256 KB).")
    chunks: list[bytes] = []
    size = 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > cap:
            raise AdminApiError(413, "image_too_large", "Ảnh quá lớn (tối đa 256 KB).")
        chunks.append(chunk)
    return b"".join(chunks)


def _multipart_file(content_type: str, body: bytes) -> bytes | None:
    """Bytes of the ``file`` field of a multipart/form-data body (stdlib parser)."""
    if not content_type.lower().startswith("multipart/form-data"):
        return None
    message = BytesParser(policy=policy.HTTP).parsebytes(
        b"Content-Type: " + content_type.encode("latin-1") + b"\r\n\r\n" + body
    )
    if not message.is_multipart():
        return None
    for part in message.iter_parts():
        if part.get_param("name", header="content-disposition") == "file":
            payload = part.get_payload(decode=True)
            return payload if isinstance(payload, bytes) else None
    return None


@router.put("/auth/avatar", response_model=AdminUserEnvelope)
async def admin_set_avatar(admin: Admin, request: Request, session: Db) -> AdminUserEnvelope:
    """Upload own avatar: multipart field ``file``, JPEG/PNG/WebP, ≤ 256 KB."""
    body = await _read_capped(request, AVATAR_MAX_BYTES + _MULTIPART_SLACK)
    data = _multipart_file(request.headers.get("content-type", ""), body)
    if data is None:
        raise AdminApiError(422, "invalid_image", "Thiếu tệp ảnh (trường file).")
    user = await _service(request, session).set_avatar(admin, data)
    return AdminUserEnvelope(user=_user_out(user))


@router.delete("/auth/avatar", response_model=AdminUserEnvelope)
async def admin_clear_avatar(admin: Admin, request: Request, session: Db) -> AdminUserEnvelope:
    """Remove own avatar."""
    return AdminUserEnvelope(user=_user_out(await _service(request, session).clear_avatar(admin)))


def _directory(request: Request, session: AsyncSession) -> AdminDirectoryService:
    key = request.app.state.settings.usage_id_key
    return AdminDirectoryService(
        session, id_key=bytes.fromhex(key.get_secret_value()) if key else None
    )


Order = Literal["asc", "desc"]


@router.get("/accounts", response_model=AccountList)
async def admin_accounts(  # noqa: PLR0913
    *,
    _admin: Admin,
    request: Request,
    session: Db,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 10,
    sort: Literal["id", "created_at", "status", "glasses_count"] = "id",
    order: Order = "asc",
    q: Annotated[str | None, Query(max_length=64)] = None,
    status: Literal["active", "suspended"] | None = None,
) -> AccountList:
    """Accounts (phone masked)."""
    return await _directory(request, session).accounts(
        ListQuery(page=page, page_size=page_size, sort=sort, order=order, q=q, status=status)
    )


@router.get("/accounts/{account_id}", response_model=AccountDetail)
async def admin_account(
    account_id: str, _admin: Admin, request: Request, session: Db
) -> AccountDetail:
    """One account and its glasses."""
    try:
        return await _directory(request, session).account(account_id)
    except NotFoundError:
        raise AdminApiError(404, "not_found", "Không tìm thấy tài khoản.") from None


@router.get("/devices", response_model=DeviceList)
async def admin_devices(  # noqa: PLR0913
    *,
    _admin: Admin,
    request: Request,
    session: Db,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 10,
    sort: Literal["id", "firmware_version", "status", "last_seen_at", "account_id"] = "id",
    order: Order = "asc",
    q: Annotated[str | None, Query(max_length=64)] = None,
    status: Literal["online", "offline"] | None = None,
    firmware_version: Annotated[str | None, Query(max_length=40)] = None,
) -> DeviceList:
    """Glasses with online/offline status and firmware."""
    return await _directory(request, session).devices(
        ListQuery(
            page=page,
            page_size=page_size,
            sort=sort,
            order=order,
            q=q,
            status=status,
            firmware_version=firmware_version,
        )
    )


@router.get("/devices/{device_id}", response_model=DeviceDetail)
async def admin_device(
    device_id: str, _admin: Admin, request: Request, session: Db
) -> DeviceDetail:
    """One glasses and its recent errors."""
    try:
        return await _directory(request, session).device(device_id)
    except NotFoundError:
        raise AdminApiError(404, "not_found", "Không tìm thấy kính.") from None


@router.get("/config", response_model=AdminConfigOut)
async def admin_config(_admin: Admin, request: Request, session: Db) -> AdminConfigOut:
    """Admin accounts (masked)."""
    admins = await _service(request, session).list_admins()
    return AdminConfigOut(
        admin_accounts=[
            AdminAccountSummary(
                email_masked=mask_email(a.email),
                created_at=a.created_at.astimezone(VN).date(),
                last_login_at=a.last_login_at.astimezone(VN) if a.last_login_at else None,
            )
            for a in admins
        ]
    )
