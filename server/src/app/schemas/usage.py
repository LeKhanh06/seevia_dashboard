"""Usage-statistics event batch contract (VH -> AB ``POST /api/v1/usage/events``).

Only operational fields are accepted: every event model forbids extra keys, strings are short and
pattern-checked, and there is no free-text field, so speech, OCR text, money amounts, names, phone
numbers, message bodies, destinations or song titles cannot be stored even by mistake. Events are
validated one by one so one bad event is reported in ``rejected`` instead of failing the batch.
"""

from __future__ import annotations

from typing import Annotated, Literal
from uuid import UUID  # noqa: TC003

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, JsonValue

MAX_BATCH_EVENTS = 500
# Trần byte thân một lô (VH ~0,3-1 KB/sự kiện, tự cắt lô dưới 1 MiB): chặn trước khi parse.
MAX_BATCH_BYTES = 2 * 1024 * 1024

Feature = Literal[
    "ocr",
    "money",
    "find",
    "space",
    "ask",
    "hazard",
    "chat",
    "call",
    "message_send",
    "message_read",
    "emergency_call",
    "music",
    "navigation",
    "ride",
]
AttemptStatus = Literal[
    "success",
    "completed_early",
    "failed",
    "cancelled",
    "abandoned",
    "technical_error",
    "uncertain",
    "rejected_image",
    "unconfirmed",
]
Signal = Literal[
    "image_rejected",
    "reprompt",
    "needs_web",
    "needs_web_decided",
    "cancel_window_open",
    "cancelled_in_window",
    "money_uncertain",
    "money_decided",
    "interrupted",
]
SessionKind = Literal["connection", "alert_mode", "ocr_live", "music", "navigation"]
EndReason = Literal[
    "closed", "replaced", "error", "shutdown", "user_off", "done", "cancelled", "timeout"
]

Code = Annotated[str, Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_.:-]+$")]
Version = Annotated[str, Field(min_length=1, max_length=40, pattern=r"^[A-Za-z0-9_.+-]+$")]
ComponentVersion = Annotated[
    str, Field(min_length=1, max_length=200, pattern=r"^[A-Za-z0-9_.+=,:-]+$")
]
DeviceId = Annotated[str, Field(min_length=1, max_length=64)]
Millis = Annotated[int, Field(ge=0, le=86_400_000)]


class _EventBase(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    event_id: UUID
    occurred_at: AwareDatetime
    device_id: DeviceId
    component_version: ComponentVersion | None = None
    firmware_version: Version | None = None


class AttemptStarted(_EventBase):
    """A feature was invoked (one button press reached a final intent)."""

    type: Literal["attempt_started"]
    attempt_id: UUID
    task_id: UUID
    feature: Feature
    entry: Literal["v2_session", "process", "v2_turn"] = "v2_session"


class AttemptSignal(_EventBase):
    """A countable signal inside one attempt (image rejected, re-prompt, web lookup, ...)."""

    type: Literal["attempt_signal"]
    attempt_id: UUID
    signal: Signal
    reason_code: Code | None = None


class AttemptMarks(_EventBase):
    """Timing marks of one attempt."""

    type: Literal["attempt_marks"]
    attempt_id: UUID
    first_audio_at: AwareDatetime | None = None
    processing_ms: Millis | None = None
    align_ms: Millis | None = None


class AttemptFinished(_EventBase):
    """Final outcome of one attempt; the first one received wins."""

    type: Literal["attempt_finished"]
    attempt_id: UUID
    task_id: UUID
    status: AttemptStatus
    error_code: Code | None = None
    playback: Literal["confirmed", "unconfirmed"] | None = None
    request_id: UUID | None = None


class TaskFinished(_EventBase):
    """Final outcome of one task; the first one received wins."""

    type: Literal["task_finished"]
    task_id: UUID
    feature: Feature
    status: AttemptStatus
    attempts: Annotated[int, Field(ge=1, le=1000)]


class DeviceInfo(BaseModel):
    """Optional firmware diagnostics forwarded from the glasses' ``hello`` frame."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    uptime_ms: Annotated[int, Field(ge=0)] | None = None
    reset_reason: Code | None = None
    wifi_drops: Annotated[int, Field(ge=0, le=1_000_000)] | None = None
    last_wifi_reason: Annotated[int, Field(ge=0, le=1000)] | None = None
    rssi: Annotated[int, Field(ge=-127, le=0)] | None = None


class SessionStarted(_EventBase):
    """A connection / live-mode / music / navigation session began."""

    type: Literal["session_started"]
    session_id: UUID
    kind: SessionKind
    device_info: DeviceInfo | None = None


class SessionHeartbeat(_EventBase):
    """The session is still alive at ``occurred_at``."""

    type: Literal["session_heartbeat"]
    session_id: UUID
    kind: SessionKind


class SessionEnded(_EventBase):
    """The session ended."""

    type: Literal["session_ended"]
    session_id: UUID
    kind: SessionKind
    end_reason: EndReason
    align_ms: Millis | None = None


class AlertRaised(_EventBase):
    """An obstacle warning was actually spoken during an ``alert_mode`` session."""

    type: Literal["alert_raised"]
    session_id: UUID
    level: Annotated[int, Field(ge=0, le=3)]


UsageEventIn = (
    AttemptStarted
    | AttemptSignal
    | AttemptMarks
    | AttemptFinished
    | TaskFinished
    | SessionStarted
    | SessionHeartbeat
    | SessionEnded
    | AlertRaised
)

EVENT_MODELS: dict[str, type[_EventBase]] = {
    "attempt_started": AttemptStarted,
    "attempt_signal": AttemptSignal,
    "attempt_marks": AttemptMarks,
    "attempt_finished": AttemptFinished,
    "task_finished": TaskFinished,
    "session_started": SessionStarted,
    "session_heartbeat": SessionHeartbeat,
    "session_ended": SessionEnded,
    "alert_raised": AlertRaised,
}


class UsageBatchIn(BaseModel):
    """Batch envelope; events are validated individually by the ingest service."""

    model_config = ConfigDict(extra="forbid")

    events: Annotated[list[dict[str, JsonValue]], Field(min_length=1, max_length=MAX_BATCH_EVENTS)]


class RejectedEvent(BaseModel):
    """One event the server refused, with a stable code (never the offending value)."""

    event_id: str | None
    code: Literal["INVALID_EVENT", "UNKNOWN_TYPE", "CONFLICT"]


class UsageBatchOut(BaseModel):
    """Ingest result: counts plus the refused events."""

    accepted: int
    duplicates: int
    rejected: list[RejectedEvent]
