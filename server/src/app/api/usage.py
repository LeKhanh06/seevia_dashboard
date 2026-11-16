"""Usage-statistics write endpoint for the glasses server (VH)."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession  # noqa: TC002

from app.auth import require_usage_writer
from app.database import get_db_session
from app.schemas.usage import UsageBatchIn, UsageBatchOut
from app.services.usage_ingest import UsageIngestService

router = APIRouter(prefix="/api/v1/usage", tags=["usage"])


@router.post(
    "/events",
    response_model=UsageBatchOut,
    dependencies=[Depends(require_usage_writer)],
)
async def ingest_usage_events(
    body: UsageBatchIn,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> UsageBatchOut:
    """Store one batch of usage events (idempotent per ``event_id``)."""
    settings = request.app.state.settings
    if settings.usage_id_key is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="USAGE_NOT_CONFIGURED"
        )
    service = UsageIngestService(
        session,
        id_key=bytes.fromhex(settings.usage_id_key.get_secret_value()),
        report_tz=settings.usage_report_tz,
    )
    result = await service.ingest(body)
    await session.commit()
    return result
