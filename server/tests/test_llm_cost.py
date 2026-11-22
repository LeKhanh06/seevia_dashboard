"""LLM cost (app/services/llm_cost.py) and the ``llm_call`` event contract."""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from typing import Any

import pytest
from pydantic import ValidationError

from app.config import Settings
from app.schemas.usage import EVENT_MODELS
from app.services import llm_cost

PRICES = llm_cost.parse_prices(llm_cost.DEFAULT_PRICES)


def _row(**kw: object) -> dict[str, Any]:
    base: dict[str, Any] = {
        "day": date(2026, 11, 22),
        "feature": "chat",
        "provider": "aistudio",
        "model": "gemini-3.8-flash",
        "purpose": "answer",
        "status": "ok",
        "calls": 1,
        "estimated_calls": 0,
        "attempts": 1,
        "in_tokens": 1_000_000,
        "out_tokens": 100_000,
        "reasoning_tokens": 0,
        "cached_tokens": 0,
        "images": 0,
        "latency_ms_sum": 900,
    }
    base.update(kw)
    return base


def _report(rows: list[dict[str, Any]], **kw: object) -> dict[str, Any]:
    args: dict[str, Any] = {
        "prices": PRICES,
        "free_providers": ["uit", "9router"],
        "compare_models": ["gemini-3.8-flash"],
        "attempts_by_feature": {"chat": 4},
        "usd_vnd": None,
    }
    args.update(kw)
    return llm_cost.report(rows, **args)


def test_base_model_strips_provider_prefix_and_thinking_level() -> None:
    """Router prefix and thinking-level suffix map to the priced base model."""
    assert llm_cost.base_model("ag/gemini-3.8-flash-low") == "gemini-3.8-flash"
    assert llm_cost.base_model("models/Gemini-3.8-Flash") == "gemini-3.8-flash"
    assert llm_cost.base_model("gemma-4-26b") == "gemma-4-26b"


def test_price_follows_effective_dates() -> None:
    """The price row in force on the day is used; Gemma has no list price."""
    dec = llm_cost.price_of(PRICES, "gemini-3.8-flash", date(2026, 12, 31))
    jan = llm_cost.price_of(PRICES, "gemini-3.8-flash", date(2027, 1, 1))
    assert dec is not None
    assert dec.input == 0.75
    assert jan is not None
    assert jan.input == 1.50
    assert llm_cost.price_of(PRICES, "gemma-4-26b", date(2026, 11, 22)) is None


def test_cost_bills_cached_input_at_cached_rate() -> None:
    """Cached input tokens are billed at the cached rate, not the input rate."""
    price = llm_cost.price_of(PRICES, "gemini-3.8-flash", date(2026, 11, 22))
    row = _row(in_tokens=1_000_000, cached_tokens=400_000, out_tokens=200_000)
    # 600k * 0.75 + 400k * 0.075 + 200k * 3.75, per 1M
    assert llm_cost.cost(price, row) == pytest.approx(0.45 + 0.03 + 0.75)
    assert llm_cost.cost(None, row) is None


def test_free_provider_costs_nothing_but_keeps_list_price() -> None:
    """Free providers cost 0 in fact but still show the list price."""
    out = _report([_row(provider="9router", model="ag/gemini-3.8-flash")])
    assert out["totals"]["cost_actual"] == 0
    assert out["totals"]["cost_list"] == pytest.approx(0.75 + 0.375)


def test_paid_provider_actual_equals_list() -> None:
    """A paid provider's actual cost equals the list price."""
    out = _report([_row()])
    assert out["totals"]["cost_actual"] == out["totals"]["cost_list"] == pytest.approx(1.125)


def test_gemma_is_unpriced_but_what_if_prices_it() -> None:
    """Gemma counts as unpriced; the what-if model prices the same tokens."""
    out = _report([_row(provider="uit", model="gemma-4-26b")])
    assert out["totals"]["unpriced_calls"] == 1
    assert out["totals"]["cost_list"] == 0
    assert out["what_if_model"] == "gemini-3.8-flash"
    assert out["totals"]["cost_what_if"] == pytest.approx(1.125)
    assert out["by_model"][0]["cost_what_if"] == pytest.approx(1.125)
    (alt,) = out["what_if"]
    assert alt["priced"]
    assert alt["cost"] == pytest.approx(1.125)
    assert alt["per_attempt"] == pytest.approx(1.125 / 4)


