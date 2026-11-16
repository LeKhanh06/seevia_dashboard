"""Usage-statistics persistence: three layers, each rebuildable from the one below.

Layer 1 ``usage_events``: raw operational events from VH (and AB's own call/SOS transitions),
append-only, deduplicated by ``event_id``. Layer 2 (``usage_attempts``, ``usage_tasks``,
``usage_sessions``, ``usage_calls``, ``usage_sos``): one row per attempt/task/session/call merged
from layer 1 regardless of arrival order. Layer 3 (``usage_daily``, ``usage_hourly``,
``usage_active_days``): per report-day aggregates recomputed from layer 2 by the rollup worker for
the days listed in ``usage_dirty_days``.

No raw ``device_id`` / ``public_user_id`` is stored here: ``owner_hash`` and ``device_hash`` are
``HMAC-SHA256(USAGE_ID_KEY, kind:id)`` pseudonyms (``app/services/usage_ids.py``). No content
(speech, OCR text, names, numbers, destinations, songs) is ever stored.
"""

from datetime import date, datetime
from uuid import UUID

from sqlalchemy import (
    CHAR,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Index,
    Integer,
    SmallInteger,
    String,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.database import Base

SESSION_KINDS = ("connection", "alert_mode", "ocr_live", "music", "navigation")


def _in(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{value}'" for value in values)


class UsageEvent(Base):
    """Layer 1: one raw, validated usage event."""

    __tablename__ = "usage_events"
    __table_args__ = (Index("ix_usage_events_occurred_at", "occurred_at"),)

    event_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    type: Mapped[str] = mapped_column(String(40), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    owner_hash: Mapped[str | None] = mapped_column(CHAR(64), nullable=True)
    device_hash: Mapped[str | None] = mapped_column(CHAR(64), nullable=True)
    component_version: Mapped[str | None] = mapped_column(String(200), nullable=True)
    body: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)


class UsageAttempt(Base):
    """Layer 2: one attempt (lượt) — one press of the button that invoked a feature."""

    __tablename__ = "usage_attempts"
    __table_args__ = (
        Index("ix_usage_attempts_started_at", "started_at"),
        Index("ix_usage_attempts_owner_started_at", "owner_hash", "started_at"),
        Index("ix_usage_attempts_task_id", "task_id"),
        Index(
            "ix_usage_attempts_open",
            "started_at",
            postgresql_where=text("finished_at IS NULL"),
        ),
        Index("uq_usage_attempts_request_id", "request_id", unique=True),
    )

    attempt_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    task_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    feature: Mapped[str | None] = mapped_column(String(32), nullable=True)
    owner_hash: Mapped[str | None] = mapped_column(CHAR(64), nullable=True)
    device_hash: Mapped[str | None] = mapped_column(CHAR(64), nullable=True)
    component_version: Mapped[str | None] = mapped_column(String(200), nullable=True)
    firmware_version: Mapped[str | None] = mapped_column(String(40), nullable=True)
    entry: Mapped[str | None] = mapped_column(String(20), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    first_audio_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str | None] = mapped_column(String(24), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    playback: Mapped[str | None] = mapped_column(String(12), nullable=True)
    request_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    processing_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    align_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # signal name (optionally "name:reason_code") -> count, accumulated once per new layer-1 event.
    signals: Mapped[dict[str, int]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )


class UsageTask(Base):
    """Layer 2: one task (tác vụ) — consecutive attempts merged by VH."""

    __tablename__ = "usage_tasks"
    __table_args__ = (
        Index("ix_usage_tasks_started_at", "started_at"),
        Index("ix_usage_tasks_open", "started_at", postgresql_where=text("finished_at IS NULL")),
    )

    task_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    feature: Mapped[str | None] = mapped_column(String(32), nullable=True)
    owner_hash: Mapped[str | None] = mapped_column(CHAR(64), nullable=True)
    device_hash: Mapped[str | None] = mapped_column(CHAR(64), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str | None] = mapped_column(String(24), nullable=True)
    attempts: Mapped[int | None] = mapped_column(Integer, nullable=True)


class UsageSession(Base):
    """Layer 2: a continuous session (connection, live mode, music, navigation)."""

    __tablename__ = "usage_sessions"
    __table_args__ = (
        CheckConstraint(f"kind IN ({_in(SESSION_KINDS)})", name="ck_usage_sessions_kind"),
        Index("ix_usage_sessions_started_at", "started_at"),
        Index("ix_usage_sessions_device_kind", "device_hash", "kind", "started_at"),
    )

    session_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    owner_hash: Mapped[str | None] = mapped_column(CHAR(64), nullable=True)
    device_hash: Mapped[str | None] = mapped_column(CHAR(64), nullable=True)
    firmware_version: Mapped[str | None] = mapped_column(String(40), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    end_reason: Mapped[str | None] = mapped_column(String(24), nullable=True)
    align_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # level -> count of alerts actually spoken during this session.
    alerts_by_level: Mapped[dict[str, int]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    # Firmware-only diagnostics from `hello` (uptime_ms, reset_reason, wifi_drops, ...), when sent.
    device_info: Mapped[dict[str, object]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )


class UsageCall(Base):
    """Layer 2: one Internet call, written by AB from ``calls`` state transitions."""

    __tablename__ = "usage_calls"
    __table_args__ = (Index("ix_usage_calls_started_at", "started_at"),)

    call_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    owner_hash: Mapped[str | None] = mapped_column(CHAR(64), nullable=True)
    device_hash: Mapped[str | None] = mapped_column(CHAR(64), nullable=True)
    direction: Mapped[str] = mapped_column(String(16), nullable=False)
    emergency: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    answered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    end_reason: Mapped[str | None] = mapped_column(String(32), nullable=True)
    final_state: Mapped[str | None] = mapped_column(String(16), nullable=True)


class UsageSos(Base):
    """Layer 2: one SOS activation, written by AB from ``emergencies`` state transitions."""

    __tablename__ = "usage_sos"
    __table_args__ = (Index("ix_usage_sos_started_at", "started_at"),)

    sos_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    owner_hash: Mapped[str | None] = mapped_column(CHAR(64), nullable=True)
    device_hash: Mapped[str | None] = mapped_column(CHAR(64), nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    answered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str | None] = mapped_column(String(16), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)


class UsageDirtyDay(Base):
    """Report days whose layer-3 rows must be recomputed."""

    __tablename__ = "usage_dirty_days"

    day: Mapped[date] = mapped_column(Date, primary_key=True)
    marked_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class UsageDaily(Base):
    """Layer 3: additive metrics per report day and group key ('' = none/unknown)."""

    __tablename__ = "usage_daily"

    day: Mapped[date] = mapped_column(Date, primary_key=True)
    owner_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    device_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    feature: Mapped[str] = mapped_column(String(32), primary_key=True)
    component_version: Mapped[str] = mapped_column(String(200), primary_key=True)
    firmware_version: Mapped[str] = mapped_column(String(40), primary_key=True)
    metrics: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)


class UsageHourly(Base):
    """Layer 3: counts per report day, hour of day and feature (peak hours, heatmap)."""

    __tablename__ = "usage_hourly"

    day: Mapped[date] = mapped_column(Date, primary_key=True)
    hour: Mapped[int] = mapped_column(SmallInteger, primary_key=True)
    feature: Mapped[str] = mapped_column(String(32), primary_key=True)
    uses: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class UsageActiveDay(Base):
    """Layer 3: (day, owner, device) with at least one real use — DAU/WAU/MAU, active days."""

    __tablename__ = "usage_active_days"

    day: Mapped[date] = mapped_column(Date, primary_key=True)
    owner_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    device_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
