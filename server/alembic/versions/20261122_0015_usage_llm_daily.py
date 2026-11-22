"""LLM token totals per day (usage_llm_daily), rebuilt from layer-1 ``llm_call`` events.

Revision ID: 20261122_0015
Revises: 20261116_0014
Create Date: 2026-11-22 11:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20261122_0015"
down_revision: str | None = "20261116_0014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Apply the migration."""
    op.create_table(
        "usage_llm_daily",
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("feature", sa.String(length=32), nullable=False),
        sa.Column("provider", sa.String(length=64), nullable=False),
        sa.Column("model", sa.String(length=80), nullable=False),
        sa.Column("purpose", sa.String(length=16), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("calls", sa.Integer(), nullable=False),
        sa.Column("estimated_calls", sa.Integer(), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("in_tokens", sa.BigInteger(), nullable=False),
        sa.Column("out_tokens", sa.BigInteger(), nullable=False),
        sa.Column("reasoning_tokens", sa.BigInteger(), nullable=False),
        sa.Column("cached_tokens", sa.BigInteger(), nullable=False),
        sa.Column("images", sa.Integer(), nullable=False),
        sa.Column("latency_ms_sum", sa.BigInteger(), nullable=False),
        sa.PrimaryKeyConstraint(
            "day",
            "feature",
            "provider",
            "model",
            "purpose",
            "status",
            name=op.f("pk_usage_llm_daily"),
        ),
    )
    # Rollup reads one day of llm_call events; the partial index keeps that off the other types.
    op.create_index(
        "ix_usage_events_llm_call_at",
        "usage_events",
        ["occurred_at"],
        postgresql_where=sa.text("type = 'llm_call'"),
    )


def downgrade() -> None:
    """Revert the migration."""
    op.drop_index("ix_usage_events_llm_call_at", table_name="usage_events")
    op.drop_table("usage_llm_daily")
