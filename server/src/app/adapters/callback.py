"""POST terminal results to the External API Client's callback URL.

The target host must be in ``CALLBACK_ALLOWED_HOSTS`` (SSRF guard). Requests
carry ``Authorization: Bearer <CALLBACK_TOKEN>`` and ``Idempotency-Key:
<request_id>``. 2xx is delivered, 408/429/5xx/network errors are retried,
any other status is dead-lettered.
"""

from __future__ import annotations

import ipaddress
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING
from urllib.parse import urlparse, urlsplit, urlunsplit

import httpx

if TYPE_CHECKING:
    from uuid import UUID

    from app.config import Settings
    from app.models.operation import Operation

_HTTP_OK_MIN = 200
_HTTP_OK_MAX = 300
_HTTP_SERVER_ERROR_MIN = 500


class CallbackOutcomeStatus(StrEnum):
    """Possible outcomes of an HTTP callback attempt."""

    DELIVERED = "delivered"
    RETRY = "retry"
    DEAD_LETTER = "dead_letter"


@dataclass(frozen=True, slots=True)
class CallbackOutcome:
    """Result of attempting an HTTP webhook callback."""

    status: CallbackOutcomeStatus
    http_status_code: int | None = None
    error_reason: str | None = None


class SsfValidationError(ValueError):
    """Raised when a callback URL fails SSRF security checks."""


def validate_callback_url(url: str, allowed_hosts: list[str]) -> None:
    """Reject non-HTTP schemes, hosts outside ``allowed_hosts`` and unlisted private IPs."""
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        msg = f"Invalid URL scheme: {parsed.scheme}"
        raise SsfValidationError(msg)

    hostname = parsed.hostname
    if not hostname:
        msg = "Callback URL missing hostname"
        raise SsfValidationError(msg)

    if allowed_hosts and hostname not in allowed_hosts:
        msg = f"Host '{hostname}' is not in CALLBACK_ALLOWED_HOSTS"
        raise SsfValidationError(msg)

    try:
        ip = ipaddress.ip_address(hostname)
        if (ip.is_private or ip.is_loopback or ip.is_link_local) and (
            not allowed_hosts or hostname not in allowed_hosts
        ):
            msg = f"Forbidden IP target: {hostname}"
            raise SsfValidationError(msg)
    except ValueError:
        pass


def _allowed_hosts(settings: Settings) -> list[str]:
    """Parse ``CALLBACK_ALLOWED_HOSTS`` into a host list (empty when unset)."""
    return [h.strip() for h in (settings.callback_allowed_hosts or "").split(",") if h.strip()]


def _classify(status_code: int) -> CallbackOutcome:
    """Map an HTTP status to the callback outcome (2xx delivered, 408/429/5xx retry, else dead)."""
    if _HTTP_OK_MIN <= status_code < _HTTP_OK_MAX:
        return CallbackOutcome(status=CallbackOutcomeStatus.DELIVERED, http_status_code=status_code)
    if status_code in {408, 429} or status_code >= _HTTP_SERVER_ERROR_MIN:
        return CallbackOutcome(
            status=CallbackOutcomeStatus.RETRY,
            http_status_code=status_code,
            error_reason=f"HTTP {status_code}",
        )
    return CallbackOutcome(
        status=CallbackOutcomeStatus.DEAD_LETTER,
        http_status_code=status_code,
        error_reason=f"HTTP {status_code} permanent error",
    )


