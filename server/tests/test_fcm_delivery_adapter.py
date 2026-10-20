"""Unit tests for FcmDeliveryAdapter real Firebase Cloud Messaging send path.

No real network/Firebase call is made: ``firebase_admin.messaging.send`` and
app resolution are mocked per CLAUDE.md ("Không gọi network/provider thật
trong unit test").
"""

import json
from datetime import UTC, datetime
from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from firebase_admin import messaging

from app.adapters.delivery import DeliveryOutcomeStatus, FcmDeliveryAdapter, _command_data
from app.config import AppEnvironment, DeliveryTransport, Settings
from app.models.device import Device
from app.models.enums import Action, DevicePlatform, DeviceStatus, Operation
from app.models.operation import Operation as OperationModel


@pytest.fixture
def settings() -> Settings:
    """Fixture providing FCM-mode configuration for adapter tests."""
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
            "delivery_transport": DeliveryTransport.FCM,
            "fcm_project_id": "demo-project",
        }
    )


@pytest.fixture
def device() -> Device:
    """Fixture providing a minimal active device."""
    now = datetime.now(UTC)
    return Device(
        user_id="user-1",
        device_id="device-1",
        platform=DevicePlatform.ANDROID,
        push_token_ciphertext="token-1",
        push_token_fingerprint="fp-1",
        status=DeviceStatus.ACTIVE,
        last_seen_at=now,
    )


@pytest.fixture
def operation() -> OperationModel:
    """Fixture providing a representative processing operation."""
    now = datetime.now(UTC)
    return OperationModel(
        request_id=uuid4(),
        client_id="client-1",
        user_id="user-1",
        device_id="device-1",
        operation=Operation.CONTACT_CALL,
        action=Action.CONTACT_CALL,
        params={"name": "Nguyen Van A"},
        request_fingerprint="f" * 64,
        created_at=now,
        updated_at=now,
        expires_at=now,
    )


def _patch_app(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "app.adapters.delivery._get_fcm_app", lambda _settings: MagicMock(name="fake-app")
    )


@pytest.mark.asyncio
async def test_fcm_adapter_sends_data_only_message_without_credentials(
    monkeypatch: pytest.MonkeyPatch,
    settings: Settings,
    device: Device,
    operation: OperationModel,
) -> None:
    """Verify the adapter builds a flat data message and never leaks a bearer token."""
    _patch_app(monkeypatch)
    sent_messages: list[messaging.Message] = []

    def fake_send(message: messaging.Message, **_kwargs: object) -> str:
        sent_messages.append(message)
        return "projects/demo-project/messages/abc123"

    monkeypatch.setattr(messaging, "send", fake_send)

    adapter = FcmDeliveryAdapter(settings)
    outcome = await adapter.send_command(
        device=device, push_token="push-token-1", operation=operation
    )

    assert outcome.status is DeliveryOutcomeStatus.SENT
    assert outcome.provider_message_id == "projects/demo-project/messages/abc123"
    assert len(sent_messages) == 1
    sent = sent_messages[0]
    assert sent.token == "push-token-1"
    assert sent.data["request_id"] == str(operation.request_id)
    assert sent.data["user_id"] == "user-1"
    assert sent.data["device_id"] == "device-1"
    assert sent.data["action"] == "contact_call"
    assert json.loads(sent.data["params_json"]) == {"name": "Nguyen Van A"}
    assert "bearer_token" not in sent.data
    assert "base_url" not in sent.data


@pytest.mark.asyncio
async def test_fcm_adapter_maps_unregistered_token_to_permanent_invalid_token(
    monkeypatch: pytest.MonkeyPatch,
    settings: Settings,
    device: Device,
    operation: OperationModel,
) -> None:
    """Verify an unregistered FCM token is classified as a permanent, invalid-token failure."""
    _patch_app(monkeypatch)

    def fake_send(_message: messaging.Message, **_kwargs: object) -> str:
        error_message = "token no longer registered"
        raise messaging.UnregisteredError(error_message)

    monkeypatch.setattr(messaging, "send", fake_send)

    adapter = FcmDeliveryAdapter(settings)
    outcome = await adapter.send_command(
        device=device, push_token="stale-token", operation=operation
    )

    assert outcome.status is DeliveryOutcomeStatus.PERMANENT_FAILURE
    assert outcome.invalid_token is True


