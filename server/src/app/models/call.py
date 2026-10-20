"""Call persistence models: the call itself and its outbox of events for the glasses server.

``owner_user_id`` / ``relative_user_id`` hold ``users.public_user_id`` (same id
space as ``care_links`` and ``glasses_devices.user_id``).
"""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    CHAR,
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.database import Base
from app.models.enums import (
    CallCallbackState,
    CallDirection,
    CallState,
    enum_check_values,
    enum_values,
)


class Call(Base):
    """One call between a glasses' owner and a relative."""

    __tablename__ = "calls"
    __table_args__ = (
        CheckConstraint(
            f"direction IN ({enum_check_values(CallDirection)})", name="ck_calls_direction"
        ),
        CheckConstraint(f"state IN ({enum_check_values(CallState)})", name="ck_calls_state"),
        Index("ix_calls_owner_user_id", "owner_user_id"),
        Index("ix_calls_relative_user_id", "relative_user_id"),
        Index("ix_calls_state_ring_deadline_at", "state", "ring_deadline_at"),
        # request_id do kính gửi để start idempotent; cuộc gọi do người thân khởi tạo thì NULL.
        Index("uq_calls_request_id", "request_id", unique=True),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    owner_user_id: Mapped[str] = mapped_column(
        String, ForeignKey("users.public_user_id", ondelete="CASCADE"), nullable=False
    )
    relative_user_id: Mapped[str] = mapped_column(
        String, ForeignKey("users.public_user_id", ondelete="CASCADE"), nullable=False
    )
    glasses_device_id: Mapped[str] = mapped_column(String, nullable=False)
    direction: Mapped[CallDirection] = mapped_column(
        Enum(
            CallDirection,
            native_enum=False,
            create_constraint=False,
            values_callable=enum_values,
            length=16,
        ),
        nullable=False,
    )
    emergency: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    state: Mapped[CallState] = mapped_column(
        Enum(
            CallState,
            native_enum=False,
            create_constraint=False,
            values_callable=enum_values,
            length=16,
        ),
        nullable=False,
        default=CallState.RINGING,
        server_default=text("'ringing'"),
    )
    end_reason: Mapped[str | None] = mapped_column(String, nullable=True)
    # Tên chủ kính gọi người thân ("Mẹ"): kính đọc "Mẹ đang gọi" / "Mẹ chưa nghe máy".
    peer_display_name: Mapped[str] = mapped_column(String, nullable=False)
    request_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    # Token media do VH sinh. Luôn lưu hash; bản gốc (media_token_pending) chỉ giữ tới khi
    # giao cho người thân lần đầu (answer / GET) rồi xoá, và cũng xoá khi cuộc gọi kết thúc.
    media_token_hash: Mapped[str | None] = mapped_column(CHAR(64), nullable=True)
    media_token_pending: Mapped[str | None] = mapped_column(Text, nullable=True)
    ring_deadline_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    answered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class CallCallback(Base):
    """Outbox row: one call event to POST to the glasses server (at-least-once, per-call order)."""

    __tablename__ = "call_callbacks"
    __table_args__ = (
        CheckConstraint(
            f"state IN ({enum_check_values(CallCallbackState)})", name="ck_call_callbacks_state"
        ),
        CheckConstraint("attempts >= 0", name="ck_call_callbacks_attempts"),
        Index("ix_call_callbacks_state_next_attempt_at", "state", "next_attempt_at"),
        Index("ix_call_callbacks_call_id", "call_id"),
    )

    # Khoá tăng dần: vừa là Idempotency-Key, vừa là thứ tự giao trong cùng một cuộc gọi.
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    call_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("calls.id", ondelete="CASCADE"), nullable=False
    )
    payload: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    state: Mapped[CallCallbackState] = mapped_column(
        Enum(
            CallCallbackState,
            native_enum=False,
            create_constraint=False,
            values_callable=enum_values,
            length=16,
        ),
        nullable=False,
        default=CallCallbackState.PENDING,
        server_default=text("'pending'"),
    )
    attempts: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    next_attempt_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
