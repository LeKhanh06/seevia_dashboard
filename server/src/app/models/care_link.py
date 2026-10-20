"""Owner-to-relative care link persistence model.

``owner_user_id`` / ``relative_user_id`` hold the human-facing
``users.public_user_id`` -- the same identifier space as
``glasses_devices.user_id`` -- so the glasses server can resolve "gọi mẹ"
for the glasses' owner without a second lookup.
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
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func, text

from app.database import Base
from app.models.enums import CareLinkStatus, enum_check_values, enum_values


class CareLink(Base):
    """A glasses owner's relative, invited by the owner and accepted by the relative."""

    __tablename__ = "care_links"
    __table_args__ = (
        UniqueConstraint("owner_user_id", "relative_user_id", name="uq_care_links_pair"),
        CheckConstraint(
            f"status IN ({enum_check_values(CareLinkStatus)})",
            name="ck_care_links_status",
        ),
        CheckConstraint("owner_user_id <> relative_user_id", name="ck_care_links_not_self"),
        Index("ix_care_links_owner_user_id", "owner_user_id"),
        Index("ix_care_links_relative_user_id", "relative_user_id"),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    owner_user_id: Mapped[str] = mapped_column(
        String, ForeignKey("users.public_user_id", ondelete="CASCADE"), nullable=False
    )
    relative_user_id: Mapped[str] = mapped_column(
        String, ForeignKey("users.public_user_id", ondelete="CASCADE"), nullable=False
    )
    # Tên chủ kính gọi người thân ("Mẹ", "Anh Nam"); cũng được khớp như một alias.
    display_name: Mapped[str] = mapped_column(String, nullable=False)
    aliases: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )
    emergency: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    # Số nhỏ gọi trước trong chuỗi khẩn cấp.
    priority: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    status: Mapped[CareLinkStatus] = mapped_column(
        Enum(
            CareLinkStatus,
            native_enum=False,
            create_constraint=False,
            values_callable=enum_values,
            length=16,
        ),
        nullable=False,
        default=CareLinkStatus.PENDING,
        server_default=text("'pending'"),
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
