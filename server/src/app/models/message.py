"""Message persistence model: owner <-> relative text messages relayed through the glasses.

``owner_user_id`` / ``relative_user_id`` hold ``users.public_user_id`` (same id space as
``care_links`` and ``calls``). A ``to_glasses`` row doubles as the outbox entry for the
``message_received`` callback to the glasses server (``callback_*`` columns), so the
event is written in the same transaction as the message.
"""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import CheckConstraint, DateTime, Enum, ForeignKey, Index, Integer, String, Text
from sqlalchemy import text as sql_text
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.database import Base
from app.models.enums import (
    MessageCallbackState,
    MessageDirection,
    enum_check_values,
    enum_values,
)


class Message(Base):
    """One text message between a glasses' owner and a relative."""

    __tablename__ = "messages"
    __table_args__ = (
        CheckConstraint(
            f"direction IN ({enum_check_values(MessageDirection)})", name="ck_messages_direction"
        ),
        CheckConstraint(
            "callback_state IS NULL OR callback_state IN "
            f"({enum_check_values(MessageCallbackState)})",
            name="ck_messages_callback_state",
        ),
        CheckConstraint("callback_attempts >= 0", name="ck_messages_callback_attempts"),
        CheckConstraint("char_length(text) BETWEEN 1 AND 1000", name="ck_messages_text_length"),
        Index("ix_messages_pair_created_at", "owner_user_id", "relative_user_id", "created_at"),
        Index(
            "ix_messages_callback_state_next_attempt_at",
            "callback_state",
            "callback_next_attempt_at",
        ),
        # request_id do bên gửi (kính hoặc app) sinh: gửi lại cùng id thì trả bản cũ.
        Index("uq_messages_request_id", "request_id", unique=True),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    owner_user_id: Mapped[str] = mapped_column(
        String, ForeignKey("users.public_user_id", ondelete="CASCADE"), nullable=False
    )
    relative_user_id: Mapped[str] = mapped_column(
        String, ForeignKey("users.public_user_id", ondelete="CASCADE"), nullable=False
    )
    direction: Mapped[MessageDirection] = mapped_column(
        Enum(
            MessageDirection,
            native_enum=False,
            create_constraint=False,
            values_callable=enum_values,
            length=16,
        ),
        nullable=False,
    )
    text: Mapped[str] = mapped_column(Text, nullable=False)
    # Tên chủ kính gọi người thân ("Mẹ"), cố định lúc gửi: kính đọc "{tên} nhắn: ...".
    peer_display_name: Mapped[str] = mapped_column(String, nullable=False)
    # Kính nhận tin (chiều to_glasses); NULL ở chiều to_relative.
    glasses_device_id: Mapped[str | None] = mapped_column(String, nullable=True)
    request_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    # Kính đã đọc/đã báo cho chủ kính (chỉ chiều to_glasses).
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    # Outbox giao ``message_received`` cho VH; NULL = không cần giao (chiều to_relative,
    # hoặc AB chưa cấu hình CALLBACK_URL).
    callback_state: Mapped[MessageCallbackState | None] = mapped_column(
        Enum(
            MessageCallbackState,
            native_enum=False,
            create_constraint=False,
            values_callable=enum_values,
            length=16,
        ),
        nullable=True,
    )
    callback_attempts: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=sql_text("0")
    )
    callback_next_attempt_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    callback_locked_until: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    callback_delivered_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
