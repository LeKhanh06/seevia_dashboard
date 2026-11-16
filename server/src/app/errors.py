"""Typed domain errors and the handlers that render them as the Public error envelope.

Services raise :class:`PublicApiError` subclasses; only the handlers here turn
them into HTTP responses. Auth failures (401/403) stay plain ``HTTPException``.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, Final

from fastapi import status
from fastapi.exception_handlers import http_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.schemas.common import ErrorResponse, PublicError

if TYPE_CHECKING:
    from fastapi import FastAPI, Request
    from starlette.responses import Response

logger = logging.getLogger(__name__)

# OpenAPI description of the 400 validation envelope for runtime routers.
VALIDATION_ERROR_RESPONSES: Final[dict[int | str, dict[str, Any]]] = {
    status.HTTP_400_BAD_REQUEST: {
        "model": ErrorResponse,
        "description": "Invalid request",
        "content": {
            "application/json": {
                "example": {
                    "status": "error",
                    "error": {
                        "code": "INVALID_REQUEST",
                        "message": "Invalid request",
                        "details": {},
                    },
                }
            }
        },
    }
}


class PublicApiError(Exception):
    """Base error: subclasses fix ``status_code`` and ``code``; instances carry message/details."""

    status_code: int
    code: str

    def __init__(self, message: str, *, details: dict[str, Any] | None = None) -> None:
        """Store the Public-facing message and optional structured details."""
        super().__init__(message)
        self.message = message
        self.details: dict[str, Any] = details if details is not None else {}


class RequestNotFoundError(PublicApiError):
    """Raised when a ``request_id`` does not exist or belongs to another caller."""

    status_code = status.HTTP_404_NOT_FOUND
    code = "REQUEST_NOT_FOUND"


class RequestIdConflictError(PublicApiError):
    """Raised when a ``request_id`` already exists with a different fingerprint/client."""

    status_code = status.HTTP_409_CONFLICT
    code = "REQUEST_ID_CONFLICT"


class PhoneAlreadyRegisteredError(PublicApiError):
    """Raised when registering a phone number that already completed OTP verification."""

    status_code = status.HTTP_409_CONFLICT
    code = "PHONE_ALREADY_REGISTERED"


class InvalidCredentialsError(PublicApiError):
    """Raised when login phone number/password do not match a verified account."""

    status_code = status.HTTP_401_UNAUTHORIZED
    code = "INVALID_CREDENTIALS"


class PhoneNotVerifiedError(PublicApiError):
    """Raised when logging in to an account that never completed OTP verification."""

    status_code = status.HTTP_403_FORBIDDEN
    code = "PHONE_NOT_VERIFIED"


class OtpInvalidError(PublicApiError):
    """Raised when a submitted OTP code does not match the stored hash."""

    status_code = status.HTTP_400_BAD_REQUEST
    code = "OTP_INVALID"


class OtpExpiredError(PublicApiError):
    """Raised when a submitted OTP code has expired or has no pending OTP."""

    status_code = status.HTTP_400_BAD_REQUEST
    code = "OTP_EXPIRED"


class DeviceNotFoundError(PublicApiError):
    """Raised when confirming a device link for a ``device_id`` with no active registration."""

    status_code = status.HTTP_404_NOT_FOUND
    code = "DEVICE_NOT_FOUND"


class DeviceOwnerConflictError(PublicApiError):
    """Raised when confirming a device link for a ``device_id`` owned by another user."""

    status_code = status.HTTP_409_CONFLICT
    code = "DEVICE_OWNER_CONFLICT"


class GlassesDeviceOwnerConflictError(PublicApiError):
    """Raised when linking a glasses ``device_id`` that is actively paired to another user."""

    status_code = status.HTTP_409_CONFLICT
    code = "GLASSES_DEVICE_OWNER_CONFLICT"


class GlassesDeviceNotLinkedError(PublicApiError):
    """Raised when a Public Service API ``device_id`` has no active glasses pairing."""

    status_code = status.HTTP_404_NOT_FOUND
    code = "GLASSES_DEVICE_NOT_LINKED"


class UserNotFoundError(PublicApiError):
    """Raised when an invited relative's phone number has no registered account."""

    status_code = status.HTTP_404_NOT_FOUND
    code = "USER_NOT_FOUND"


