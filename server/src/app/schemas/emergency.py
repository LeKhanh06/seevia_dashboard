"""Emergency API schemas (glasses server only)."""

from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import EmergencyState
from app.schemas.call import CallRelativeData
from app.schemas.common import TrimmedNonEmptyStr


class EmergencyStartRequest(BaseModel):
    """Glasses server: start calling the owner's emergency relatives."""

    model_config = ConfigDict(extra="forbid")

    glasses_device_id: TrimmedNonEmptyStr
    # Tuỳ chọn: chủ kính do AB suy từ glasses_device_id; có gửi mà khác chủ thật thì từ chối.
    user_id: TrimmedNonEmptyStr | None = None
    request_id: UUID
    # Token media một lần cho từng lượt gọi (3 vòng x số người), VH sinh sẵn để AB không gọi ngược.
    # Hết token trước khi hết vòng thì chuỗi kết thúc sớm bằng emergency_exhausted.
    media_tokens: list[Annotated[str, Field(min_length=8, max_length=256)]] = Field(
        min_length=1, max_length=90
    )


class EmergencyStartData(BaseModel):
    """Result of ``emergency/start`` (a replay returns the same chain)."""

    model_config = ConfigDict(extra="forbid")

    emergency_id: UUID
    state: EmergencyState
    call_id: UUID | None
    relative: CallRelativeData | None
    contacts: int


class EmergencyStateData(BaseModel):
    """State of a chain after ``emergency/{id}/stop``."""

    model_config = ConfigDict(extra="forbid")

    emergency_id: UUID
    state: EmergencyState
    # Chỉ có khi `state == "answered"` (người thân vừa nghe máy đúng lúc VH bấm dừng): cuộc gọi đó vẫn `active`,
    # VH phải gửi `call/{call_id}/hangup` nếu đã đóng đầu kính. Các trạng thái khác: null.
    call_id: UUID | None = None
