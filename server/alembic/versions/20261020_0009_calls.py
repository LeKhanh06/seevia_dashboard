"""Add calls and call_callbacks (glasses <-> relative calls, outbox of events to the glasses server).

Revision ID: 20261020_0009
Revises: 20261019_0008
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "20261020_0009"
down_revision: str | None = "20261019_0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create calls and call_callbacks."""
    op.create_table(
        "calls",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("owner_user_id", sa.String(), nullable=False),
        sa.Column("relative_user_id", sa.String(), nullable=False),
        sa.Column("glasses_device_id", sa.String(), nullable=False),
        sa.Column("direction", sa.String(length=16), nullable=False),
        sa.Column("emergency", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column(
            "state", sa.String(length=16), nullable=False, server_default=sa.text("'ringing'")
        ),
        sa.Column("end_reason", sa.String(), nullable=True),
        sa.Column("peer_display_name", sa.String(), nullable=False),
        sa.Column("request_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("media_token_hash", sa.CHAR(length=64), nullable=True),
        sa.Column("media_token_pending", sa.Text(), nullable=True),
        sa.Column("ring_deadline_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("answered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.PrimaryKeyConstraint("id", name="pk_calls"),
        sa.ForeignKeyConstraint(
            ["owner_user_id"],
            ["users.public_user_id"],
            ondelete="CASCADE",
            name="fk_calls_owner_user_id_users",
        ),
        sa.ForeignKeyConstraint(
            ["relative_user_id"],
            ["users.public_user_id"],
            ondelete="CASCADE",
            name="fk_calls_relative_user_id_users",
        ),
        sa.CheckConstraint("direction IN ('to_relative', 'to_glasses')", name="ck_calls_direction"),
        sa.CheckConstraint(
            "state IN ('ringing', 'active', 'ended', 'missed', 'rejected', 'failed')",
            name="ck_calls_state",
        ),
    )
    op.create_index("ix_calls_owner_user_id", "calls", ["owner_user_id"])
    op.create_index("ix_calls_relative_user_id", "calls", ["relative_user_id"])
    op.create_index("ix_calls_state_ring_deadline_at", "calls", ["state", "ring_deadline_at"])
    op.create_index("uq_calls_request_id", "calls", ["request_id"], unique=True)

    op.create_table(
        "call_callbacks",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("call_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column(
            "state", sa.String(length=16), nullable=False, server_default=sa.text("'pending'")
        ),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column(
            "next_attempt_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("locked_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_call_callbacks"),
        sa.ForeignKeyConstraint(
            ["call_id"], ["calls.id"], ondelete="CASCADE", name="fk_call_callbacks_call_id_calls"
        ),
        sa.CheckConstraint(
            "state IN ('pending', 'delivered', 'dead_letter')", name="ck_call_callbacks_state"
        ),
        sa.CheckConstraint("attempts >= 0", name="ck_call_callbacks_attempts"),
    )
    op.create_index(
        "ix_call_callbacks_state_next_attempt_at", "call_callbacks", ["state", "next_attempt_at"]
    )
    op.create_index("ix_call_callbacks_call_id", "call_callbacks", ["call_id"])


def downgrade() -> None:
    """Drop call_callbacks and calls."""
    op.drop_index("ix_call_callbacks_call_id", table_name="call_callbacks")
    op.drop_index("ix_call_callbacks_state_next_attempt_at", table_name="call_callbacks")
    op.drop_table("call_callbacks")
    op.drop_index("uq_calls_request_id", table_name="calls")
    op.drop_index("ix_calls_state_ring_deadline_at", table_name="calls")
    op.drop_index("ix_calls_relative_user_id", table_name="calls")
    op.drop_index("ix_calls_owner_user_id", table_name="calls")
    op.drop_table("calls")
