"""Emergency worker: text the relatives with the location, and keep the call chain moving.

Two independent loops so a slow location lookup (up to ``EMERGENCY_LOCATION_TIMEOUT_S``) never
delays the next call: the chain loop reacts to calls ending (woken by the call endpoints and the
timeout worker, polling as the durable fallback); the message loop leases chains whose texts are
owed, asks the owner's phone for its location through a regular ``location_get`` operation
(client ``INTERNAL_CLIENT_ID``, so no callback goes to the glasses server), then texts everyone.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING
from uuid import uuid4

from app.actions import ACTION_ROUTES_BY_ACTION, Action
from app.adapters.delivery import get_delivery_adapter
from app.models.enums import RequestState
from app.repositories.emergency import EmergencyRepository
from app.repositories.operation import OperationRepository
from app.schemas.service_requests import LocationGetRequest
from app.services.emergency import EmergencyService
from app.services.operation import INTERNAL_CLIENT_ID, OperationService
from app.workers.wake import WorkerWakeSignals

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from app.adapters.delivery import DeliveryAdapter
    from app.config import Settings

logger = logging.getLogger("app.workers.emergency")

_LOCATION_POLL_S = 0.2
# Thời gian giữ chỗ chuỗi khi đang gửi tin: dư so với trần chờ vị trí, hết hạn thì worker khác nhặt.
_MESSAGE_LEASE_MARGIN_S = 20.0


class EmergencyWorker:
    """Advance emergency chains and send their distress texts."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        settings: Settings,
        wake_signals: WorkerWakeSignals | None = None,
        adapter: DeliveryAdapter | None = None,
    ) -> None:
        """Bind session factory, settings, wake signals and push transport (optional for tests)."""
        self._session_factory = session_factory
        self._settings = settings
        self._adapter = adapter or get_delivery_adapter(settings)
        self._wake = wake_signals or WorkerWakeSignals()

    async def run_chain_once(self) -> int:
        """Move chains whose current call ended; return how many changed."""
        async with self._session_factory() as session:
            service = EmergencyService(session, self._settings, self._adapter)
            changed = await service.advance_running(limit=self._settings.worker_batch_size)
            await session.commit()
        await service.dispatch_pushes()
        if changed:
            self._wake.callback.set()
        return changed

    async def run_messages_once(self) -> int:
        """Text the relatives of every chain that still owes it; return how many chains."""
        now = datetime.now(UTC)
        lease = timedelta(
            seconds=self._settings.emergency_location_timeout_s + _MESSAGE_LEASE_MARGIN_S
        )
        async with self._session_factory() as session:
            claimed = await EmergencyRepository(session).claim_messages_due(
                now=now, lease_until=now + lease, limit=self._settings.worker_batch_size
            )
            # Tách khỏi phiên: lấy id trước khi commit đóng phiên, vị trí chờ ngoài giao dịch.
            targets = [(e.id, e.owner_user_id, e.glasses_device_id) for e in claimed]
            await session.commit()
        for emergency_id, owner_user_id, glasses_device_id in targets:
            try:
                location = await self._fetch_location(owner_user_id, glasses_device_id)
                async with self._session_factory() as session:
                    service = EmergencyService(session, self._settings, self._adapter)
                    await service.send_messages(emergency_id, location)
                    await session.commit()
                await service.dispatch_pushes()
            except Exception:
                logger.exception(
                    "Emergency texts failed for %s; will retry after the lease", emergency_id
                )
        return len(targets)

    async def _fetch_location(
        self, owner_user_id: str, glasses_device_id: str
    ) -> tuple[float, float] | None:
        """Ask the owner's phone where it is; ``None`` on error, timeout or no phone (<= cap)."""
        request_id = uuid4()
        try:
            async with self._session_factory() as session:
                await OperationService(session, self._settings).accept(
                    client_id=INTERNAL_CLIENT_ID,
                    user_id=owner_user_id,
                    route=ACTION_ROUTES_BY_ACTION[Action.LOCATION_GET],
                    request=LocationGetRequest(device_id=glasses_device_id, request_id=request_id),
                )
                await session.commit()
            self._wake.delivery.set()
            deadline = (
                asyncio.get_running_loop().time() + self._settings.emergency_location_timeout_s
            )
            while asyncio.get_running_loop().time() < deadline:
                async with self._session_factory() as session:
                    operation = await OperationRepository(session).get_by_request_id(
                        client_id=INTERNAL_CLIENT_ID, request_id=request_id
                    )
                if operation is not None and operation.request_state is not RequestState.PROCESSING:
                    result = operation.result or {}
                    if operation.request_state is RequestState.SUCCEEDED:
                        return float(result["lat"]), float(result["lng"])  # type: ignore[arg-type]
                    return None
                await asyncio.sleep(_LOCATION_POLL_S)
        except Exception:
            logger.exception("Emergency location lookup failed")
        return None

    async def run_loop(self, shutdown_event: asyncio.Event) -> None:
        """Run the chain loop and the message loop until shutdown."""
        await asyncio.gather(
            self._loop(shutdown_event, self.run_chain_once, self._wake.emergency_chain),
            self._loop(shutdown_event, self.run_messages_once, self._wake.emergency_messages),
        )

    async def _loop(
        self,
        shutdown_event: asyncio.Event,
        step: Callable[[], Awaitable[int]],
        wake: asyncio.Event,
    ) -> None:
        poll_interval = self._settings.worker_poll_seconds
        while not shutdown_event.is_set():
            try:
                if await step() == 0:
                    try:
                        await asyncio.wait_for(wake.wait(), timeout=poll_interval)
                    except TimeoutError:
                        pass
                    finally:
                        wake.clear()
            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("Error in EmergencyWorker loop")
                await asyncio.sleep(poll_interval)
