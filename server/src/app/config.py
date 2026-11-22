"""Validated application configuration loaded from environment variables."""

import re
from enum import StrEnum
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Self

from fastapi import Request
from pydantic import Field, HttpUrl, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class AppEnvironment(StrEnum):
    """Supported deployment environments."""

    LOCAL = "local"
    TEST = "test"
    DEMO = "demo"


class DeliveryTransport(StrEnum):
    """Supported command delivery transports."""

    FAKE = "fake"
    FCM = "fcm"
    WEBHOOK = "webhook"


class LogLevel(StrEnum):
    """Supported application log levels."""

    DEBUG = "debug"
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


class PublicApiScope(StrEnum):
    """Public API bearer scopes.

    ``service:execute`` for POSTs, ``requests:read`` for status, ``calls:manage`` for the
    glasses server's call endpoints (``/api/v1/service/call/{start,{id}/hangup,{id}/answer}``).
    """

    SERVICE_EXECUTE = "service:execute"
    REQUESTS_READ = "requests:read"
    CALLS_MANAGE = "calls:manage"


# Hex encoding of a 32-byte HMAC-SHA256 digest (see app/auth.py).
_TOKEN_HASH_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_USAGE_ID_KEY_PATTERN = re.compile(r"^(?:[0-9a-f]{2}){32,}$")


def _validate_token_hash_format(value: SecretStr, *, field_name: str) -> SecretStr:
    """Reject a token hash that is not exactly 64 lowercase hex characters."""
    if not _TOKEN_HASH_PATTERN.fullmatch(value.get_secret_value()):
        message = f"{field_name} must be exactly 64 lowercase hexadecimal characters"
        raise ValueError(message)
    return value


def _parse_public_api_scopes(value: object) -> frozenset[str]:
    """Parse a comma-separated string or iterable into a non-empty set of known scopes."""
    if isinstance(value, str):
        items = [item.strip() for item in value.split(",")]
    elif isinstance(value, list | tuple | set | frozenset):
        items = [str(item).strip() for item in value]
    else:
        message = "PUBLIC_API_SCOPES must be a comma-separated string or an iterable of strings"
        raise TypeError(message)

    if not items or any(not item for item in items):
        message = "PUBLIC_API_SCOPES must not be empty and must not contain empty items"
        raise ValueError(message)
    if len(items) != len(set(items)):
        message = "PUBLIC_API_SCOPES must not contain duplicate scopes"
        raise ValueError(message)
    known_scopes = {scope.value for scope in PublicApiScope}
    unknown_scopes = sorted(set(items) - known_scopes)
    if unknown_scopes:
        message = f"PUBLIC_API_SCOPES contains unknown scopes: {unknown_scopes}"
        raise ValueError(message)
    return frozenset(items)


