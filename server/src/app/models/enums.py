"""Database logical enum values persisted as checked varchar columns."""

from enum import StrEnum

from app.actions import Action, Operation


class DevicePlatform(StrEnum):
    """Supported device platforms."""

    ANDROID = "android"


class DeviceStatus(StrEnum):
    """Device enrollment and delivery states."""

    ACTIVE = "active"
    INACTIVE = "inactive"
    REVOKED = "revoked"


class GlassesLinkStatus(StrEnum):
    """Glasses-to-user pairing states."""

    ACTIVE = "active"
    INACTIVE = "inactive"


class CareLinkStatus(StrEnum):
    """Owner-to-relative care link states."""

    PENDING = "pending"
    ACTIVE = "active"
    REVOKED = "revoked"


class CallDirection(StrEnum):
    """Who rings whom: the glasses' owner calls a relative, or the reverse."""

    TO_RELATIVE = "to_relative"
    TO_GLASSES = "to_glasses"


class CallState(StrEnum):
    """Call lifecycle: ``ringing`` -> ``active``/``rejected``/``missed``/``ended``."""

    RINGING = "ringing"
    ACTIVE = "active"
    ENDED = "ended"
    MISSED = "missed"
    REJECTED = "rejected"
    FAILED = "failed"


class CallCallbackState(StrEnum):
    """Delivery state of one call event queued for the glasses server."""

    PENDING = "pending"
    DELIVERED = "delivered"
    DEAD_LETTER = "dead_letter"


class MessageDirection(StrEnum):
    """Who wrote the message: the glasses' owner (to a relative) or a relative (to the glasses)."""

    TO_RELATIVE = "to_relative"
    TO_GLASSES = "to_glasses"


class MessageCallbackState(StrEnum):
    """Delivery state of the ``message_received`` event owed to the glasses server."""

    PENDING = "pending"
    DELIVERED = "delivered"
    DEAD_LETTER = "dead_letter"


class RequestState(StrEnum):
    """Public operation states persisted in PostgreSQL."""

    PROCESSING = "processing"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    TIMED_OUT = "timed_out"


class DeliveryState(StrEnum):
    """Internal command delivery states."""

    RECEIVED = "received"
    SENDING = "sending"
    RETRY = "retry"
    SENT = "sent"
    FAILED = "failed"
    REPORT_RECEIVED = "report_received"
    REPORT_TIMEOUT = "report_timeout"


class CallbackState(StrEnum):
    """Terminal-result callback delivery states."""

    NOT_REQUIRED = "not_required"
    PENDING = "pending"
    SENDING = "sending"
    RETRY = "retry"
    DELIVERED = "delivered"
    DEAD_LETTER = "dead_letter"


def enum_values[EnumT: StrEnum](enum_type: type[EnumT]) -> list[str]:
    """Return values so SQLAlchemy persists StrEnum values instead of names."""
    return [member.value for member in enum_type]


def enum_check_values[EnumT: StrEnum](enum_type: type[EnumT]) -> str:
    """Render deterministic SQL literals for a named check constraint."""
    return ", ".join(f"'{member.value}'" for member in enum_type)


__all__ = [
    "Action",
    "CallCallbackState",
    "CallDirection",
    "CallState",
    "CallbackState",
    "CareLinkStatus",
    "DeliveryState",
    "DevicePlatform",
    "DeviceStatus",
    "GlassesLinkStatus",
    "MessageCallbackState",
    "MessageDirection",
    "Operation",
    "RequestState",
]
