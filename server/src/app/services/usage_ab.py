"""Usage statistics that only AB knows: Internet calls and SOS chains, from their own state changes.

Called by ``CallService`` / ``EmergencyService`` inside the transaction that changes the state, so
the statistics row commits or rolls back with the state itself. Every transition writes one
layer-1 event whose id is ``uuid5(namespace, "<id>:<state>")``: replaying the same transition
(retry, duplicate request, both sides reporting) hits the same id and changes nothing.

Nothing identifying is stored: owner and glasses are HMAC pseudonyms, and no relative name, phone
number or media token goes into the event body. Statistics switched off (no ``USAGE_ID_KEY``)
makes every method a no-op.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid5
from zoneinfo import ZoneInfo

from app.actions import Action
from app.models.enums import CallState, EmergencyState
from app.repositories.glasses_device import GlassesDeviceRepository
from app.repositories.usage import UsageRepository, days_between
from app.services.usage_ids import pseudonym

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from app.config import Settings
    from app.models.call import Call
    from app.models.emergency import Emergency
    from app.models.operation import Operation

# Fixed namespace for AB-originated event ids (never change: it is what makes replays duplicates).
USAGE_EVENT_NAMESPACE = UUID("8a0f6f0e-3b1d-5c47-9e2a-5d6c1b7e4f90")
COMPONENT_VERSION = "ab"
_CALL_FINAL = frozenset({CallState.ENDED, CallState.MISSED, CallState.REJECTED, CallState.FAILED})
# SOS chain end state -> (attempt-style status, error code).
_SOS_RESULT: dict[EmergencyState, tuple[str, str | None]] = {
    EmergencyState.ANSWERED: ("success", None),
    EmergencyState.EXHAUSTED: ("failed", "NO_ANSWER"),
    EmergencyState.STOPPED: ("cancelled", None),
}

# SIM fallback SOS (``emergency/call`` operation) end state -> (status, default error code).
_SIM_SOS_RESULT: dict[str, tuple[str, str | None]] = {
    "succeeded": ("success", None),
    "failed": ("failed", "PHONE_FAILED"),
    "timed_out": ("failed", "RESULT_TIMEOUT"),
}
_CODE = re.compile(r"^[A-Za-z0-9_.:-]{1,64}$")


def event_id(entity_id: UUID, state: str) -> UUID:
    """Deterministic layer-1 id of one state transition."""
    return uuid5(USAGE_EVENT_NAMESPACE, f"{entity_id}:{state}")


def _loaded(obj: object, name: str) -> Any:  # noqa: ANN401 -- ORM attribute of any type
    """Attribute value if already in memory; never triggers an (async-unsafe) lazy load.

    Server-default columns (``created_at``) are not loaded after the INSERT flush; the caller falls
    back to "now", and later transitions (which load the real value) correct it via ``LEAST``.
    """
    return obj.__dict__.get(name)


class AbUsageRecorder:
    """Write AB-side usage rows in the caller's transaction (never commits)."""

    def __init__(self, session: AsyncSession, settings: Settings) -> None:
        """Bind to the caller-owned session; no key configured = statistics off."""
        key = settings.usage_id_key
        self._key = bytes.fromhex(key.get_secret_value()) if key is not None else None
        self._session = session
        self._repo = UsageRepository(session)
        self._tz = ZoneInfo(settings.usage_report_tz)

    async def call(self, call: Call) -> None:
        """Record the current state of an Internet call (ringing, active or final)."""
        if self._key is None:
            return
        now = datetime.now(UTC)
        state = call.state
        started = _loaded(call, "created_at") or now
        answered = _loaded(call, "answered_at")
        final = state in _CALL_FINAL
        ended = _loaded(call, "ended_at") if final else None
        owner = pseudonym(self._key, "owner", call.owner_user_id)
        device = pseudonym(self._key, "device", call.glasses_device_id)
        is_new = await self._repo.insert_event(
            {
                "event_id": event_id(call.id, state.value),
                "type": "call_state",
                "occurred_at": now,
                "received_at": now,
                "owner_hash": owner,
                "device_hash": device,
                "component_version": COMPONENT_VERSION,
                "body": {
                    "call_id": str(call.id),
                    "state": state.value,
                    "direction": call.direction.value,
                    "emergency": call.emergency,
                    "end_reason": call.end_reason if final else None,
                },
            }
        )
        if not is_new:
            return
        values: dict[str, Any] = {
            "call_id": call.id,
            "owner_hash": owner,
            "device_hash": device,
            "direction": call.direction.value,
            "emergency": call.emergency,
            "started_at": started,
        }
        if answered is not None:
            values["answered_at"] = answered
        if final:
            values |= {
                "final_state": state.value,
                "ended_at": ended or now,
                "end_reason": call.end_reason,
            }
        stored = await self._repo.upsert_call(values) or started
        await self._repo.mark_dirty(
            set(days_between(stored, ended or answered or stored, self._tz))
        )

    async def sos(self, emergency: Emergency, *, answered_at: datetime | None = None) -> None:
        """Record an SOS chain: started (``running``) or ended (answered, exhausted, stopped)."""
        if self._key is None:
            return
        now = datetime.now(UTC)
        state = emergency.state
        started = _loaded(emergency, "created_at") or now
        result = _SOS_RESULT.get(state)
        owner = pseudonym(self._key, "owner", emergency.owner_user_id)
        device = pseudonym(self._key, "device", emergency.glasses_device_id)
        is_new = await self._repo.insert_event(
            {
                "event_id": event_id(emergency.id, state.value),
                "type": "sos_state",
                "occurred_at": now,
                "received_at": now,
                "owner_hash": owner,
                "device_hash": device,
                "component_version": COMPONENT_VERSION,
                "body": {"sos_id": str(emergency.id), "state": state.value},
            }
        )
        if not is_new:
            return
        values: dict[str, Any] = {
            "sos_id": emergency.id,
            "owner_hash": owner,
            "device_hash": device,
            "started_at": started,
        }
        if answered_at is not None:
            values["answered_at"] = answered_at
        if result is not None:
            status, code = result
            values |= {"status": status, "finished_at": now, "error_code": code}
        stored = await self._repo.upsert_sos(values) or started
        await self._repo.mark_dirty(set(days_between(stored, now if result else stored, self._tz)))

    async def sim_sos(
        self,
        operation: Operation,
        *,
        state: str | None = None,
        error_code: str | None = None,
        glasses_device_id: str | None = None,
    ) -> None:
        """Record an SOS sent the old SIM way (``emergency/call``: SMS + call from owner's phone).

        Same ``usage_sos`` row as the Internet chain (keyed by the operation ``request_id``), so the
        dashboard counts every SOS once whichever path it took. ``state`` overrides the row's state
        when the caller just changed it in SQL (timeout, delivery failure) without reloading.
        Other actions are ignored.
        """
        if self._key is None or operation.action is not Action.EMERGENCY_CALL:
            return
        now = datetime.now(UTC)
        state = state or operation.request_state.value
        started = _loaded(operation, "created_at") or now
        result = _SIM_SOS_RESULT.get(state)
        glasses = glasses_device_id or await GlassesDeviceRepository(
            self._session
        ).get_active_device_id(operation.user_id)
        owner = pseudonym(self._key, "owner", operation.user_id)
        device = pseudonym(self._key, "device", glasses) if glasses else None
        is_new = await self._repo.insert_event(
            {
                "event_id": event_id(operation.request_id, f"sim:{state}"),
                "type": "sos_state",
                "occurred_at": now,
                "received_at": now,
                "owner_hash": owner,
                "device_hash": device,
                "component_version": COMPONENT_VERSION,
                "body": {"sos_id": str(operation.request_id), "state": state, "channel": "sim"},
            }
        )
        if not is_new:
            return
        values: dict[str, Any] = {
            "sos_id": operation.request_id,
            "owner_hash": owner,
            "device_hash": device,
            "started_at": started,
        }
        if result is not None:
            status, default_code = result
            code = error_code if error_code and _CODE.match(error_code) else default_code
            if status == "success":
                code = None
            values |= {"status": status, "finished_at": now, "error_code": code}
        stored = await self._repo.upsert_sos(values) or started
        await self._repo.mark_dirty(set(days_between(stored, now if result else stored, self._tz)))
