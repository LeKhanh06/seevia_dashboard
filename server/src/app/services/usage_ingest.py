"""Ingest one usage event batch: validate, deduplicate, merge into layer 2, queue dirty days.

Fast path: one multi-row layer-1 insert, then the layer-2 upserts of the new events, all inside ONE
savepoint (~1 statement per event instead of ~4). A constraint conflict (e.g. two attempts claiming
one ``request_id``) rolls that savepoint back and the batch is replayed event by event, each in its
own savepoint, so the conflict rejects that event only. Layer 2 is touched only when the layer-1
insert is new, so resending a batch is a no-op beyond the ``duplicates`` count.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from typing import TYPE_CHECKING, Any
from zoneinfo import ZoneInfo

from pydantic import ValidationError
from sqlalchemy.exc import IntegrityError

from app.repositories.usage import UsageRepository, days_between, report_day
from app.schemas.usage import (
    EVENT_MODELS,
    AlertRaised,
    AttemptFinished,
    AttemptMarks,
    AttemptSignal,
    AttemptStarted,
    RejectedEvent,
    SessionEnded,
    SessionHeartbeat,
    SessionStarted,
    TaskFinished,
    UsageBatchIn,
    UsageBatchOut,
    UsageEventIn,
)
from app.services.usage_ids import pseudonym

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


_UUID_TEXT_LEN = 36


def _uuid_text(value: object) -> str | None:
    """``value`` khi là UUID hợp lệ (dạng chuẩn), ngược lại None."""
    if not isinstance(value, str) or len(value) > _UUID_TEXT_LEN:
        return None
    try:
        return str(uuid.UUID(value))
    except ValueError:
        return None


class UsageIngestService:
    """Write path of the usage statistics."""

    def __init__(self, session: AsyncSession, *, id_key: bytes, report_tz: str) -> None:
        """Bind to one transaction; ``id_key`` derives owner/device pseudonyms."""
        self._session = session
        self._repo = UsageRepository(session)
        self._key = id_key
        self._tz = ZoneInfo(report_tz)
        self._owner_cache: dict[str, str | None] = {}

    async def ingest(self, batch: UsageBatchIn) -> UsageBatchOut:
        """Validate and store every event of ``batch``; commit is the caller's."""
        rejected: list[tuple[int, RejectedEvent]] = []
        events: list[tuple[int, UsageEventIn]] = []
        for index, raw in enumerate(batch.events):
            event_id = raw.get("event_id")
            # Chỉ trả lại mã đúng dạng UUID: không phản hồi lại chuỗi tuỳ ý của client.
            shown_id = _uuid_text(event_id)
            model = EVENT_MODELS.get(str(raw.get("type")))
            if model is None:
                rejected.append((index, RejectedEvent(event_id=shown_id, code="UNKNOWN_TYPE")))
                continue
            try:
                events.append((index, model.model_validate(raw)))  # type: ignore[arg-type]
            except ValidationError:
                rejected.append((index, RejectedEvent(event_id=shown_id, code="INVALID_EVENT")))
        dirty: set[date] = set()
        try:
            async with self._session.begin_nested():
                new = await self._store_all([event for _, event in events], dirty)
        except IntegrityError:
            dirty = set()
            new, conflicts = await self._store_each(events, dirty)
            rejected += conflicts
        await self._repo.mark_dirty(dirty)
        rejected.sort(key=lambda item: item[0])
        return UsageBatchOut(
            accepted=new,
            duplicates=len(events) - new - sum(r.code == "CONFLICT" for _, r in rejected),
            rejected=[r for _, r in rejected],
        )

    async def _store_all(self, events: list[UsageEventIn], dirty: set[date]) -> int:
        """Fast path: one layer-1 insert for the batch, then layer 2 per new event in order."""
        now = datetime.now(UTC)
        rows = [await self._row(event, now) for event in events]
        fresh = await self._repo.insert_events([row for row, _ in rows])
        new = 0
        for event, (row, owner) in zip(events, rows, strict=True):
            if event.event_id in fresh:
                fresh.discard(event.event_id)  # repeated in the batch: only the first one counts
                await self._apply(event, owner, row["device_hash"], dirty)
                new += 1
        return new

    async def _store_each(
        self, events: list[tuple[int, UsageEventIn]], dirty: set[date]
    ) -> tuple[int, list[tuple[int, RejectedEvent]]]:
        """Slow path after a conflict: one savepoint per event; only the offender is rejected."""
        new = 0
        conflicts: list[tuple[int, RejectedEvent]] = []
        for index, event in events:
            try:
                async with self._session.begin_nested():
                    is_new = await self._store(event, dirty)
            except IntegrityError:
                conflicts.append(
                    (index, RejectedEvent(event_id=str(event.event_id), code="CONFLICT"))
                )
                continue
            new += is_new
        return new, conflicts

    async def _owner_hash(self, device_id: str) -> str | None:
        if device_id not in self._owner_cache:
            owner = await self._repo.active_owner(device_id)
            self._owner_cache[device_id] = (
                pseudonym(self._key, "owner", owner) if owner is not None else None
            )
        return self._owner_cache[device_id]

    async def _row(self, event: UsageEventIn, now: datetime) -> tuple[dict[str, Any], str | None]:
        """Layer-1 row of ``event`` (pseudonymised ids, operational body) plus owner pseudonym."""
        owner = await self._owner_hash(event.device_id)
        body = event.model_dump(
            mode="json",
            exclude={"event_id", "type", "occurred_at", "device_id", "component_version"},
            exclude_none=True,
        )
        row = {
            "event_id": event.event_id,
            "type": event.type,
            "occurred_at": event.occurred_at,
            "received_at": now,
            "owner_hash": owner,
            "device_hash": pseudonym(self._key, "device", event.device_id),
            "component_version": event.component_version,
            "body": body,
        }
        return row, owner

    async def _store(self, event: UsageEventIn, dirty: set[date]) -> bool:
        row, owner = await self._row(event, datetime.now(UTC))
        is_new = await self._repo.insert_event(row)
        if is_new:
            await self._apply(event, owner, row["device_hash"], dirty)
        return is_new

    async def _apply(
        self, event: UsageEventIn, owner: str | None, device: str, dirty: set[date]
    ) -> None:
        ids: dict[str, Any] = {"owner_hash": owner, "device_hash": device}
        at = event.occurred_at
        if isinstance(event, AttemptStarted):
            started = await self._repo.upsert_attempt(
                {
                    "attempt_id": event.attempt_id,
                    "task_id": event.task_id,
                    "feature": event.feature,
                    "entry": event.entry,
                    "started_at": at,
                    "component_version": event.component_version,
                    "firmware_version": event.firmware_version,
                    **ids,
                }
            )
            task_started = await self._repo.upsert_task(
                {"task_id": event.task_id, "feature": event.feature, "started_at": at, **ids}
            )
            self._dirty(dirty, started, task_started)
        elif isinstance(event, AttemptSignal):
            signals: dict[str, int] = {event.signal: 1}
            if event.reason_code is not None:
                signals[f"{event.signal}:{event.reason_code}"] = 1
            self._dirty(
                dirty,
                await self._repo.upsert_attempt(
                    {"attempt_id": event.attempt_id, "signals": signals, **ids}
                ),
            )
        elif isinstance(event, AttemptMarks):
            marks = {
                key: value
                for key, value in (
                    ("first_audio_at", event.first_audio_at),
                    ("processing_ms", event.processing_ms),
                    ("align_ms", event.align_ms),
                )
                if value is not None
            }
            self._dirty(
                dirty,
                await self._repo.upsert_attempt({"attempt_id": event.attempt_id, **marks, **ids}),
            )
        elif isinstance(event, AttemptFinished):
            values: dict[str, Any] = {
                "attempt_id": event.attempt_id,
                "task_id": event.task_id,
                "status": event.status,
                "error_code": event.error_code,
                "playback": event.playback,
                "finished_at": at,
                **ids,
            }
            if event.request_id is not None:
                values["request_id"] = event.request_id
            self._dirty(dirty, await self._repo.upsert_attempt(values))
        elif isinstance(event, TaskFinished):
            self._dirty(
                dirty,
                await self._repo.upsert_task(
                    {
                        "task_id": event.task_id,
                        "feature": event.feature,
                        "status": event.status,
                        "attempts": event.attempts,
                        "finished_at": at,
                        **ids,
                    }
                ),
            )
        elif isinstance(event, (SessionStarted, SessionHeartbeat, SessionEnded, AlertRaised)):
            await self._apply_session(event, ids, dirty)

    async def _apply_session(
        self,
        event: SessionStarted | SessionHeartbeat | SessionEnded | AlertRaised,
        ids: dict[str, Any],
        dirty: set[date],
    ) -> None:
        at = event.occurred_at
        values: dict[str, Any] = {"session_id": event.session_id, "last_seen_at": at, **ids}
        if isinstance(event, AlertRaised):
            values["kind"] = "alert_mode"
            values["alerts_by_level"] = {str(event.level): 1}
            # An alert belongs to the day it was spoken, whatever the session span.
            dirty.add(report_day(at, self._tz))
        else:
            values["kind"] = event.kind
        if isinstance(event, SessionStarted):
            values["started_at"] = at
            values["firmware_version"] = event.firmware_version
            if event.device_info is not None:
                values["device_info"] = event.device_info.model_dump(exclude_none=True)
        elif isinstance(event, SessionEnded):
            values["ended_at"] = at
            values["end_reason"] = event.end_reason
            if event.align_ms is not None:
                values["align_ms"] = event.align_ms
        started, until = await self._repo.upsert_session(values)
        if started is not None and until is not None:
            dirty.update(days_between(started, until, self._tz))
        elif started is not None:
            dirty.add(report_day(started, self._tz))

    def _dirty(self, dirty: set[date], *starts: datetime | None) -> None:
        for started in starts:
            if started is not None:
                dirty.add(report_day(started, self._tz))
