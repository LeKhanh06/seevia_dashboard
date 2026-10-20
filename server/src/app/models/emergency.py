"""Emergency chain persistence: one row per "gọi người thân khẩn cấp" started by the glasses.

``owner_user_id`` holds ``users.public_user_id``. The chain is advanced by ``EmergencyWorker``:
it rings the owner's emergency relatives one by one (``round``/``pos`` point at the next one),
at most ``EMERGENCY_MAX_ROUNDS`` rounds, using the media tokens the glasses server sent upfront.
"""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.database import Base
from app.models.enums import EmergencyState, enum_check_values, enum_values


class Emergency(Base):
    """One emergency chain: text everyone, then ring the relatives in priority order."""

    __tablename__ = "emergencies"
    __table_args__ = (
        CheckConstraint(
            f"state IN ({enum_check_values(EmergencyState)})", name="ck_emergencies_state"
        ),
        CheckConstraint('"round" >= 0 AND pos >= 0', name="ck_emergencies_cursor"),
        Index("uq_emergencies_request_id", "request_id", unique=True),
        Index("ix_emergencies_state", "state"),
        Index("ix_emergencies_glasses_device_id", "glasses_device_id"),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    owner_user_id: Mapped[str] = mapped_column(
        String, ForeignKey("users.public_user_id", ondelete="CASCADE"), nullable=False
    )
    glasses_device_id: Mapped[str] = mapped_column(String, nullable=False)
    request_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    state: Mapped[EmergencyState] = mapped_column(
        Enum(
            EmergencyState,
            native_enum=False,
            create_constraint=False,
            values_callable=enum_values,
            length=16,
        ),
        nullable=False,
        default=EmergencyState.RUNNING,
        server_default=text("'running'"),
    )
    # Token media một lần cho từng lượt gọi (VH sinh sẵn); xoá sạch khi chuỗi kết thúc.
    media_tokens: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )
    tokens_used: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    # Con trỏ chuỗi: vòng hiện tại và vị trí người kế tiếp trong danh sách ưu tiên.
    round: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    pos: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    current_call_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    messages_sent: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    messages_locked_until: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
