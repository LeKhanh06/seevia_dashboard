"""Usage-statistics write endpoint for the glasses server (VH)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession  # noqa: TC002

from app.auth import require_usage_writer
from app.database import get_db_session
from app.errors import _error_response
from app.schemas.usage import MAX_BATCH_BYTES, UsageBatchIn, UsageBatchOut
from app.services.usage_ingest import UsageIngestService

if TYPE_CHECKING:
    from starlette.types import ASGIApp, Message, Receive, Scope, Send

router = APIRouter(prefix="/api/v1/usage", tags=["usage"])
EVENTS_PATH = "/api/v1/usage/events"


class UsageBodyLimitMiddleware:
    """Từ chối thân lô thống kê quá ``MAX_BATCH_BYTES`` (413) TRƯỚC khi FastAPI parse JSON.

    Chỉ áp cho ``POST /api/v1/usage/events``. Có ``Content-Length`` thì xét ngay; không có
    (chunked) thì đọc tối đa trần rồi phát lại thân cho ứng dụng.
    """

    def __init__(self, app: ASGIApp, limit: int = MAX_BATCH_BYTES) -> None:
        """Bọc ``app``; ``limit`` là số byte tối đa của thân lô."""
        self.app = app
        self.limit = limit

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Chuyển thẳng mọi request khác; lô thống kê quá trần nhận 413."""
        if scope["type"] != "http" or scope.get("path") != EVENTS_PATH:
            await self.app(scope, receive, send)
            return
        declared = dict(scope.get("headers") or []).get(b"content-length")
        if declared is not None:
            if self._over(declared):
                await self._reject(scope, receive, send)
            else:
                await self.app(scope, receive, send)
            return
        body = await self._read_capped(receive)
        if body is None:
            await self._reject(scope, receive, send)
            return
        sent = False

        async def replay() -> Message:
            nonlocal sent
            if sent:
                return await receive()
            sent = True
            return {"type": "http.request", "body": body, "more_body": False}

        await self.app(scope, replay, send)

    def _over(self, declared: bytes) -> bool:
        try:
            return int(declared) > self.limit
        except ValueError:
            return True

    async def _read_capped(self, receive: Receive) -> bytes | None:
        """Đọc thân không có Content-Length; None khi vượt trần."""
        chunks: list[bytes] = []
        size = 0
        while True:
            message = await receive()
            if message["type"] != "http.request":
                break
            chunk = message.get("body", b"")
            size += len(chunk)
            if size > self.limit:
                return None
            chunks.append(chunk)
            if not message.get("more_body", False):
                break
        return b"".join(chunks)

    async def _reject(self, scope: Scope, receive: Receive, send: Send) -> None:
        response = _error_response(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            code="PAYLOAD_TOO_LARGE",
            message="Usage batch is too large.",
            details={"max_bytes": self.limit},
        )
        await response(scope, receive, send)


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
