"""Message API schemas: glasses-server endpoints and phone-app endpoints."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import MessageDirection
from app.schemas.common import TrimmedNonEmptyStr

MESSAGE_MAX_CHARS = 1000


class MessageSendRequest(BaseModel):
    """Glasses server: the owner dictated a message for a relative by spoken name."""

    model_config = ConfigDict(extra="forbid")

    # Chủ kính do AB suy từ kính đã xác thực (glasses_device_id); user_id chỉ để đối chiếu.
    glasses_device_id: TrimmedNonEmptyStr
    user_id: TrimmedNonEmptyStr | None = None
    contact_alias: TrimmedNonEmptyStr
    text: TrimmedNonEmptyStr = Field(max_length=MESSAGE_MAX_CHARS)
    request_id: UUID


class MessageRelativeData(BaseModel):
    """The relative texted, as the owner names them."""

    model_config = ConfigDict(extra="forbid")

    display_name: str


class MessageSendData(BaseModel):
    """Result of ``messages/send``."""

    model_config = ConfigDict(extra="forbid")

    message_id: UUID
    relative: MessageRelativeData
    created_at: datetime


class UnreadMessageData(BaseModel):
    """A relative's message the glasses have not read out yet."""

    model_config = ConfigDict(extra="forbid")

    message_id: UUID
    from_display_name: str
    text: str
    created_at: datetime


class UnreadMessagesData(BaseModel):
    """Result of ``messages/unread`` (oldest first)."""

    model_config = ConfigDict(extra="forbid")

    messages: list[UnreadMessageData]


class MessageReadRequest(BaseModel):
    """Body of ``messages/{id}/read``: the glasses that read it out must belong to its owner."""

    model_config = ConfigDict(extra="forbid")

    glasses_device_id: TrimmedNonEmptyStr
    user_id: TrimmedNonEmptyStr | None = None


class MessageReadData(BaseModel):
    """Result of ``messages/{id}/read``."""

    model_config = ConfigDict(extra="forbid")

    message_id: UUID
    read_at: datetime | None


class MessageCreateRequest(BaseModel):
    """Phone app: a relative texts the glasses of ``owner_user_id``."""

    model_config = ConfigDict(extra="forbid")

    owner_user_id: TrimmedNonEmptyStr
    text: TrimmedNonEmptyStr = Field(max_length=MESSAGE_MAX_CHARS)
    request_id: UUID


class MessageCreatedData(BaseModel):
    """Result of ``POST /messages``."""

    model_config = ConfigDict(extra="forbid")

    message_id: UUID
    created_at: datetime


class MessageItem(BaseModel):
    """One message of a conversation, from the caller's point of view."""

    model_config = ConfigDict(extra="forbid")

    id: UUID
    direction: MessageDirection
    from_me: bool
    text: str
    created_at: datetime
    # Chỉ có nghĩa với tin gửi tới kính: lúc kính đọc cho chủ kính nghe; còn lại null.
    read_at: datetime | None


class MessagesData(BaseModel):
    """Result of ``GET /messages`` (newest first)."""

    model_config = ConfigDict(extra="forbid")

    messages: list[MessageItem]
