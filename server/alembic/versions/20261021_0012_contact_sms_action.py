"""Allow contact_sms in operation/action constraints.

Revision ID: 20261021_0012
Revises: 20261020_0011
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20261021_0012"
down_revision: str | None = "20261020_0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

OPERATION_VALUES = (
    "ride_quote",
    "ride_confirm",
    "music_play",
    "music_stop",
    "music_volume",
    "navigation_start",
    "navigation_stop",
    "emergency_call",
    "contact_call",
    "location_get",
    "capabilities_get",
    "call_answer",
    "call_reject",
    "contact_sms",
)


def _in_values(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{value}'" for value in values)


def _recreate(values: tuple[str, ...]) -> None:
    op.drop_constraint("ck_operations_operation", "operations", type_="check")
    op.drop_constraint("ck_operations_action", "operations", type_="check")
    op.create_check_constraint(
        "ck_operations_operation", "operations", f"operation IN ({_in_values(values)})"
    )
    op.create_check_constraint(
        "ck_operations_action", "operations", f"action IN ({_in_values(values)})"
    )


def upgrade() -> None:
    """Widen operation/action constraints for the SIM text action."""
    _recreate(OPERATION_VALUES)


def downgrade() -> None:
    """Remove contact_sms after callers stop creating it."""
    _recreate(OPERATION_VALUES[:-1])