class CallbackAdapter:
    """HTTP webhook callback delivery adapter."""

    def __init__(self, settings: Settings, client: httpx.AsyncClient | None = None) -> None:
        """Initialize CallbackAdapter with settings and optional HTTPX client."""
        self._settings = settings
        self._client = client

    async def send_callback(self, operation: Operation) -> CallbackOutcome:
        """Post terminal status representation to callback URL."""
        if not self._settings.callback_url:
            return CallbackOutcome(
                status=CallbackOutcomeStatus.DEAD_LETTER,
                error_reason="CALLBACK_URL is not configured",
            )

        url_str = str(self._settings.callback_url)
        allowed_hosts = (
            [
                host.strip()
                for host in self._settings.callback_allowed_hosts.split(",")
                if host.strip()
            ]
            if self._settings.callback_allowed_hosts
            else []
        )

        try:
            validate_callback_url(url_str, allowed_hosts)
        except SsfValidationError as exc:
            return CallbackOutcome(
                status=CallbackOutcomeStatus.DEAD_LETTER,
                error_reason=f"SSRF validation failed: {exc}",
            )

        payload = {
            "status": "ok",
            "data": {
                "request_id": str(operation.request_id),
                "operation": operation.operation.value,
                "request_state": operation.request_state.value,
                "result": operation.result,
                "error": operation.error,
                "created_at": operation.created_at.isoformat(),
                "updated_at": operation.updated_at.isoformat(),
            },
        }

        headers = {
            "Content-Type": "application/json",
            "Idempotency-Key": str(operation.request_id),
        }
        if self._settings.callback_token:
            headers["Authorization"] = f"Bearer {self._settings.callback_token.get_secret_value()}"

        client = self._client or httpx.AsyncClient(timeout=10.0)
        close_client = self._client is None

        try:
            response = await client.post(url_str, json=payload, headers=headers)
            status_code = response.status_code

            if _HTTP_OK_MIN <= status_code < _HTTP_OK_MAX:
                return CallbackOutcome(
                    status=CallbackOutcomeStatus.DELIVERED,
                    http_status_code=status_code,
                )
            if status_code in {408, 429} or status_code >= _HTTP_SERVER_ERROR_MIN:
                return CallbackOutcome(
                    status=CallbackOutcomeStatus.RETRY,
                    http_status_code=status_code,
                    error_reason=f"HTTP {status_code}",
                )
            return CallbackOutcome(
                status=CallbackOutcomeStatus.DEAD_LETTER,
                http_status_code=status_code,
                error_reason=f"HTTP {status_code} permanent error",
            )
        except (OSError, httpx.HTTPError) as exc:
            return CallbackOutcome(
                status=CallbackOutcomeStatus.RETRY,
                error_reason=f"Network error: {exc}",
            )
        finally:
            if close_client:
                await client.aclose()


class CallEventAdapter:
    """POST glasses-server events (calls, messages) to the callback origin."""

    def __init__(self, settings: Settings, client: httpx.AsyncClient | None = None) -> None:
        """Bind validated settings and an optional reusable HTTP client."""
        self._settings = settings
        self._client = client

    async def send_event(self, *, event_id: int, payload: dict[str, object]) -> CallbackOutcome:
        """POST one call event; ``Idempotency-Key`` is the outbox id so the receiver can dedupe."""
        return await self._post(
            path="/internal/calls", idempotency_key=f"call-event-{event_id}", payload=payload
        )

    async def send_message_event(
        self, *, message_id: UUID, payload: dict[str, object]
    ) -> CallbackOutcome:
        """POST ``message_received`` to ``/internal/messages`` (dedupe key = the message id)."""
        return await self._post(
            path="/internal/messages",
            idempotency_key=f"message-{message_id}",
            payload=payload,
        )

    async def _post(
        self, *, path: str, idempotency_key: str, payload: dict[str, object]
    ) -> CallbackOutcome:
        if self._settings.callback_url is None or self._settings.callback_token is None:
            return CallbackOutcome(
                status=CallbackOutcomeStatus.DEAD_LETTER,
                error_reason="CALLBACK_URL is not configured",
            )
        parsed = urlsplit(str(self._settings.callback_url))
        url = urlunsplit((parsed.scheme, parsed.netloc, path, "", ""))
        try:
            validate_callback_url(url, _allowed_hosts(self._settings))
        except SsfValidationError as exc:
            return CallbackOutcome(
                status=CallbackOutcomeStatus.DEAD_LETTER,
                error_reason=f"SSRF validation failed: {exc}",
            )
        headers = {
            "Content-Type": "application/json",
            "Idempotency-Key": idempotency_key,
            "Authorization": f"Bearer {self._settings.callback_token.get_secret_value()}",
        }
        client = self._client or httpx.AsyncClient(timeout=5.0)
        try:
            response = await client.post(url, json=payload, headers=headers)
        except (OSError, httpx.HTTPError) as exc:
            return CallbackOutcome(
                status=CallbackOutcomeStatus.RETRY, error_reason=f"Network error: {exc}"
            )
        finally:
            if self._client is None:
                await client.aclose()
        return _classify(response.status_code)
