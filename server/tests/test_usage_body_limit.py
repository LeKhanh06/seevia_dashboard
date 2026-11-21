"""Trần byte của lô thống kê: 413 trước khi parse JSON; mã sự kiện lạ không bị phản hồi lại."""

from collections.abc import AsyncIterator

import pytest
from fastapi import FastAPI, Request
from httpx import ASGITransport, AsyncClient

from app.api.usage import EVENTS_PATH, UsageBodyLimitMiddleware
from app.services.usage_ingest import _uuid_text


def _app(limit: int) -> tuple[FastAPI, list[int]]:
    seen: list[int] = []
    app = FastAPI()
    app.add_middleware(UsageBodyLimitMiddleware, limit=limit)

    @app.post(EVENTS_PATH)
    async def events(request: Request) -> dict[str, int]:
        body = await request.body()
        seen.append(len(body))
        return {"size": len(body)}

    @app.post("/other")
    async def other(request: Request) -> dict[str, int]:
        return {"size": len(await request.body())}

    return app, seen


@pytest.mark.asyncio
async def test_oversized_batch_rejected_before_handler() -> None:
    """Có Content-Length: quá trần -> 413, handler không chạy; route khác không bị giới hạn."""
    app, seen = _app(limit=100)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
        ok = await client.post(EVENTS_PATH, content=b"x" * 100)
        big = await client.post(EVENTS_PATH, content=b"x" * 101)
        other = await client.post("/other", content=b"x" * 1000)
    assert ok.status_code == 200
    assert ok.json() == {"size": 100}
    assert big.status_code == 413
    assert big.json()["error"]["code"] == "PAYLOAD_TOO_LARGE"
    assert seen == [100]  # lô quá trần không tới handler
    assert other.status_code == 200  # route khác không bị giới hạn


@pytest.mark.asyncio
async def test_chunked_body_counted_without_content_length() -> None:
    """Không Content-Length (chunked): đếm byte thật, vẫn chặn khi vượt."""
    app, seen = _app(limit=100)

    async def gen(n: int) -> AsyncIterator[bytes]:
        for _ in range(n):
            yield b"x" * 30

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
        ok = await client.post(EVENTS_PATH, content=gen(3))
        big = await client.post(EVENTS_PATH, content=gen(4))
    assert ok.status_code == 200
    assert ok.json() == {"size": 90}
    assert big.status_code == 413
    assert seen == [90]


def test_rejected_event_id_only_echoes_uuid() -> None:
    """Sự kiện bị từ chối chỉ trả lại mã đúng dạng UUID."""
    good = "0192f3a0-0000-7000-8000-000000000001"
    assert _uuid_text(good) == good
    assert _uuid_text("<script>alert(1)</script>") is None
    assert _uuid_text("a" * 5000) is None
    assert _uuid_text(42) is None
