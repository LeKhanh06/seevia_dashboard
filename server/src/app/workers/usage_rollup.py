"""Usage rollup worker: recompute layer-3 rows for dirty report days; close abandoned open rows.

Each round (1) closes attempts/tasks VH never finished (VH died) older than
``usage_unclosed_after_s`` as ``technical_error`` / ``UNCLOSED`` and marks their days dirty, then
(2) leases a few dirty days with ``FOR UPDATE SKIP LOCKED`` and rebuilds each day from layer 2 in
its own transaction. A day re-marked while it was being computed keeps its mark for the next round,
so late events are never lost; two workers never compute the same day at the same time.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

from app.repositories.usage import UsageReadRepository, UsageRepository, report_day
from app.services.usage_aggregate import aggregate_day, day_bounds

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from app.config import Settings

logger = logging.getLogger(__name__)


class UsageRollupWorker:
    """Background recomputation of the daily usage aggregates."""

    def __init__(
        self, session_factory: async_sessionmaker[AsyncSession], settings: Settings
    ) -> None:
        """Bind to the session factory and the report time zone."""
        self._session_factory = session_factory
        self._settings = settings
        self._tz = ZoneInfo(settings.usage_report_tz)

    async def run_once(self, now: datetime | None = None) -> int:
        """Sweep stale rows and rebuild one batch of dirty days; return the days rebuilt."""
        now = now or datetime.now(UTC)
        async with self._session_factory() as session:
            cutoff = now - timedelta(seconds=self._settings.usage_unclosed_after_s)
            starts = await UsageReadRepository(session).sweep_unclosed(cutoff, now)
            await UsageRepository(session).mark_dirty({report_day(s, self._tz) for s in starts})
            claimed = await UsageReadRepository(session).claim_dirty(
                now, self._settings.usage_rollup_lease_s, self._settings.worker_batch_size
            )
            await session.commit()
        for day, marked_at in claimed:
            async with self._session_factory() as session:
                repo = UsageReadRepository(session)
                lo, hi = day_bounds(day, self._tz)
                result = aggregate_day(await repo.layer2(lo, hi), day, self._tz)
                await repo.replace_day(day, result)
                await repo.replace_llm_day(day, await repo.llm_day(lo, hi))
                await repo.release_dirty(day, marked_at)
                await session.commit()
        if claimed:
            logger.info("Usage rollup rebuilt %d day(s)", len(claimed))
        return len(claimed)

    async def run_loop(self, shutdown_event: asyncio.Event) -> None:
        """Poll until shutdown; idle rounds sleep ``usage_rollup_interval_s``."""
        interval = self._settings.usage_rollup_interval_s
        while not shutdown_event.is_set():
            try:
                if await self.run_once() == 0:
                    await asyncio.sleep(interval)
            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("Error in UsageRollupWorker loop")
                await asyncio.sleep(interval)
