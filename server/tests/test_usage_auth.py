"""Usage-stats writer credential tests.

The glasses server (VH) posts usage event batches with its own bearer token, separate from the
Public API token: the service token must not write statistics and the statistics token must not
execute service actions. Raw tokens are generated at run time.
"""

import hashlib
import hmac
import secrets

import pytest
from fastapi import Depends, FastAPI, status
from httpx import ASGITransport, AsyncClient
from pydantic import ValidationError

from app.auth import require_public_scope, require_usage_writer
from app.config import AppEnvironment, DeliveryTransport, PublicApiScope, Settings

_PUBLIC_API_DOMAIN = b"app-demo-auth/v1/public-api"
_USAGE_API_DOMAIN = b"app-demo-auth/v1/usage-api"


def _digest_hex(raw_token: str, domain: bytes) -> str:
    return hmac.new(raw_token.encode("utf-8"), domain, hashlib.sha256).hexdigest()


def _settings(**extra: object) -> Settings:
    return Settings.model_validate(
        {
            "app_env": AppEnvironment.TEST,
            "http_port": 8000,
            "database_url": "postgresql+asyncpg://test:test@localhost:5432/app_test",
            "public_api_token_hash": extra.pop("public_api_token_hash", "1" * 64),
            "public_api_client_id": "usage-auth-test",
            "public_api_scopes": frozenset({PublicApiScope.SERVICE_EXECUTE}),
            "device_api_token_hash": "2" * 64,
            "field_encryption_key": "test-only-field-key",
            "delivery_transport": DeliveryTransport.FAKE,
            **extra,
        }
    )


def _app(settings: Settings) -> FastAPI:
    app = FastAPI()
    app.state.settings = settings

    @app.post("/probe/usage", dependencies=[Depends(require_usage_writer)])
    async def probe_usage() -> dict[str, bool]:
        return {"ok": True}

    @app.post(
        "/probe/service",
        dependencies=[Depends(require_public_scope(PublicApiScope.SERVICE_EXECUTE))],
    )
    async def probe_service() -> dict[str, bool]:
        return {"ok": True}

    return app


async def _post(app: FastAPI, path: str, token: str | None) -> int:
    headers = {"Authorization": f"Bearer {token}"} if token is not None else {}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
        return (await client.post(path, headers=headers)).status_code


async def test_usage_writer_accepts_usage_token() -> None:
    """A token digested with the usage domain is accepted."""
    raw = secrets.token_urlsafe(32)
    app = _app(_settings(usage_api_token_hash=_digest_hex(raw, _USAGE_API_DOMAIN)))
    assert await _post(app, "/probe/usage", raw) == status.HTTP_200_OK


async def test_usage_writer_rejects_public_token_and_missing_header() -> None:
    """The Public API token and a missing header are both 401."""
    public_raw = secrets.token_urlsafe(32)
    usage_raw = secrets.token_urlsafe(32)
    app = _app(
        _settings(
            public_api_token_hash=_digest_hex(public_raw, _PUBLIC_API_DOMAIN),
            usage_api_token_hash=_digest_hex(usage_raw, _USAGE_API_DOMAIN),
        )
    )
    assert await _post(app, "/probe/usage", public_raw) == status.HTTP_401_UNAUTHORIZED
    assert await _post(app, "/probe/usage", None) == status.HTTP_401_UNAUTHORIZED


async def test_public_scope_rejects_usage_token() -> None:
    """The usage token cannot execute service actions."""
    usage_raw = secrets.token_urlsafe(32)
    app = _app(_settings(usage_api_token_hash=_digest_hex(usage_raw, _USAGE_API_DOMAIN)))
    assert await _post(app, "/probe/service", usage_raw) == status.HTTP_401_UNAUTHORIZED


async def test_unconfigured_usage_hash_returns_503() -> None:
    """Without USAGE_API_TOKEN_HASH the writer endpoint is 503, not 500."""
    app = _app(_settings())
    assert await _post(app, "/probe/usage", "anything") == status.HTTP_503_SERVICE_UNAVAILABLE


def test_settings_without_usage_keys_still_loads() -> None:
    """Existing deployments without the new keys still start."""
    settings = _settings()
    assert settings.usage_api_token_hash is None
    assert settings.usage_id_key is None


def test_usage_hash_and_key_are_validated() -> None:
    """Malformed hash or short key fail settings validation."""
    with pytest.raises(ValidationError):
        _settings(usage_api_token_hash="not-hex")
    with pytest.raises(ValidationError):
        _settings(usage_id_key="ab" * 8)  # 8 bytes < 32
    assert _settings(usage_id_key="ab" * 32).usage_id_key is not None