class CareLinkNotFoundError(PublicApiError):
    """Raised when a care link is missing or the caller is not a party to it."""

    status_code = status.HTTP_404_NOT_FOUND
    code = "CARE_LINK_NOT_FOUND"


class CareLinkConflictError(PublicApiError):
    """Raised when a care link already exists for the pair or is in the wrong state."""

    status_code = status.HTTP_409_CONFLICT
    code = "CARE_LINK_CONFLICT"


class ContactNotFoundError(PublicApiError):
    """Raised when no active care link matches the spoken contact name."""

    status_code = status.HTTP_404_NOT_FOUND
    code = "CONTACT_NOT_FOUND"


class MultipleContactsFoundError(PublicApiError):
    """Raised when several active care links match; ``details.candidates`` lists their names."""

    status_code = status.HTTP_409_CONFLICT
    code = "MULTIPLE_CONTACTS_FOUND"


class EmergencyNotFoundError(PublicApiError):
    """Raised when an emergency chain does not exist."""

    status_code = status.HTTP_404_NOT_FOUND
    code = "EMERGENCY_NOT_FOUND"


class NoEmergencyContactError(PublicApiError):
    """Raised when the owner has no ``active`` relative flagged for emergencies."""

    status_code = status.HTTP_404_NOT_FOUND
    code = "EMERGENCY_CONTACT_NOT_FOUND"


class CallNotFoundError(PublicApiError):
    """Raised when a call is missing or the caller is not a party to it."""

    status_code = status.HTTP_404_NOT_FOUND
    code = "CALL_NOT_FOUND"


class CallNotAllowedError(PublicApiError):
    """Raised when the caller has no ``active`` care link for the call."""

    status_code = status.HTTP_403_FORBIDDEN
    code = "CALL_NOT_ALLOWED"


class CallStateConflictError(PublicApiError):
    """Raised when the requested transition is impossible from the call's current state."""

    status_code = status.HTTP_409_CONFLICT
    code = "CALL_STATE_CONFLICT"


class MessageNotFoundError(PublicApiError):
    """Raised when a message is missing or the caller is not its glasses' owner."""

    status_code = status.HTTP_404_NOT_FOUND
    code = "MESSAGE_NOT_FOUND"


class MessageNotAllowedError(PublicApiError):
    """Raised when the caller has no ``active`` care link with the other party."""

    status_code = status.HTTP_403_FORBIDDEN
    code = "MESSAGE_NOT_ALLOWED"


class DeviceEventForwardError(PublicApiError):
    """Raised when a spontaneous phone event cannot reach the glasses server."""

    status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    code = "DEVICE_EVENT_FORWARD_FAILED"


def _error_response(
    *, status_code: int, code: str, message: str, details: dict[str, Any]
) -> JSONResponse:
    """Build the stable ``{"status": "error", "error": {...}}`` JSON response."""
    envelope = ErrorResponse(error=PublicError(code=code, message=message, details=details))
    return JSONResponse(status_code=status_code, content=envelope.model_dump(mode="json"))


async def _handle_public_api_error(_request: Request, exc: Exception) -> JSONResponse:
    """Map a typed domain error to its stable Public error envelope."""
    if not isinstance(exc, PublicApiError):
        raise exc
    return _error_response(
        status_code=exc.status_code, code=exc.code, message=exc.message, details=exc.details
    )


