"""Plain row types exchanged between the usage repository and the aggregation service."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from datetime import datetime

GroupKey = tuple[str, str, str, str, str]  # owner_hash, device_hash, feature, component, firmware

# Attempt statuses whose error_code counts as an error (cancel reasons like "superseded" do not).
ERROR_STATUSES: frozenset[str] = frozenset({"failed", "technical_error"})


@dataclass(frozen=True, slots=True)
class AttemptRow:
    """Layer-2 attempt columns used by the aggregation."""

    task_id: str | None
    feature: str | None
    owner_hash: str | None
    device_hash: str | None
    component_version: str | None
    firmware_version: str | None
    started_at: datetime | None
    first_audio_at: datetime | None
    finished_at: datetime | None
    status: str | None
    error_code: str | None
    processing_ms: int | None
    align_ms: int | None
    signals: dict[str, int] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class TaskRow:
    """Layer-2 task columns (component/firmware of its first attempt)."""

    feature: str | None
    owner_hash: str | None
    device_hash: str | None
    component_version: str | None
    firmware_version: str | None
    started_at: datetime | None
    status: str | None
    attempts: int | None
    task_id: str | None = None


@dataclass(frozen=True, slots=True)
class SessionRow:
    """Layer-2 session columns."""

    kind: str
    owner_hash: str | None
    device_hash: str | None
    firmware_version: str | None
    started_at: datetime | None
    last_seen_at: datetime | None
    ended_at: datetime | None
    end_reason: str | None
    align_ms: int | None


@dataclass(frozen=True, slots=True)
class AlertRow:
    """One spoken alert (from layer 1, which keeps the per-alert time)."""

    owner_hash: str | None
    device_hash: str | None
    occurred_at: datetime
    level: int


@dataclass(frozen=True, slots=True)
class CallRow:
    """Layer-2 Internet call (emergency calls are excluded upstream)."""

    owner_hash: str | None
    device_hash: str | None
    started_at: datetime
    answered_at: datetime | None
    ended_at: datetime | None
    final_state: str | None
    end_reason: str | None
    direction: str = "to_relative"


@dataclass(frozen=True, slots=True)
class SosRow:
    """Layer-2 SOS activation."""

    owner_hash: str | None
    device_hash: str | None
    started_at: datetime
    answered_at: datetime | None
    finished_at: datetime | None
    status: str | None
    error_code: str | None


@dataclass(slots=True)
class Layer2Rows:
    """Every layer-2 row touching one report day."""

    attempts: list[AttemptRow] = field(default_factory=list)
    tasks: list[TaskRow] = field(default_factory=list)
    sessions: list[SessionRow] = field(default_factory=list)
    alerts: list[AlertRow] = field(default_factory=list)
    calls: list[CallRow] = field(default_factory=list)
    sos: list[SosRow] = field(default_factory=list)


@dataclass(slots=True)
class DayResult:
    """Layer-3 content of one day."""

    daily: dict[GroupKey, dict[str, Any]]
    hourly: dict[tuple[int, str], tuple[int, int]]  # (hour, feature) -> (uses, attempts)
    active: set[tuple[str, str]]  # (owner_hash, device_hash) with a real use
