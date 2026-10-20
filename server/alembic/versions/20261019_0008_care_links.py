"""Add care_links (owner-to-relative links for glasses calls and messages).

Revision ID: 20261019_0008
Revises: 20260822_0007
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "20261019_0008"
down_revision: str | None = "20260822_0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create care_links keyed by users.public_user_id."""
    op.create_table(
        "care_links",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("owner_user_id", sa.String(), nullable=False),
        sa.Column("relative_user_id", sa.String(), nullable=False),
        sa.Column("display_name", sa.String(), nullable=False),
        sa.Column(
            "aliases",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("emergency", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("priority", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column(
            "status", sa.String(length=16), nullable=False, server_default=sa.text("'pending'")
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.PrimaryKeyConstraint("id", name="pk_care_links"),
        sa.ForeignKeyConstraint(
            ["owner_user_id"],
            ["users.public_user_id"],
            ondelete="CASCADE",
            name="fk_care_links_owner_user_id_users",
        ),
        sa.ForeignKeyConstraint(
            ["relative_user_id"],
            ["users.public_user_id"],
            ondelete="CASCADE",
            name="fk_care_links_relative_user_id_users",
        ),
        sa.UniqueConstraint("owner_user_id", "relative_user_id", name="uq_care_links_pair"),
        sa.CheckConstraint(
            "status IN ('pending', 'active', 'revoked')", name="ck_care_links_status"
        ),
        sa.CheckConstraint("owner_user_id <> relative_user_id", name="ck_care_links_not_self"),
    )
    op.create_index("ix_care_links_owner_user_id", "care_links", ["owner_user_id"])
    op.create_index("ix_care_links_relative_user_id", "care_links", ["relative_user_id"])


def downgrade() -> None:
    """Drop care_links."""
    op.drop_index("ix_care_links_relative_user_id", table_name="care_links")
    op.drop_index("ix_care_links_owner_user_id", table_name="care_links")
    op.drop_table("care_links")