def test_waste_counts_cancelled_and_prewarm() -> None:
    """Cancelled, prewarm and error calls are reported as waste."""
    out = _report(
        [
            _row(status="cancelled", in_tokens=0, out_tokens=1_000_000),
            _row(purpose="prewarm", in_tokens=1_000_000, out_tokens=0),
            _row(status="error", in_tokens=0, out_tokens=0),
        ]
    )
    waste = out["waste"]
    assert waste["cancelled_tokens"] == 1_000_000
    assert waste["prewarm_tokens"] == 1_000_000
    assert waste["cancelled_cost_what_if"] == pytest.approx(3.75)
    assert waste["prewarm_cost_what_if"] == pytest.approx(0.75)
    assert waste["cancelled_calls"] == 1
    assert waste["cancelled_cost_list"] == pytest.approx(3.75)
    assert waste["prewarm_calls"] == 1
    assert waste["prewarm_cost_list"] == pytest.approx(0.75)
    assert waste["error_calls"] == 1


def test_groupings_and_per_attempt() -> None:
    """Rows group by feature/purpose/day with a per-attempt cost."""
    out = _report(
        [_row(), _row(purpose="classify", in_tokens=0, out_tokens=0, latency_ms_sum=100)],
        attempts_by_feature={"chat": 2},
    )
    (feature,) = out["by_feature"]
    assert feature["feature"] == "chat"
    assert feature["attempts"] == 2
    assert feature["cost_list_per_attempt"] == pytest.approx(1.125 / 2)
    assert feature["cost_what_if_per_attempt"] == pytest.approx(1.125 / 2)
    assert [p["purpose"] for p in out["by_purpose"]] == ["answer", "classify"]
    assert out["totals"]["avg_latency_ms"] == 500
    assert out["by_day"][0]["day"] == "2026-11-22"


def test_price_table_rejects_negative() -> None:
    """A negative price in the table is rejected."""
    with pytest.raises(ValueError, match="negative"):
        llm_cost.parse_prices([{"model": "x", "input": -1, "output": 1}])


def _event(**kw: object) -> dict[str, Any]:
    base: dict[str, Any] = {
        "event_id": str(uuid.uuid4()),
        "type": "llm_call",
        "occurred_at": datetime.now(UTC).isoformat(),
        "device_id": "glasses-1",
        "attempt_id": str(uuid.uuid4()),
        "provider": "9router",
        "model": "ag/gemini-3.8-flash",
        "purpose": "answer",
        "status": "ok",
        "in_tokens": 2010,
        "out_tokens": 221,
        "reasoning_tokens": 191,
        "estimated": False,
    }
    base.update(kw)
    return base


def test_llm_call_event_accepts_counts_only() -> None:
    """The llm_call event takes counts only: no free text, known purposes, no negatives."""
    model = EVENT_MODELS["llm_call"]
    ev = model.model_validate(_event(attempt_id=None))
    assert ev.attempt_id is None  # type: ignore[attr-defined]
    assert ev.model == "ag/gemini-3.8-flash"  # type: ignore[attr-defined]
    with pytest.raises(ValidationError):
        model.model_validate(_event(text="câu người dùng nói"))  # no free text, ever
    with pytest.raises(ValidationError):
        model.model_validate(_event(purpose="anything"))
    with pytest.raises(ValidationError):
        model.model_validate(_event(in_tokens=-1))


def test_flash_lite_price_has_no_end_date() -> None:
    """Gemini 3.5 Flash-Lite: $0.30 in, $2.50 out, $0.03 cached; no scheduled change."""
    for day in (date(2026, 11, 22), date(2027, 6, 1)):
        price = llm_cost.price_of(PRICES, "ag/gemini-3.5-flash-lite", day)
        assert price is not None
        assert (price.input, price.output, price.cached) == (0.30, 2.50, 0.03)


def test_flash_lite_on_9router_priced_but_free() -> None:
    """The 9router trial branch costs 0 in fact; its list price uses the Flash-Lite row."""
    out = _report([_row(provider="9router", model="ag/gemini-3.5-flash-lite")])
    assert out["totals"]["cost_actual"] == 0
    assert out["totals"]["cost_list"] == pytest.approx(0.30 + 0.25)
    assert out["totals"]["unpriced_calls"] == 0


def test_default_compare_models_lead_with_flash_lite() -> None:
    """The chosen Gemini model (3.5 Flash-Lite) leads the what-if; 3.8 Flash stays second."""
    factory = Settings.model_fields["llm_compare_models"].default_factory
    assert factory is not None
    assert factory() == ["gemini-3.5-flash-lite", "gemini-3.8-flash"]  # type: ignore[call-arg]