class Settings(BaseSettings):
    """Runtime configuration for the backend process."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_ignore_empty=True,
        extra="ignore",
        case_sensitive=False,
    )

    app_env: AppEnvironment
    http_port: int = Field(ge=1, le=65_535)
    database_url: str
    public_api_token_hash: SecretStr
    public_api_client_id: str
    # NoDecode: pydantic-settings JSON-decodes complex-typed env values by
    # default (e.g. it would require PUBLIC_API_SCOPES to be a JSON array
    # string). NoDecode passes the raw comma-separated env string straight
    # to the "before" validator below instead.
    public_api_scopes: Annotated[frozenset[str], NoDecode]
    device_api_token_hash: SecretStr
    field_encryption_key: SecretStr
    delivery_transport: DeliveryTransport

    # Thống kê sử dụng: VH gửi lô sự kiện bằng token riêng (scope cố định usage:write), khác token
    # Public API. `usage_id_key` (hex >= 32 byte) là khoá HMAC đổi device_id/public_user_id thành mã
    # giả trong bảng thống kê. Chưa đặt -> endpoint thống kê trả 503, phần khác chạy bình thường.
    usage_api_token_hash: SecretStr | None = None
    usage_id_key: SecretStr | None = None
    # Múi giờ chốt ngày báo cáo của bảng tổng hợp (tầng 3); hỏi múi giờ khác thì tính từ tầng 2.
    usage_report_tz: str = "Asia/Ho_Chi_Minh"
    usage_rollup_interval_s: float = Field(default=5.0, gt=0)
    usage_rollup_lease_s: float = Field(default=60.0, gt=0)
    # Lượt/tác vụ VH không bao giờ chốt (VH chết) quá hạn này -> technical_error / UNCLOSED.
    usage_unclosed_after_s: float = Field(default=1800.0, gt=0)
    # Tiền token LLM (GET /admin/stats/llm), tính lúc đọc từ usage_llm_daily.
    # `llm_prices_path`: file JSON bảng giá [{model, from, to, input, output, cached}], USD/1M;
    # rỗng = bảng mặc định trong app/services/llm_cost.py.
    # `llm_free_providers`: nhánh không trả tiền thật (quota UIT, gói 9router).
    # `llm_compare_models`: model quy đổi "nếu chạy bằng model này"; model đầu = số chính
    # `what_if_model` (3.5 Flash-Lite = nhánh Gemini đã chọn ở VH). `llm_usd_vnd`: tỷ giá hiển thị.
    llm_prices_path: str | None = None
    llm_free_providers: list[str] = Field(default_factory=lambda: ["uit", "9router"])
    llm_compare_models: list[str] = Field(
        default_factory=lambda: ["gemini-3.5-flash-lite", "gemini-3.8-flash"]
    )
    llm_usd_vnd: float | None = Field(default=None, gt=0)
    # Dashboard admin (seevia_dashboard): phiên đăng nhập email + mật khẩu, hết hạn sau ttl giây.
    # `admin_cors_origins`: origin của trang dashboard tĩnh (vd. ["http://127.0.0.1:5500"]);
    # rỗng = không bật CORS (dashboard cùng origin hoặc qua proxy).
    admin_session_ttl_s: int = Field(default=3600, gt=0, le=86400)
    admin_cors_origins: list[str] = Field(default_factory=list)

    fcm_project_id: str | None = None
    # Voice server v2: transport webhook cho E2E với điện thoại giả lập.
    delivery_webhook_url: HttpUrl | None = None
    google_application_credentials: Path | None = None

    spotify_client_id: str | None = None
    spotify_client_secret: SecretStr | None = None

    worker_poll_seconds: float = Field(default=0.5, gt=0)
    worker_batch_size: int = Field(default=20, ge=1)
    delivery_lease_seconds: int = Field(default=30, ge=1)
    callback_lease_seconds: int = Field(default=30, ge=1)

    callback_url: HttpUrl | None = None
    callback_token: SecretStr | None = None
    callback_allowed_hosts: str | None = None

    # Gọi điện qua kính: chuông tối đa bao lâu, và URL WS cầu âm thanh của VH (`WS /v2/call`)
    # app nhận cùng media_token khi nghe máy.
    call_ring_timeout_s: float = Field(default=30.0, gt=0)
    call_media_url: str | None = None
    # Cuộc gọi `active` quá lâu (VH sập, mất hangup) bị đóng để không mồ côi vô hạn.
    call_max_active_s: float = Field(default=7200.0, gt=0)
    # Khẩn cấp: trần chờ vị trí từ điện thoại người khiếm thị (quá hạn thì nhắn không kèm vị trí)
    # và số vòng gọi tối đa qua danh sách người thân khẩn cấp.
    emergency_location_timeout_s: float = Field(default=5.0, gt=0)
    emergency_max_rounds: int = Field(default=3, ge=1)

    demo_time_scale: float = Field(default=1.0, gt=0)
    log_level: LogLevel = LogLevel.INFO

    @field_validator("database_url")
    @classmethod
    def require_async_postgresql_url(cls, value: str) -> str:
        """Reject database URLs that cannot use SQLAlchemy's asyncpg driver."""
        if not value.startswith("postgresql+asyncpg://"):
            message = "DATABASE_URL must use the postgresql+asyncpg scheme"
            raise ValueError(message)
        return value

    @field_validator("public_api_token_hash")
    @classmethod
    def validate_public_api_token_hash_format(cls, value: SecretStr) -> SecretStr:
        """Enforce the 64-hex-character digest format at startup, before database/worker."""
        return _validate_token_hash_format(value, field_name="PUBLIC_API_TOKEN_HASH")

    @field_validator("device_api_token_hash")
    @classmethod
    def validate_device_api_token_hash_format(cls, value: SecretStr) -> SecretStr:
        """Enforce the 64-hex-character digest format at startup, before database/worker."""
        return _validate_token_hash_format(value, field_name="DEVICE_API_TOKEN_HASH")

    @field_validator("usage_api_token_hash")
    @classmethod
    def validate_usage_api_token_hash_format(cls, value: SecretStr | None) -> SecretStr | None:
        """Delegate optional usage token hash validation to the shared helper."""
        if value is None:
            return None
        return _validate_token_hash_format(value, field_name="USAGE_API_TOKEN_HASH")

    @field_validator("usage_id_key")
    @classmethod
    def validate_usage_id_key(cls, value: SecretStr | None) -> SecretStr | None:
        """Require at least 32 bytes of hex key material for usage pseudonyms."""
        if value is None:
            return None
        raw = value.get_secret_value()
        if not _USAGE_ID_KEY_PATTERN.fullmatch(raw):
            message = "USAGE_ID_KEY must be >= 64 lowercase hexadecimal characters (32 bytes)"
            raise ValueError(message)
        return value

    @field_validator("public_api_client_id")
    @classmethod
    def require_non_empty_public_api_client_id(cls, value: str) -> str:
        """Require ``PUBLIC_API_CLIENT_ID`` to be a non-empty configured identifier."""
        trimmed = value.strip()
        if not trimmed:
            message = "PUBLIC_API_CLIENT_ID must be a non-empty identifier"
            raise ValueError(message)
        return trimmed

    @field_validator("public_api_scopes", mode="before")
    @classmethod
    def parse_public_api_scopes(cls, value: object) -> frozenset[str]:
        """Parse and validate ``PUBLIC_API_SCOPES`` before pydantic's set coercion."""
        return _parse_public_api_scopes(value)

    @model_validator(mode="after")
    def validate_conditional_configuration(self) -> Self:
        """Require complete provider and callback settings when those modes are enabled."""
        if self.delivery_transport is DeliveryTransport.FCM and not self.fcm_project_id:
            message = "FCM_PROJECT_ID is required when DELIVERY_TRANSPORT=fcm"
            raise ValueError(message)

        if self.delivery_transport is DeliveryTransport.WEBHOOK and not self.delivery_webhook_url:
            message = "DELIVERY_WEBHOOK_URL is required when DELIVERY_TRANSPORT=webhook"
            raise ValueError(message)

        callback_values = (
            self.callback_url,
            self.callback_token,
            self.callback_allowed_hosts,
        )
        if any(value is not None for value in callback_values) and not all(
            value is not None for value in callback_values
        ):
            message = (
                "CALLBACK_URL, CALLBACK_TOKEN, and CALLBACK_ALLOWED_HOSTS "
                "must be configured together"
            )
            raise ValueError(message)

        return self


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Load and cache validated process configuration."""
    return Settings()


def get_app_settings(request: Request) -> Settings:
    """Return validated application settings attached to application state."""
    settings: Settings | None = getattr(request.app.state, "settings", None)
    if settings is None:
        return get_settings()
    return settings
