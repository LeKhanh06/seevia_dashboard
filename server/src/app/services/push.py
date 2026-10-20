"""Collect FCM data messages during a transaction and send them after the commit."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

from app.adapters.delivery import DeliveryOutcomeStatus

if TYPE_CHECKING:
    from app.adapters.delivery import DeliveryAdapter

logger = logging.getLogger("app.services.push")


@dataclass(frozen=True, slots=True)
class QueuedPush:
    """One FCM data message waiting for the commit (``label`` is a log id, never content)."""

    push_token: str
    data: dict[str, str]
    label: str


class PushQueue:
    """Hold pushes until the router committed, so a phone never hears about a rolled-back write."""

    def __init__(self, adapter: DeliveryAdapter) -> None:
        """Bind the transport."""
        self._adapter = adapter
        self._items: list[QueuedPush] = []

    def add(self, push: QueuedPush) -> None:
        """Queue a push."""
        self._items.append(push)

    async def dispatch(self) -> None:
        """Send the queued pushes (best effort, never raises)."""
        items, self._items = self._items, []
        for push in items:
            try:
                outcome = await self._adapter.send_data(
                    push_token=push.push_token, data=push.data, label=push.label
                )
            except Exception:
                logger.exception("Push failed unexpectedly for %s", push.label)
                continue
            if outcome.status is not DeliveryOutcomeStatus.SENT:
                logger.warning("Push not delivered for %s: %s", push.label, outcome.status)
