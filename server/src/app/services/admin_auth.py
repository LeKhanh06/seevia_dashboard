"""Admin dashboard sign-in, sessions and own-profile edits.

Passwords use ``hashlib.scrypt`` (stdlib; n=2**14, r=8, p=1, 16-byte salt). Session tokens are
``secrets.token_urlsafe(32)`` stored as SHA-256. Failed logins are throttled per email in memory
so a single process cannot be brute forced faster than scrypt already allows.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import threading
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Final

from app.errors import AdminApiError
from app.repositories.admin import AdminRepository
from app.security import hash_token

if TYPE_CHECKING:
    from uuid import UUID

    from sqlalchemy.ext.asyncio import AsyncSession

    from app.models.admin import AdminUser

_N, _R, _P = 2**14, 8, 1
_SALT_BYTES = 16
_DKLEN = 32
_PREFIX: Final = "scrypt"
AVATAR_MAX_BYTES: Final = 256 * 1024
_LOGIN_MAX_FAILURES: Final = 5
_LOGIN_WINDOW_S: Final = 900.0


def hash_password(password: str) -> str:
    """Return ``scrypt$n$r$p$salt_hex$digest_hex``."""
    salt = secrets.token_bytes(_SALT_BYTES)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=_N, r=_R, p=_P, dklen=_DKLEN)
    return f"{_PREFIX}${_N}${_R}${_P}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    """Constant-time check; malformed ``stored`` -> ``False``."""
    try:
        prefix, n, r, p, salt_hex, digest_hex = stored.split("$")
        if prefix != _PREFIX:
            return False
        expected = bytes.fromhex(digest_hex)
        actual = hashlib.scrypt(
            password.encode(),
            salt=bytes.fromhex(salt_hex),
            n=int(n),
            r=int(r),
            p=int(p),
            dklen=len(expected),
        )
    except ValueError:
        return False
    return hmac.compare_digest(actual, expected)


# Hash compared when the email is unknown, so both paths cost one scrypt.
_DUMMY_HASH: Final = hash_password(secrets.token_urlsafe(16))


def password_is_strong(password: str) -> bool:
    """At least 8 characters with at least one letter and one digit."""
    return (
        len(password) >= 8  # noqa: PLR2004
        and any(c.isalpha() for c in password)
        and any(c.isdigit() for c in password)
    )


def normalize_email(email: str) -> str:
    """Trim and lower-case."""
    return email.strip().lower()


def mask_email(email: str) -> str:
    """``khanh@gmail.com`` -> ``kh***@gmail.com``."""
    local, _, domain = email.partition("@")
    return f"{local[:2]}***@{domain}" if domain else "***"


def admin_public_id(admin: AdminUser) -> str:
    """``AD-0001`` style display id."""
    return f"AD-{admin.number:04d}"


def avatar_type(data: bytes) -> str | None:
    """Image MIME type from magic bytes (JPEG/PNG/WebP), else ``None``."""
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":  # noqa: PLR2004
        return "image/webp"
    return None


def avatar_url(admin: AdminUser) -> str | None:
    """Return the stored avatar as a ``data:`` URL, or ``None``."""
    if admin.avatar is None or admin.avatar_type is None:
        return None
    return f"data:{admin.avatar_type};base64,{base64.b64encode(admin.avatar).decode()}"


class LoginThrottle:
    """Per-email failed-login counter in a sliding window (in-process)."""

    def __init__(
        self, max_failures: int = _LOGIN_MAX_FAILURES, window_s: float = _LOGIN_WINDOW_S
    ) -> None:
        """Configure the limit."""
        self._max = max_failures
        self._window = window_s
        self._failures: dict[str, list[float]] = {}
        self._lock = threading.Lock()

    def _recent(self, email: str, now: float) -> list[float]:
        kept = [t for t in self._failures.get(email, []) if now - t < self._window]
        if kept:
            self._failures[email] = kept
        else:
            self._failures.pop(email, None)
        return kept

    def blocked(self, email: str) -> bool:
        """Tell whether ``email`` reached the failure limit inside the window."""
        with self._lock:
            return len(self._recent(email, time.monotonic())) >= self._max

    def fail(self, email: str) -> None:
        """Record one failure."""
        with self._lock:
            now = time.monotonic()
            self._failures[email] = [*self._recent(email, now), now]

    def reset(self, email: str) -> None:
        """Forget failures after a successful login."""
        with self._lock:
            self._failures.pop(email, None)


@dataclass(frozen=True, slots=True)
class AdminPrincipal:
    """Signed-in admin plus the session it used."""

    admin_id: UUID
    session_id: UUID


@dataclass(frozen=True, slots=True)
class LoginResult:
    """Issued token and the admin it belongs to."""

    token: str
    expires_in: int
    admin: AdminUser


def _invalid_credentials() -> AdminApiError:
    return AdminApiError(401, "invalid_credentials", "Email hoặc mật khẩu không đúng.")


class AdminAuthService:
    """Login/logout/profile use cases; commits its own writes."""

    def __init__(self, session: AsyncSession, *, ttl_s: int, throttle: LoginThrottle) -> None:
        """Bind to a request session."""
        self._session = session
        self._repo = AdminRepository(session)
        self._ttl_s = ttl_s
        self._throttle = throttle

    async def login(self, email: str, password: str) -> LoginResult:
        """Verify credentials and issue a session token."""
        key = normalize_email(email)
        if self._throttle.blocked(key):
            raise AdminApiError(
                429, "too_many_attempts", "Đăng nhập sai quá nhiều lần, thử lại sau 15 phút."
            )
        admin = await self._repo.get_by_email(key)
        ok = verify_password(password, admin.password_hash if admin else _DUMMY_HASH)
        if admin is None or not ok:
            self._throttle.fail(key)
            raise _invalid_credentials()
        self._throttle.reset(key)
        now = datetime.now(UTC)
        token = secrets.token_urlsafe(32)
        await self._repo.create_session(
            admin_id=admin.id,
            token_hash=hash_token(token),
            expires_at=now + timedelta(seconds=self._ttl_s),
        )
        admin.last_login_at = now
        await self._session.commit()
        await self._session.refresh(admin)
        return LoginResult(token=token, expires_in=self._ttl_s, admin=admin)

    async def authenticate(self, token: str) -> AdminPrincipal | None:
        """Resolve a bearer token to its admin; ``None`` when invalid/expired/revoked."""
        found = await self._repo.get_valid_session(hash_token(token), now=datetime.now(UTC))
        if found is None:
            return None
        record, admin = found
        return AdminPrincipal(admin_id=admin.id, session_id=record.id)

    async def get(self, principal: AdminPrincipal) -> AdminUser:
        """Load the signed-in admin."""
        admin = await self._repo.get(principal.admin_id)
        if admin is None:
            raise AdminApiError(401, "unauthorized", "Phiên đăng nhập không hợp lệ.")
        return admin

    async def logout(self, principal: AdminPrincipal) -> None:
        """Revoke the current session."""
        await self._repo.revoke_session(principal.session_id)
        await self._session.commit()

    async def change_password(self, principal: AdminPrincipal, current: str, new: str) -> None:
        """Change own password; other sessions are revoked, the current one stays."""
        admin = await self.get(principal)
        if not verify_password(current, admin.password_hash):
            raise AdminApiError(400, "invalid_current_password", "Mật khẩu hiện tại không đúng.")
        if not password_is_strong(new):
            raise AdminApiError(
                422, "weak_password", "Mật khẩu mới cần ít nhất 8 ký tự, có cả chữ và số."
            )
        if verify_password(new, admin.password_hash):
            raise AdminApiError(422, "same_password", "Mật khẩu mới trùng mật khẩu cũ.")
        admin.password_hash = hash_password(new)
        await self._repo.revoke_other_sessions(admin.id, keep=principal.session_id)
        await self._session.commit()

    async def set_avatar(self, principal: AdminPrincipal, data: bytes) -> AdminUser:
        """Store a JPEG/PNG/WebP avatar (≤ 256 KB)."""
        if len(data) > AVATAR_MAX_BYTES:
            raise AdminApiError(413, "image_too_large", "Ảnh quá lớn (tối đa 256 KB).")
        kind = avatar_type(data)
        if kind is None:
            raise AdminApiError(422, "invalid_image", "Ảnh phải là JPEG, PNG hoặc WebP.")
        admin = await self.get(principal)
        admin.avatar, admin.avatar_type = data, kind
        await self._session.commit()
        await self._session.refresh(admin)
        return admin

    async def clear_avatar(self, principal: AdminPrincipal) -> AdminUser:
        """Remove the avatar."""
        admin = await self.get(principal)
        admin.avatar, admin.avatar_type = None, None
        await self._session.commit()
        await self._session.refresh(admin)
        return admin

    async def create_admin(self, *, email: str, display_name: str, password: str) -> AdminUser:
        """Create an admin (used by ``scripts/create_admin.py``)."""
        if not password_is_strong(password):
            raise AdminApiError(
                422, "weak_password", "Mật khẩu cần ít nhất 8 ký tự, có cả chữ và số."
            )
        key = normalize_email(email)
        if "@" not in key:
            raise AdminApiError(422, "invalid_email", "Email không hợp lệ.")
        if await self._repo.get_by_email(key) is not None:
            raise AdminApiError(409, "email_taken", "Email đã có tài khoản quản trị.")
        admin = await self._repo.create_admin(
            email=key,
            display_name=display_name.strip() or key,
            password_hash=hash_password(password),
        )
        await self._session.commit()
        return admin

    async def list_admins(self) -> list[AdminUser]:
        """Every admin (for ``/admin/config``)."""
        return await self._repo.list_admins()
