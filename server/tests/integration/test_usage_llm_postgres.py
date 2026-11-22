"""PostgreSQL: ``llm_call`` events -> usage_llm_daily (rollup) -> ``GET /admin/stats/llm``."""

from __future__ import annotations

from datetime import date
from typing import TYPE_CHECKING, Any

import pytest
from sqlalchemy import select, text

from app.models.usage import UsageLlmDaily
from app.workers.usage_rollup import UsageRollupWorker
from tests.integration.admin_helpers import admin_client, auth, login, make_admin
from tests.integration.usage_helpers import (
    attempt,
    ev,
    post_events,
    usage_client,
    usage_settings,
    vn,
)

if TYPE_CHECKING:
    from uuid import UUID

    from sqlalchemy.ext.asyncio import AsyncSession

    from app.database import AsyncSessionFactory


def _call(at: str, attempt_id: UUID | None, **kw: object) -> dict[str, Any]:
    fields: dict[str, Any] = {
        "provider": "9router",
        "model": "ag/gemini-3.8-flash",
        "purpose": "answer",
        "status": "ok",
        "in_tokens": 1_000_000,
        "out_tokens": 100_000,
        "reasoning_tokens": 40_000,
        "estimated": False,
        "latency_ms": 900,
    }
    if attempt_id is not None:
        fields["attempt_id"] = attempt_id
    fields.update(kw)
    return ev("llm_call", vn(at), **fields)


async def _ingest_and_roll(factory: AsyncSessionFactory, events: list[dict[str, Any]]) -> None:
    async with usage_client(factory) as (client, token):
        await post_events(client, token, events)
    worker = UsageRollupWorker(factory, usage_settings("unused"))
    await worker.run_once(now=vn("2026-11-22 10:30:00"))


async def test_llm_calls_roll_up_by_feature_and_purpose(
    db_session: AsyncSession, postgres_session_factory: AsyncSessionFactory
) -> None:
    """Calls join their attempt's feature; calls outside a turn land in ``none``; dirty cleared."""
    att, _, events = attempt(
        "chat", vn("2026-11-22 10:00:00"), vn("2026-11-22 10:00:05"), "success"
    )
    events += [
        _call("2026-11-22 10:00:01", att, purpose="classify", in_tokens=500, out_tokens=2),
        _call("2026-11-22 10:00:02", att),
        _call("2026-11-22 10:00:02", att, status="cancelled", estimated=True, out_tokens=30),
        _call("2026-11-22 09:00:00", None, purpose="prewarm", provider="uit", model="gemma-4-26b"),
    ]
    await _ingest_and_roll(postgres_session_factory, events)

    rows = (await db_session.execute(select(UsageLlmDaily))).scalars().all()
    got = {(r.feature, r.provider, r.purpose, r.status): r for r in rows}
    assert set(got) == {
        ("chat", "9router", "classify", "ok"),
        ("chat", "9router", "answer", "ok"),
        ("chat", "9router", "answer", "cancelled"),
        ("none", "uit", "prewarm", "ok"),
    }
    answer = got[("chat", "9router", "answer", "ok")]
    assert (answer.calls, answer.attempts, answer.in_tokens, answer.out_tokens) == (
        1,
        1,
        1_000_000,
        100_000,
    )
    assert answer.reasoning_tokens == 40_000
    assert answer.latency_ms_sum == 900
    assert got[("chat", "9router", "answer", "cancelled")].estimated_calls == 1
    assert got[("none", "uit", "prewarm", "ok")].attempts == 0
    assert all(r.day == date(2026, 11, 22) for r in rows)
    assert await db_session.scalar(text("SELECT count(*) FROM usage_dirty_days")) == 0


async def test_rollup_is_idempotent_and_duplicates_not_double_counted(
    db_session: AsyncSession, postgres_session_factory: AsyncSessionFactory
) -> None:
    """Re-sending the same event ids and re-rolling the day keeps one count."""
    att, _, events = attempt("ask", vn("2026-11-22 10:00:00"), vn("2026-11-22 10:00:05"), "success")
    events.append(_call("2026-11-22 10:00:01", att))
    await _ingest_and_roll(postgres_session_factory, events)
    await _ingest_and_roll(postgres_session_factory, events)
    total = await db_session.scalar(
        select(UsageLlmDaily.calls).where(UsageLlmDaily.purpose == "answer")
    )
    assert total == 1


@pytest.mark.parametrize("compare", [["gemini-3.8-flash"]])
async def test_admin_llm_endpoint_prices_tokens(
    db_session: AsyncSession,  # noqa: ARG001 -- truncates the tables after the test
    postgres_session_factory: AsyncSessionFactory,
    compare: list[str],
) -> None:
    """9router is free in fact but priced at list; Gemma UIT is unpriced but priced in what-if."""
    att, _, events = attempt(
        "chat", vn("2026-11-22 10:00:00"), vn("2026-11-22 10:00:05"), "success"
    )
    events += [
        _call("2026-11-22 10:00:02", att),
        _call("2026-11-22 10:00:03", att, provider="uit", model="gemma-4-26b", purpose="classify"),
    ]
    await _ingest_and_roll(postgres_session_factory, events)

    await make_admin(postgres_session_factory)
    async with admin_client(postgres_session_factory, llm_compare_models=compare) as client:
        token = await login(client)
        unauth = await client.get("/api/v1/admin/stats/llm")
        assert unauth.status_code == 401
        resp = await client.get(
            "/api/v1/admin/stats/llm",
            params={"period": "custom", "from": "2026-11-22", "to": "2026-11-22"},
            headers=auth(token),
        )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    totals = body["totals"]
    assert totals["calls"] == 2
    assert totals["in_tokens"] == 2_000_000
    assert totals["cost_actual"] == 0  # both branches free in fact
    assert totals["cost_list"] == pytest.approx(0.75 + 0.375)  # only the Gemini row has a price
    assert totals["unpriced_calls"] == 1
    (alt,) = body["what_if"]
    assert alt["cost"] == pytest.approx(2 * (0.75 + 0.375))
    assert alt["per_attempt"] == pytest.approx(2 * (0.75 + 0.375))
    assert {m["model"] for m in body["by_model"]} == {"ag/gemini-3.8-flash", "gemma-4-26b"}
    assert body["by_feature"][0]["feature"] == "chat"
    assert body["by_feature"][0]["attempts"] == 1
