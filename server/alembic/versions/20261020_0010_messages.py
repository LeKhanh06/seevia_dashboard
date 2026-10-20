"""Add messages (owner <-> relative texts, outbox of message_received for the glasses server).

Revision ID: 20261020_0010
Revises: 20261020_0009
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "20261020_0010"
down_revision: str | None = "20261020_0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create messages."""
    op.create_table(
        "messages",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("owner_user_id", sa.String(), nullable=False),
        sa.Column("relative_user_id", sa.String(), nullable=False),
        sa.Column("direction", sa.String(length=16), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("peer_display_name", sa.String(), nullable=False),
        sa.Column("glasses_device_id", sa.String(), nullable=True),
        sa.Column("request_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("read_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("callback_state", sa.String(length=16), nullable=True),
        sa.Column("callback_attempts", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column(
            "callback_next_attempt_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("callback_locked_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("callback_delivered_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_messages"),
        sa.ForeignKeyConstraint(
            ["owner_user_id"],
            ["users.public_user_id"],
            ondelete="CASCADE",
            name="fk_messages_owner_user_id_users",
        ),
        sa.ForeignKeyConstraint(
            ["relative_user_id"],
            ["users.public_user_id"],
            ondelete="CASCADE",
            name="fk_messages_relative_user_id_users",
        ),
        sa.CheckConstraint(
            "direction IN ('to_relative', 'to_glasses')", name="ck_messages_direction"
        ),
        sa.CheckConstraint(
            "callback_state IS NULL OR callback_state IN ('pending', 'delivered', 'dead_letter')",
            name="ck_messages_callback_state",
        ),
        sa.CheckConstraint("callback_attempts >= 0", name="ck_messages_callback_attempts"),
        sa.CheckConstraint("char_length(text) BETWEEN 1 AND 1000", name="ck_messages_text_length"),
    )
    op.create_index(
        "ix_messages_pair_created_at",
        "messages",
        ["owner_user_id", "relative_user_id", "created_at"],
    )
    op.create_index(
        "ix_messages_callback_state_next_attempt_at",
        "messages",
        ["callback_state", "callback_next_attempt_at"],
    )
    op.create_index("uq_messages_request_id", "messages", ["request_id"], unique=True)


def downgrade() -> None:
    """Drop messages."""
    op.drop_index("uq_messages_request_id", table_name="messages")
    op.drop_index("ix_messages_callback_state_next_attempt_at", table_name="messages")
    op.drop_index("ix_messages_pair_created_at", table_name="messages")
    op.drop_table("messages")
