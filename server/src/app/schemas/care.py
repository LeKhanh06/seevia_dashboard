"""Care-link API schemas: invite, accept, list, update, revoke."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import CareLinkStatus
from app.schemas.auth import NormalizedPhone
from app.schemas.common import TrimmedNonEmptyStr

AliasList = list[TrimmedNonEmptyStr]


class CareInviteRequest(BaseModel):
    """Owner invites an existing account (by phone number) as a relative."""

    model_config = ConfigDict(extra="forbid")

    phone_number: NormalizedPhone
    display_name: TrimmedNonEmptyStr
    aliases: AliasList = Field(default_factory=list, max_length=10)
    emergency: bool = False
    priority: int = Field(default=0, ge=0, le=100)


class CareUpdateRequest(BaseModel):
    """Owner edits a link; omitted fields are left unchanged."""

    model_config = ConfigDict(extra="forbid")

    aliases: AliasList | None = Field(default=None, max_length=10)
    emergency: bool | None = None
    priority: int | None = Field(default=None, ge=0, le=100)


class CareLinkData(BaseModel):
    """One care link as seen by the caller."""

    model_config = ConfigDict(extra="forbid")

    id: UUID
    role: Literal["owner", "relative"]
    status: CareLinkStatus
    display_name: str
    aliases: list[str]
    emergency: bool
    priority: int
    owner_user_id: str
    relative_user_id: str
    # Tên/số của phía bên kia, để app hiển thị lời mời và danh sách.
    peer_display_name: str | None
    peer_phone_number: str
    created_at: datetime


class CareLinksData(BaseModel):
    """Every non-revoked link the caller takes part in, either role."""

    model_config = ConfigDict(extra="forbid")

    links: list[CareLinkData]