async def _handle_validation_error(request: Request, _exc: Exception) -> JSONResponse:
    """Map FastAPI's 422 to ``400 INVALID_REQUEST`` without echoing input or error details."""
    if _is_admin(request):
        return _admin_default(status.HTTP_400_BAD_REQUEST)
    return _error_response(
        status_code=status.HTTP_400_BAD_REQUEST,
        code="INVALID_REQUEST",
        message="Request validation failed",
        details={},
    )


async def _handle_unexpected_error(request: Request, exc: Exception) -> JSONResponse:
    """Log the exception and return a generic ``500 INTERNAL_ERROR`` envelope."""
    logger.error(
        "Unhandled exception while processing %s %s: %s",
        request.method,
        request.url.path,
        exc,
    )

    if _is_admin(request):
        return _admin_default(status.HTTP_500_INTERNAL_SERVER_ERROR)
    return _error_response(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        code="INTERNAL_ERROR",
        message="Internal server error",
        details={},
    )


ADMIN_PREFIX: Final = "/api/v1/admin"
_ADMIN_CODES: Final[dict[int, tuple[str, str]]] = {
    status.HTTP_400_BAD_REQUEST: ("invalid_request", "Yêu cầu không hợp lệ."),
    status.HTTP_401_UNAUTHORIZED: ("unauthorized", "Phiên đăng nhập đã hết hạn hoặc không hợp lệ."),
    status.HTTP_403_FORBIDDEN: ("forbidden", "Không có quyền truy cập."),
    status.HTTP_404_NOT_FOUND: ("not_found", "Không tìm thấy."),
    status.HTTP_405_METHOD_NOT_ALLOWED: ("method_not_allowed", "Phương thức không được hỗ trợ."),
    status.HTTP_500_INTERNAL_SERVER_ERROR: ("internal_error", "Lỗi máy chủ."),
}


def _is_admin(request: Request) -> bool:
    path = request.url.path
    return path == ADMIN_PREFIX or path.startswith(ADMIN_PREFIX + "/")


def admin_error_response(status_code: int, code: str, message: str) -> JSONResponse:
    """Dashboard envelope ``{"error": {"code", "message"}}`` (admin routes only)."""
    return JSONResponse(
        status_code=status_code, content={"error": {"code": code, "message": message}}
    )


def _admin_default(status_code: int) -> JSONResponse:
    code, message = _ADMIN_CODES.get(status_code, ("error", "Có lỗi xảy ra."))
    return admin_error_response(status_code, code, message)


class AdminApiError(Exception):
    """Admin dashboard error: HTTP status, lower-case ``code``, Vietnamese ``message``."""

    def __init__(self, status_code: int, code: str, message: str) -> None:
        """Store the response fields."""
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message


async def _handle_admin_api_error(_request: Request, exc: Exception) -> JSONResponse:
    """Render :class:`AdminApiError` (raised only by admin code)."""
    if not isinstance(exc, AdminApiError):
        raise exc
    response = admin_error_response(exc.status_code, exc.code, exc.message)
    if exc.status_code == status.HTTP_401_UNAUTHORIZED:
        response.headers["WWW-Authenticate"] = "Bearer"
    return response


async def _handle_http_exception(request: Request, exc: Exception) -> Response:
    """Admin paths get the dashboard envelope; everything else keeps FastAPI's default."""
    if not isinstance(exc, StarletteHTTPException):
        raise exc
    if _is_admin(request):
        return _admin_default(exc.status_code)
    return await http_exception_handler(request, exc)


def register_exception_handlers(app: FastAPI) -> None:
    """Register validation, domain and catch-all handlers (``HTTPException`` keeps its own)."""
    app.add_exception_handler(RequestValidationError, _handle_validation_error)
    app.add_exception_handler(StarletteHTTPException, _handle_http_exception)
    app.add_exception_handler(AdminApiError, _handle_admin_api_error)
    app.add_exception_handler(PublicApiError, _handle_public_api_error)
    app.add_exception_handler(Exception, _handle_unexpected_error)
