"""Push commands to phones: FCM data messages, or a fake transport for local runs.

Adapters only report an outcome (sent / transient / permanent); the delivery
worker decides what that means for the operation.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from datetime import timedelta
from enum import StrEnum
from http import HTTPStatus
from typing import TYPE_CHECKING, Any, Protocol
from urllib.parse import quote

import firebase_admin
import httpx
from firebase_admin import credentials, messaging
from firebase_admin.exceptions import FirebaseError

from app.actions import Action
from app.config import DeliveryTransport, Settings

if TYPE_CHECKING:
    from app.models.device import Device
    from app.models.operation import Operation

logger = logging.getLogger("app.adapters.delivery")

_FCM_APP_NAME = "app-demo-fcm"


class DeliveryOutcomeStatus(StrEnum):
    """Possible outcomes of a delivery attempt."""

    SENT = "sent"
    TRANSIENT_FAILURE = "transient_failure"
    PERMANENT_FAILURE = "permanent_failure"


@dataclass(frozen=True, slots=True)
class DeliveryOutcome:
    """Result of attempting to deliver a command to a device."""

    status: DeliveryOutcomeStatus
    provider_message_id: str | None = None
    error_reason: str | None = None
    invalid_token: bool = False


class DeliveryAdapter(Protocol):
    """Protocol for device message delivery transports."""

    async def send_command(
        self,
        *,
        device: Device,
        push_token: str,
        operation: Operation,
    ) -> DeliveryOutcome:
        """Deliver a command message to a device."""
        ...

    async def send_data(
        self, *, push_token: str, data: dict[str, str], label: str
    ) -> DeliveryOutcome:
        """Deliver a plain data message (call events); ``label`` is the log correlation id."""
        ...


class FakeDeliveryAdapter:
    """Deterministic fake delivery transport for local development and testing."""

    async def send_command(
        self,
        *,
        device: Device,
        push_token: str,
        operation: Operation,
    ) -> DeliveryOutcome:
        """Simulate immediate successful delivery with a deterministic message ID."""
        _ = (device, push_token)
        provider_message_id = f"fake-msg-{operation.request_id}"
        return DeliveryOutcome(
            status=DeliveryOutcomeStatus.SENT,
            provider_message_id=provider_message_id,
        )

    async def send_data(
        self, *, push_token: str, data: dict[str, str], label: str
    ) -> DeliveryOutcome:
        """Simulate immediate successful delivery of a data message."""
        _ = (push_token, data)
        return DeliveryOutcome(
            status=DeliveryOutcomeStatus.SENT, provider_message_id=f"fake-data-{label}"
        )


def _get_fcm_app(settings: Settings) -> firebase_admin.App:
    """Return the cached Firebase Admin app, initializing it on first use."""
    try:
        return firebase_admin.get_app(_FCM_APP_NAME)
    except ValueError:
        if settings.google_application_credentials is not None:
            cred = credentials.Certificate(str(settings.google_application_credentials))
        else:
            cred = credentials.ApplicationDefault()
        return firebase_admin.initialize_app(
            cred,
            options={"projectId": settings.fcm_project_id},
            name=_FCM_APP_NAME,
        )


def _maps_url(params: dict[str, Any]) -> str | None:
    """URL chỉ đường đi bộ Google Maps cho navigation_start, hoặc None nếu thiếu đích.

    Ưu tiên địa chỉ; không có địa chỉ thì dùng "lat,lng". App mở URL này để dẫn đường.
    """
    destination = params.get("destination")
    if not isinstance(destination, dict):
        return None
    address = destination.get("address")
    lat, lng = destination.get("lat"), destination.get("lng")
    if isinstance(address, str) and address.strip():
        target = address.strip()
    elif all(isinstance(v, int | float) and not isinstance(v, bool) for v in (lat, lng)):
        target = f"{lat},{lng}"
    else:
        return None
    return (
        "https://www.google.com/maps/dir/?api=1"
        f"&destination={quote(target, safe=',')}&travelmode=walking"
    )


def _command_data(operation: Operation) -> dict[str, str]:
    """Build the data payload the Android receiver expects.

    FCM và webhook dùng chung hàm này để điện thoại giả lập nhận đúng cái điện
    thoại thật nhận. Payload không mang credential: điện thoại báo kết quả bằng
    URL và token đã lưu lúc đăng ký.
    """
    data = {
        "request_id": str(operation.request_id),
        "user_id": operation.user_id,
        "device_id": operation.device_id or "",
        "action": operation.action.value,
        "params_json": json.dumps(operation.params, separators=(",", ":")),
        "issued_at": operation.created_at.isoformat(),
        "deadline_at": (operation.created_at + timedelta(seconds=1)).isoformat(),
        "expires_at": operation.expires_at.isoformat(),
        "requested_priority": "high",
    }
    if operation.action is Action.NAVIGATION_START and (url := _maps_url(operation.params)):
        data["maps_url"] = url  # trường thêm: app mở thẳng Google Maps, không đổi params_json
    return data


def _build_message(*, push_token: str, operation: Operation) -> messaging.Message:
    """Build the data-only FCM message from the shared command payload."""
    return messaging.Message(
        data=_command_data(operation),
        token=push_token,
        android=messaging.AndroidConfig(priority="high", ttl=timedelta(seconds=10)),
    )


# Cuộc gọi đổ chuông tối đa CALL_RING_TIMEOUT_S (30 s): tin quá hạn thì vô nghĩa, để FCM bỏ.
_CALL_PUSH_TTL = timedelta(seconds=30)


class FcmDeliveryAdapter:
    """FCM delivery transport using Firebase Admin SDK."""

    def __init__(self, settings: Settings) -> None:
        """Initialize FcmDeliveryAdapter with validated settings."""
        self._settings = settings

    async def send_command(
        self,
        *,
        device: Device,
        push_token: str,
        operation: Operation,
    ) -> DeliveryOutcome:
        """Deliver a data message to Android device via FCM."""
        _ = device
        message = _build_message(push_token=push_token, operation=operation)
        return await self._send(message, f"request {operation.request_id}")

    async def send_data(
        self, *, push_token: str, data: dict[str, str], label: str
    ) -> DeliveryOutcome:
        """Deliver a data-only message (call events) with the short call TTL."""
        message = messaging.Message(
            data=data,
            token=push_token,
            android=messaging.AndroidConfig(priority="high", ttl=_CALL_PUSH_TTL),
        )
        return await self._send(message, label)

    async def _send(self, message: messaging.Message, label: str) -> DeliveryOutcome:
        """Send via the Admin SDK and map the exception to an outcome."""
        app = _get_fcm_app(self._settings)
        try:
            message_id = await asyncio.to_thread(messaging.send, message, app=app)
        except messaging.UnregisteredError as exc:
            logger.info("FCM token unregistered for %s", label)
            return DeliveryOutcome(
                status=DeliveryOutcomeStatus.PERMANENT_FAILURE,
                error_reason=str(exc),
                invalid_token=True,
            )
        except (messaging.SenderIdMismatchError, messaging.ThirdPartyAuthError) as exc:
            logger.warning("FCM permanent send failure for %s", label)
            return DeliveryOutcome(
                status=DeliveryOutcomeStatus.PERMANENT_FAILURE,
                error_reason=str(exc),
            )
        except (messaging.QuotaExceededError, FirebaseError) as exc:
            logger.warning("FCM transient send failure for %s", label)
            return DeliveryOutcome(
                status=DeliveryOutcomeStatus.TRANSIENT_FAILURE,
                error_reason=str(exc),
            )

        return DeliveryOutcome(
            status=DeliveryOutcomeStatus.SENT,
            provider_message_id=message_id,
        )


class WebhookDeliveryAdapter:
    """POST the FCM data payload to a URL (phone simulator / e2e), not to FCM.

    Chỉ dùng để chạy E2E không cần điện thoại thật. Quy ước kết quả giống FCM:
    2xx = sent, lỗi mạng/5xx/429 = transient, 4xx khác = permanent.
    """

    def __init__(
        self, settings: Settings, *, transport: httpx.AsyncBaseTransport | None = None
    ) -> None:
        """Initialize with validated settings (DELIVERY_WEBHOOK_URL required)."""
        if settings.delivery_webhook_url is None:
            message = "DELIVERY_WEBHOOK_URL is required for the webhook transport"
            raise ValueError(message)
        self._url = str(settings.delivery_webhook_url)
        self._timeout = httpx.Timeout(5.0)
        self._transport = transport  # chỉ test truyền vào

    async def send_command(
        self,
        *,
        device: Device,
        push_token: str,
        operation: Operation,
    ) -> DeliveryOutcome:
        """POST the command data with ``X-Push-Token``; map the status to an outcome."""
        _ = device
        return await self._post(_command_data(operation), push_token, str(operation.request_id))

    async def send_data(
        self, *, push_token: str, data: dict[str, str], label: str
    ) -> DeliveryOutcome:
        """POST a data message (call events) exactly as FCM would carry it."""
        return await self._post(data, push_token, label)

    async def _post(self, data: dict[str, str], push_token: str, label: str) -> DeliveryOutcome:
        """POST ``data`` with ``X-Push-Token``; map the status to an outcome."""
        try:
            async with httpx.AsyncClient(
                timeout=self._timeout, transport=self._transport
            ) as client:
                response = await client.post(
                    self._url,
                    json=data,
                    headers={"X-Push-Token": push_token},
                )
        except httpx.HTTPError as exc:
            logger.warning("Webhook send failed for %s", label)
            return DeliveryOutcome(
                status=DeliveryOutcomeStatus.TRANSIENT_FAILURE,
                error_reason=type(exc).__name__,
            )

        code = response.status_code
        if response.is_success:
            return DeliveryOutcome(
                status=DeliveryOutcomeStatus.SENT,
                provider_message_id=f"webhook-{label}",
            )
        status = (
            DeliveryOutcomeStatus.TRANSIENT_FAILURE
            if response.is_server_error
            or code in (HTTPStatus.REQUEST_TIMEOUT, HTTPStatus.TOO_MANY_REQUESTS)
            else DeliveryOutcomeStatus.PERMANENT_FAILURE
        )
        return DeliveryOutcome(status=status, error_reason=f"webhook HTTP {code}")


# Token giả lập (relative_sim: "sim-<số>", phone_sim: "sim"): không bao giờ là token FCM thật.
_SIM_TOKEN_PREFIX = "sim"


class HybridDeliveryAdapter:
    """FCM cho điện thoại thật, webhook cho điện thoại giả lập (token bắt đầu bằng ``sim``).

    Chỉ bật khi ``DELIVERY_TRANSPORT=fcm`` kèm ``DELIVERY_WEBHOOK_URL``: dùng cho stack thật có
    người thân giả lập (``relative_sim.py``) chạy cạnh điện thoại chủ kính thật. Không đặt URL thì
    hành vi như cũ (mọi token đi FCM).
    """

    def __init__(self, fcm: DeliveryAdapter, webhook: DeliveryAdapter) -> None:
        """Bind the two transports."""
        self._fcm = fcm
        self._webhook = webhook

    def _pick(self, push_token: str) -> DeliveryAdapter:
        return self._webhook if push_token.startswith(_SIM_TOKEN_PREFIX) else self._fcm

    async def send_command(
        self,
        *,
        device: Device,
        push_token: str,
        operation: Operation,
    ) -> DeliveryOutcome:
        """Route the command by token shape."""
        return await self._pick(push_token).send_command(
            device=device, push_token=push_token, operation=operation
        )

    async def send_data(
        self, *, push_token: str, data: dict[str, str], label: str
    ) -> DeliveryOutcome:
        """Route the data message by token shape."""
        return await self._pick(push_token).send_data(
            push_token=push_token, data=data, label=label
        )


def get_delivery_adapter(settings: Settings) -> DeliveryAdapter:
    """Return the configured delivery transport adapter."""
    if settings.delivery_transport is DeliveryTransport.FCM:
        if settings.delivery_webhook_url is not None:
            return HybridDeliveryAdapter(
                FcmDeliveryAdapter(settings), WebhookDeliveryAdapter(settings)
            )
        return FcmDeliveryAdapter(settings)
    if settings.delivery_transport is DeliveryTransport.WEBHOOK:
        return WebhookDeliveryAdapter(settings)
    return FakeDeliveryAdapter()
