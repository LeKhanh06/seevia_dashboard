"""Call API schemas: glasses-server endpoints and phone-app endpoints."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import CallDirection, CallState
from app.schemas.common import TrimmedNonEmptyStr


class CallStartRequest(BaseModel):
    """Glasses server: the owner asks to call a relative by spoken name."""

    model_config = ConfigDict(extra="forbid")

    glasses_device_id: TrimmedNonEmptyStr
    # Tuỳ chọn: chủ kính do AB suy từ glasses_device_id; có gửi mà khác chủ thật thì từ chối.
    user_id: TrimmedNonEmptyStr | None = None
    contact_alias: TrimmedNonEmptyStr
    # Token media một lần do VH sinh; AB chỉ lưu hash và chuyển cho người thân khi nghe máy.
    media_token: str = Field(min_length=8, max_length=256)
    request_id: UUID
    emergency: bool = False


class CallHangupRequest(BaseModel):
    """Glasses server ends (or declines, or cancels) a call."""

    model_config = ConfigDict(extra="forbid")

    reason: TrimmedNonEmptyStr | None = Field(default=None, max_length=64)


class CallServiceAnswerRequest(BaseModel):
    """Glasses server: the glasses picked up an incoming call."""

    model_config = ConfigDict(extra="forbid")

    media_token: str = Field(min_length=8, max_length=256)


class CallRelativeData(BaseModel):
    """The relative being called, as the owner names them."""

    model_config = ConfigDict(extra="forbid")

    display_name: str


class CallStartData(BaseModel):
    """Result of ``call/start``."""

    model_config = ConfigDict(extra="forbid")

    call_id: UUID
    relative: CallRelativeData


class CallStateData(BaseModel):
    """State of a call after a glasses-server transition."""

    model_config = ConfigDict(extra="forbid")

    call_id: UUID
    state: CallState
    end_reason: str | None


class CallCreateRequest(BaseModel):
    """Phone app: a relative calls the glasses of ``owner_user_id``."""

    model_config = ConfigDict(extra="forbid")

    owner_user_id: TrimmedNonEmptyStr


class CallCreatedData(BaseModel):
    """Result of ``POST /calls``."""

    model_config = ConfigDict(extra="forbid")

    call_id: UUID


class CallAnswerData(BaseModel):
    """Result of answering: where to open the audio WS and the one-time token."""

    model_config = ConfigDict(extra="forbid")

    call_id: UUID
    media_url: str
    # Chỉ có ở lần nghe máy đầu tiên; gọi lại (đã active) trả null.
    media_token: str | None


class CallData(BaseModel):
    """A call as seen by the relative (``GET``, ``reject``, ``hangup``)."""

    model_config = ConfigDict(extra="forbid")

    call_id: UUID
    direction: CallDirection
    state: CallState
    end_reason: str | None
    emergency: bool
    peer_display_name: str
    media_url: str | None
    # Cuộc gọi tới kính: token chỉ giao một lần qua GET đầu tiên sau khi kính nghe (hoặc FCM).
    media_token: str | None
    created_at: datetime
    answered_at: datetime | None
    ended_at: datetime | None
