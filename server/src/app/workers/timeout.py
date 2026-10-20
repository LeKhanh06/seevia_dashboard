"""Timeout worker: operations without a report by ``expires_at`` become ``timed_out``.

The conditional update in the repository loses cleanly to a report that
arrives first, so a request is never both reported and timed out.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from app.adapters.delivery import get_delivery_adapter
from app.repositories.operation import OperationRepository
from app.services.call import CallService

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from app.config import Settings

logger = logging.getLogger(__name__)

_TIMEOUT_ERROR_PAYLOAD: dict[str, object] = {
    "code": "REPORT_TIMEOUT",
    "message": "Operation timed out waiting for device report",
    "details": {},
}


class TimeoutWorker:
    """Worker polling and transitioning expired processing operations."""

    def __init__(
        self, session_factory: async_sessionmaker[AsyncSession], settings: Settings
    ) -> None:
        """Initialize TimeoutWorker with session factory and configuration."""
        self._session_factory = session_factory
        self._settings = settings

    async def run_once(self, now: datetime | None = None) -> int:
        """Time out one batch of expired operations and unanswered calls; return how many changed.

        ``now`` chỉ để test đặt đồng hồ; mặc định là giờ thật.
        """
        now = now or datetime.now(UTC)
        async with self._session_factory() as session:
            repo = OperationRepository(session)
            expired = await repo.list_expired(now=now, limit=self._settings.worker_batch_size)
            count = 0
            for operation in expired:
                if await repo.record_timeout(
                    request_id=operation.request_id,
                    now=now,
                    error=_TIMEOUT_ERROR_PAYLOAD,
                    schedule_callback=self._settings.callback_url is not None,
                ):
                    count += 1
            await session.commit()

        if count:
            logger.info("Transitioned %d expired operation(s) to timed_out", count)
        return count + await self._expire_calls(now)

    async def _expire_calls(self, now: datetime) -> int:
        """Mark unanswered calls ``missed``; close ``active`` calls past the max duration."""
        async with self._session_factory() as session:
            service = CallService(session, self._settings, get_delivery_adapter(self._settings))
            limit = self._settings.worker_batch_size
            count = await service.expire_ringing(now=now, limit=limit)
            count += await service.sweep_active(now=now, limit=limit)
            await session.commit()
        await service.dispatch_pushes()
        if count:
            logger.info("Closed %d stale call(s) (missed or over max duration)", count)
        return count

    async def run_loop(self, shutdown_event: asyncio.Event) -> None:
        """Continuous polling timeout worker loop until shutdown signal is set."""
        poll_interval = self._settings.worker_poll_seconds
        while not shutdown_event.is_set():
            try:
                processed = await self.run_once()
                if processed == 0:
                    await asyncio.sleep(poll_interval)
            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("Error in TimeoutWorker loop")
                await asyncio.sleep(poll_interval)
