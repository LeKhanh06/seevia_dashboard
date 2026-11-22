"""Admin dashboard statistics: ``GET /api/v1/admin/stats/*`` (read-only, admin session)."""

from __future__ import annotations

from datetime import date  # noqa: TC003 -- FastAPI resolves query types at runtime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession  # noqa: TC002

from app.api.admin import Admin, Db  # noqa: TC001 -- FastAPI dependencies
from app.errors import AdminApiError
from app.services.admin_stats import (
    AdminStatsService,
    InvalidPeriodError,
    Period,
    PeriodName,
    resolve_period,
)

router = APIRouter(prefix="/api/v1/admin/stats", tags=["admin-stats"])


def _period(
    period: PeriodName = "week",
    first: Annotated[date | None, Query(alias="from")] = None,
    last: Annotated[date | None, Query(alias="to")] = None,
) -> Period:
    try:
        return resolve_period(period, first, last)
    except InvalidPeriodError as exc:
        raise AdminApiError(400, "invalid_request", str(exc)) from None


PeriodQ = Annotated[Period, Depends(_period)]


def _service(request: Request, session: AsyncSession) -> AdminStatsService:
    key = request.app.state.settings.usage_id_key
    return AdminStatsService(session, id_key=bytes.fromhex(key.get_secret_value()) if key else None)


@router.get("/overview")
async def stats_overview(
    _admin: Admin, period: PeriodQ, request: Request, session: Db
) -> dict[str, Any]:
    """Totals, series vs previous period, groups, SOS, top features, attention."""
    return await _service(request, session).overview(period)


async def _group(
    page: str, period: Period, request: Request, session: AsyncSession
) -> dict[str, Any]:
    return await _service(request, session).group_page(page, period)


@router.get("/see-read")
async def stats_see_read(
    _admin: Admin, period: PeriodQ, request: Request, session: Db
) -> dict[str, Any]:
    """See & Read group page."""
    return await _group("see-read", period, request, session)


@router.get("/find")
async def stats_find(
    _admin: Admin, period: PeriodQ, request: Request, session: Db
) -> dict[str, Any]:
    """Find & Understand group page."""
    return await _group("find", period, request, session)


@router.get("/connect")
async def stats_connect(
    _admin: Admin, period: PeriodQ, request: Request, session: Db
) -> dict[str, Any]:
    """Connect group page."""
    return await _group("connect", period, request, session)


@router.get("/go")
async def stats_go(_admin: Admin, period: PeriodQ, request: Request, session: Db) -> dict[str, Any]:
    """Go group page."""
    return await _group("go", period, request, session)


@router.get("/community")
async def stats_community(
    _admin: Admin, period: PeriodQ, request: Request, session: Db
) -> dict[str, Any]:
    """Accounts, glasses connection, disconnects (install/battery: no source → null)."""
    return await _service(request, session).community(period)


@router.get("/time")
async def stats_time(
    _admin: Admin, period: PeriodQ, request: Request, session: Db
) -> dict[str, Any]:
    """Count uses by day, hour, week and weekday x hour."""
    return await _service(request, session).time_of_use(period)


@router.get("/quality")
async def stats_quality(
    _admin: Admin, period: PeriodQ, request: Request, session: Db
) -> dict[str, Any]:
    """Completion, latency, outcomes, extra attempts."""
    return await _service(request, session).quality(period)


@router.get("/engagement")
async def stats_engagement(
    _admin: Admin, period: PeriodQ, request: Request, session: Db
) -> dict[str, Any]:
    """Active owners (DAU/WAU/MAU by period), new users, glasses."""
    return await _service(request, session).engagement(period)


@router.get("/safety")
async def stats_safety(
    _admin: Admin, period: PeriodQ, request: Request, session: Db
) -> dict[str, Any]:
    """SOS outcomes and alert-mode usage."""
    return await _service(request, session).safety(period)


@router.get("/llm")
async def stats_llm(
    _admin: Admin, period: PeriodQ, request: Request, session: Db
) -> dict[str, Any]:
    """LLM calls, tokens and cost (actual, list price, what-if per compare model)."""
    settings = request.app.state.settings
    return await _service(request, session).llm(
        period,
        prices_path=settings.llm_prices_path,
        free_providers=settings.llm_free_providers,
        compare_models=settings.llm_compare_models,
        usd_vnd=settings.llm_usd_vnd,
    )


@router.get("/errors")
async def stats_errors(  # noqa: PLR0913
    *,
    _admin: Admin,
    period: PeriodQ,
    request: Request,
    session: Db,
    group: Annotated[str | None, Query(max_length=40)] = None,
    firmware_version: Annotated[str | None, Query(max_length=40)] = None,
    sort: Literal["code", "feature", "group", "count", "change", "last_seen"] = "count",
    order: Literal["asc", "desc"] = "desc",
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 10,
) -> dict[str, Any]:
    """Error codes per feature with change vs previous period."""
    return await _service(request, session).errors(
        period,
        group=group,
        firmware_version=firmware_version,
        sort=sort,
        order=order,
        page=page,
        page_size=page_size,
    )