@pytest.mark.asyncio
async def test_fcm_adapter_maps_quota_exceeded_to_transient_failure(
    monkeypatch: pytest.MonkeyPatch,
    settings: Settings,
    device: Device,
    operation: OperationModel,
) -> None:
    """Verify quota-exceeded errors are classified as retryable transient failures."""
    _patch_app(monkeypatch)

    def fake_send(_message: messaging.Message, **_kwargs: object) -> str:
        error_message = "rate limited"
        raise messaging.QuotaExceededError(error_message)

    monkeypatch.setattr(messaging, "send", fake_send)

    adapter = FcmDeliveryAdapter(settings)
    outcome = await adapter.send_command(device=device, push_token="token-1", operation=operation)

    assert outcome.status is DeliveryOutcomeStatus.TRANSIENT_FAILURE
    assert outcome.invalid_token is False


@pytest.mark.asyncio
async def test_fcm_adapter_maps_sender_id_mismatch_to_permanent_failure(
    monkeypatch: pytest.MonkeyPatch,
    settings: Settings,
    device: Device,
    operation: OperationModel,
) -> None:
    """Verify a sender ID mismatch is classified as a permanent (non-retryable) failure."""
    _patch_app(monkeypatch)

    def fake_send(_message: messaging.Message, **_kwargs: object) -> str:
        error_message = "sender id mismatch"
        raise messaging.SenderIdMismatchError(error_message)

    monkeypatch.setattr(messaging, "send", fake_send)

    adapter = FcmDeliveryAdapter(settings)
    outcome = await adapter.send_command(device=device, push_token="token-1", operation=operation)

    assert outcome.status is DeliveryOutcomeStatus.PERMANENT_FAILURE
    assert outcome.invalid_token is False


def _nav_operation(operation: OperationModel, params: dict[str, object]) -> OperationModel:
    """Đổi operation mẫu thành lệnh navigation_start với params cho trước."""
    operation.operation = Operation.NAVIGATION_START
    operation.action = Action.NAVIGATION_START
    operation.params = params
    return operation


def test_navigation_start_payload_has_maps_url(operation: OperationModel) -> None:
    """navigation_start có maps_url top-level: địa chỉ tiếng Việt hoặc lat/lng, đi bộ."""
    by_address = _command_data(
        _nav_operation(operation, {"destination": {"address": "Bưu điện Thành phố Hồ Chí Minh"}})
    )
    assert by_address["maps_url"] == (
        "https://www.google.com/maps/dir/?api=1"
        "&destination=B%C6%B0u%20%C4%91i%E1%BB%87n%20Th%C3%A0nh%20ph%E1%BB%91"
        "%20H%E1%BB%93%20Ch%C3%AD%20Minh&travelmode=walking"
    )

    by_coords = _command_data(
        _nav_operation(operation, {"destination": {"lat": 10.7721, "lng": 106.6578}})
    )
    assert by_coords["maps_url"] == (
        "https://www.google.com/maps/dir/?api=1&destination=10.7721,106.6578&travelmode=walking"
    )
    # Trường cũ giữ nguyên, thêm maps_url là additive.
    assert json.loads(by_coords["params_json"]) == {
        "destination": {"lat": 10.7721, "lng": 106.6578}
    }


def test_maps_url_only_for_navigation_start_with_destination(operation: OperationModel) -> None:
    """Action khác, hoặc navigation_start thiếu đích dùng được, không có maps_url."""
    assert "maps_url" not in _command_data(operation)
    assert "maps_url" not in _command_data(_nav_operation(operation, {}))
    assert "maps_url" not in _command_data(_nav_operation(operation, {"destination": "x"}))
    assert "maps_url" not in _command_data(
        _nav_operation(operation, {"destination": {"address": "  "}})
    )
