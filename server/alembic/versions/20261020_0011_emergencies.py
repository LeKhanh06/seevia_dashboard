"""Add emergencies (chuỗi gọi người thân khẩn cấp).

Revision ID: 20261020_0011
Revises: 20261020_0010
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "20261020_0011"
down_revision: str | None = "20261020_0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create emergencies."""
    op.create_table(
        "emergencies",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("owner_user_id", sa.String(), nullable=False),
        sa.Column("glasses_device_id", sa.String(), nullable=False),
        sa.Column("request_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "state", sa.String(length=16), nullable=False, server_default=sa.text("'running'")
        ),
        sa.Column(
            "media_tokens",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("tokens_used", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("round", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("pos", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("current_call_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("messages_sent", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("messages_locked_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.PrimaryKeyConstraint("id", name="pk_emergencies"),
        sa.ForeignKeyConstraint(
            ["owner_user_id"],
            ["users.public_user_id"],
            ondelete="CASCADE",
            name="fk_emergencies_owner_user_id_users",
        ),
        sa.CheckConstraint(
            "state IN ('running', 'answered', 'exhausted', 'stopped')",
            name="ck_emergencies_state",
        ),
        sa.CheckConstraint('"round" >= 0 AND pos >= 0', name="ck_emergencies_cursor"),
    )
    op.create_index("uq_emergencies_request_id", "emergencies", ["request_id"], unique=True)
    op.create_index("ix_emergencies_state", "emergencies", ["state"])
    op.create_index("ix_emergencies_glasses_device_id", "emergencies", ["glasses_device_id"])


def downgrade() -> None:
    """Drop emergencies."""
    op.drop_table("emergencies")
