"""Glasses owner's own usage statistics: ``GET /api/v1/me/stats`` (app login session)."""

from __future__ import annotations

from datetime import date  # noqa: TC003 -- FastAPI resolves query types at runtime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession  # noqa: TC002

from app.auth import UserPrincipal, require_user_session
from app.database import get_db_session
from app.errors import VALIDATION_ERROR_RESPONSES, InvalidStatsPeriodError, UsageNotConfiguredError
from app.services.admin_stats import InvalidPeriodError, owner_stats, resolve_period

router = APIRouter(prefix="/api/v1/me", tags=["me"])


@router.get("/stats", responses=VALIDATION_ERROR_RESPONSES)
async def my_stats(
    request: Request,
    principal: Annotated[UserPrincipal, Depends(require_user_session)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
    first: Annotated[date, Query(alias="from")],
    last: Annotated[date, Query(alias="to")],
) -> dict[str, Any]:
    """Return the signed-in owner's uses, completion and time for ``from``..``to`` (inclusive)."""
    key = request.app.state.settings.usage_id_key
    if key is None:
        msg = "Usage statistics are not configured"
        raise UsageNotConfiguredError(msg)
    try:
        period = resolve_period("custom", first, last)
    except InvalidPeriodError as exc:
        raise InvalidStatsPeriodError(str(exc)) from None
    return await owner_stats(
        session,
        period,
        id_key=bytes.fromhex(key.get_secret_value()),
        public_user_id=principal.public_user_id,
    )
