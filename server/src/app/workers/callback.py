"""Callback worker: deliver terminal results to the client's callback URL.

Claims due callbacks with a lease, POSTs outside the transaction, then
records the outcome fenced by that lease. Backoff: 10s, 60s, 5m, 30m.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from app.adapters.callback import CallbackAdapter, CallbackOutcomeStatus, CallEventAdapter
from app.repositories.call import CallRepository
from app.repositories.operation import OperationRepository

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from app.config import Settings
    from app.models.operation import Operation

logger = logging.getLogger("app.workers.callback")

_CALLBACK_BACKOFF = [
    timedelta(seconds=10),
    timedelta(seconds=60),
    timedelta(seconds=300),
    timedelta(seconds=1800),
]


# Sự kiện cuộc gọi sống ngắn (chuông 30 s): thử lại dồn dập rồi bỏ, không treo hàng giờ.
_CALL_EVENT_BACKOFF = [
    timedelta(seconds=1),
    timedelta(seconds=2),
    timedelta(seconds=5),
    timedelta(seconds=10),
]


class CallbackWorker:
    """Worker polling and sending webhook callbacks for terminal operations."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        settings: Settings,
        wake_event: asyncio.Event | None = None,
        call_events: CallEventAdapter | None = None,
    ) -> None:
        """Initialize CallbackWorker with session factory and configuration."""
        self._session_factory = session_factory
        self._settings = settings
        self._adapter = CallbackAdapter(settings)
        self._call_events = call_events or CallEventAdapter(settings)
        self._wake_event = wake_event or asyncio.Event()

    async def run_once(self) -> int:
        """Execute one polling batch of callback claims and HTTP notifications.

        Returns the number of claimed operations and call events processed.
        """
        call_events = await self._run_call_events()
        now = datetime.now(UTC)
        lease_duration = timedelta(seconds=self._settings.callback_lease_seconds)
        lease_until = now + lease_duration
        batch_size = self._settings.worker_batch_size

        async with self._session_factory() as session:
            repo = OperationRepository(session)
            claimed = await repo.claim_due_callbacks(
                now=now, lease_until=lease_until, limit=batch_size
            )
            await session.commit()

        if not claimed:
            return call_events

        for op in claimed:
            try:
                await self._process_claimed_callback(op)
            except Exception:
                logger.exception(
                    "Unexpected error processing callback for request_id=%s",
                    op.request_id,
                )

        return len(claimed) + call_events

    async def _run_call_events(self) -> int:
        """Deliver due call events (``/internal/calls``); returns how many were attempted."""
        now = datetime.now(UTC)
        async with self._session_factory() as session:
            repo = CallRepository(session)
            claimed = await repo.claim_due_callbacks(
                now=now,
                lease_until=now + timedelta(seconds=self._settings.callback_lease_seconds),
                limit=self._settings.worker_batch_size,
            )
            await session.commit()

        for event in claimed:
            try:
                outcome = await self._call_events.send_event(
                    event_id=event.id, payload=event.payload
                )
                async with self._session_factory() as session:
                    repo = CallRepository(session)
                    done = datetime.now(UTC)
                    if outcome.status is CallbackOutcomeStatus.DELIVERED:
                        await repo.record_callback_delivered(event.id, now=done)
                    elif outcome.status is CallbackOutcomeStatus.RETRY and event.attempts < len(
                        _CALL_EVENT_BACKOFF
                    ):
                        await repo.record_callback_retry(
                            event.id, next_attempt_at=done + _CALL_EVENT_BACKOFF[event.attempts]
                        )
                    else:
                        await repo.record_callback_dead_letter(event.id)
                        logger.warning(
                            "Call event %s dead-lettered: %s", event.id, outcome.error_reason
                        )
                    await session.commit()
            except Exception:
                logger.exception("Unexpected error delivering call event %s", event.id)
        return len(claimed)

    async def _process_claimed_callback(self, op: Operation) -> None:
        """Send HTTP callback and persist outcome."""
        now = datetime.now(UTC)
        locked_until = op.callback_locked_until or now

        outcome = await self._adapter.send_callback(op)

        async with self._session_factory() as session:
            repo = OperationRepository(session)

            if outcome.status is CallbackOutcomeStatus.DELIVERED:
                await repo.record_callback_delivered(
                    request_id=op.request_id,
                    lease_until=locked_until,
                )
            elif outcome.status is CallbackOutcomeStatus.RETRY:
                attempt_idx = min(op.callback_attempts, len(_CALLBACK_BACKOFF) - 1)
                retry_delay = _CALLBACK_BACKOFF[attempt_idx]
                next_callback_at = now + retry_delay
                await repo.record_callback_retry(
                    request_id=op.request_id,
                    lease_until=locked_until,
                    next_callback_at=next_callback_at,
                )
            else:  # DEAD_LETTER
                await repo.record_callback_dead_letter(
                    request_id=op.request_id,
                    lease_until=locked_until,
                )

            await session.commit()

    async def run_loop(self, shutdown_event: asyncio.Event) -> None:
        """Continuous polling callback worker loop until shutdown signal is set."""
        poll_interval = self._settings.worker_poll_seconds
        while not shutdown_event.is_set():
            try:
                processed = await self.run_once()
                if processed == 0:
                    try:
                        await asyncio.wait_for(self._wake_event.wait(), timeout=poll_interval)
                    except TimeoutError:
                        pass
                    finally:
                        self._wake_event.clear()
            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("Error in CallbackWorker loop")
                await asyncio.sleep(poll_interval)
