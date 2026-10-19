"""Unit tests for WebhookDeliveryAdapter (no real network: httpx.MockTransport)."""

import json
from datetime import UTC, datetime
from uuid import uuid4

import httpx
import pytest

from app.adapters.delivery import (
    DeliveryOutcomeStatus,
    WebhookDeliveryAdapter,
    _command_data,
)
from app.config import AppEnvironment, DeliveryTransport, Settings
from app.models.device import Device
from app.models.enums import Action, DevicePlatform, DeviceStatus, Operation
from app.models.operation import Operation as OperationModel


@pytest.fixture
def settings() -> Settings:
    """Webhook-mode configuration."""
    return Settings.model_validate(
        {
            "app_env": AppEnvironment.TEST,
            "http_port": 8000,
            "database_url": "postgresql+asyncpg://test:test@localhost:5432/app_test",
            "public_api_token_hash": "1" * 64,
            "public_api_client_id": "test-client",
            "public_api_scopes": {"service:execute", "requests:read"},
            "device_api_token_hash": "2" * 64,
            "field_encryption_key": "test-only-field-key",
            "delivery_transport": DeliveryTransport.WEBHOOK,
            "delivery_webhook_url": "http://phone-sim.test/push",
        }
    )


@pytest.fixture
def device() -> Device:
    """Minimal active device."""
    return Device(
        user_id="user-1",
        device_id="device-1",
        platform=DevicePlatform.ANDROID,
        push_token_ciphertext="token-1",
        push_token_fingerprint="fp-1",
        status=DeviceStatus.ACTIVE,
        last_seen_at=datetime.now(UTC),
    )


@pytest.fixture
def operation() -> OperationModel:
    """Return a representative processing operation."""
    now = datetime.now(UTC)
    return OperationModel(
        request_id=uuid4(),
        client_id="client-1",
        user_id="user-1",
        device_id="device-1",
        operation=Operation.CONTACT_CALL,
        action=Action.CONTACT_CALL,
        params={"name": "mẹ"},
        request_fingerprint="f" * 64,
        created_at=now,
        updated_at=now,
        expires_at=now,
    )


@pytest.mark.asyncio
async def test_webhook_sends_fcm_payload(
    settings: Settings, device: Device, operation: OperationModel
) -> None:
    """Webhook body is exactly the FCM data payload, token only in the header."""
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200)

    adapter = WebhookDeliveryAdapter(settings, transport=httpx.MockTransport(handler))
    outcome = await adapter.send_command(device=device, push_token="sim", operation=operation)

    assert outcome.status is DeliveryOutcomeStatus.SENT
    assert len(seen) == 1
    assert str(seen[0].url) == "http://phone-sim.test/push"
    assert seen[0].headers["X-Push-Token"] == "sim"
    assert json.loads(seen[0].content) == _command_data(operation)
    assert json.loads(json.loads(seen[0].content)["params_json"]) == {"name": "mẹ"}


@pytest.mark.asyncio
async def test_webhook_5xx_transient(
    settings: Settings, device: Device, operation: OperationModel
) -> None:
    """5xx and network errors are retryable."""
    adapter = WebhookDeliveryAdapter(
        settings, transport=httpx.MockTransport(lambda _r: httpx.Response(503))
    )
    outcome = await adapter.send_command(device=device, push_token="sim", operation=operation)
    assert outcome.status is DeliveryOutcomeStatus.TRANSIENT_FAILURE

    def boom(_request: httpx.Request) -> httpx.Response:
        message = "refused"
        raise httpx.ConnectError(message)

    adapter = WebhookDeliveryAdapter(settings, transport=httpx.MockTransport(boom))
    outcome = await adapter.send_command(device=device, push_token="sim", operation=operation)
    assert outcome.status is DeliveryOutcomeStatus.TRANSIENT_FAILURE


@pytest.mark.asyncio
async def test_webhook_4xx_permanent(
    settings: Settings, device: Device, operation: OperationModel
) -> None:
    """4xx (other than 408/429) is not retried."""
    adapter = WebhookDeliveryAdapter(
        settings, transport=httpx.MockTransport(lambda _r: httpx.Response(404))
    )
    outcome = await adapter.send_command(device=device, push_token="sim", operation=operation)
    assert outcome.status is DeliveryOutcomeStatus.PERMANENT_FAILURE
