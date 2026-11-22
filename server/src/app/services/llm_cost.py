"""Price LLM token totals (``usage_llm_daily``) at read time: actual, list and what-if cost.

Only tokens are stored; money is computed here from a dated price table, so a price change (e.g.
Gemini 3.8 Flash doubling on 2027-01-01) re-prices history without touching data.

Three numbers per row:
- ``cost_actual``: what was really paid. Providers in ``free_providers`` (UIT Gemma quota, 9router
  on a subscription) cost 0; any other provider is billed at the model's list price.
- ``cost_list``: the model's list price whatever the provider (what 9router traffic would cost on a
  paid key). Models without a price (Gemma on UIT) are counted in ``unpriced_calls``.
- ``what_if[model]``: the same tokens billed at another model's price. Tokenizers differ (images
  above all), so this is an estimate to compare branches, not an invoice.
"""

from __future__ import annotations

import json
import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

# USD per 1M tokens, paid tier Standard. Source: https://aistudio.google.com/docs/pricing (checked
# 2026-11-22; ai.google.dev redirects there). Thinking tokens bill as output.
# Override with LLM_PRICES_PATH.
DEFAULT_PRICES: list[dict[str, Any]] = [
    {
        "model": "gemini-3.8-flash",
        "to": "2026-12-31",
        "input": 0.75,
        "output": 3.75,
        "cached": 0.075,
    },
    {
        "model": "gemini-3.8-flash",
        "from": "2027-01-01",
        "input": 1.50,
        "output": 7.50,
        "cached": 0.15,
    },
]
_SUFFIX = re.compile(r"-(minimal|low|medium|high)$")


@dataclass(frozen=True)
class Price:
    """List price of one model over ``[first, last]`` (open ends = unbounded), USD per 1M tokens."""

    model: str
    first: date | None
    last: date | None
    input: float
    output: float
    cached: float | None

    def covers(self, day: date) -> bool:
        """Tell whether this price applies on ``day``."""
        return (self.first is None or day >= self.first) and (self.last is None or day <= self.last)


def base_model(model: str) -> str:
    """``ag/gemini-3.8-flash-low`` -> ``gemini-3.8-flash`` (provider prefix, thinking level)."""
    return _SUFFIX.sub("", model.rsplit("/", 1)[-1].lower())


def parse_prices(items: list[dict[str, Any]]) -> list[Price]:
    """Validate a price table (list of dicts with model/from/to/input/output/cached)."""
    out = []
    for item in items:
        price = Price(
            model=base_model(str(item["model"])),
            first=date.fromisoformat(item["from"]) if item.get("from") else None,
            last=date.fromisoformat(item["to"]) if item.get("to") else None,
            input=float(item["input"]),
            output=float(item["output"]),
            cached=float(item["cached"]) if item.get("cached") is not None else None,
        )
        if min(price.input, price.output, price.cached or 0.0) < 0:
            msg = f"negative price for {price.model}"
            raise ValueError(msg)
        out.append(price)
    return out


def load_prices(path: str | None) -> list[Price]:
    """Price table from ``path`` (JSON list) or the built-in default."""
    if not path:
        return parse_prices(DEFAULT_PRICES)
    return parse_prices(json.loads(Path(path).read_text(encoding="utf-8")))


def price_of(prices: list[Price], model: str, day: date) -> Price | None:
    """Return the price of ``model`` on ``day``; the last matching entry wins."""
    name = base_model(model)
    found = None
    for price in prices:
        if price.model == name and price.covers(day):
            found = price
    return found


def cost(price: Price | None, row: dict[str, Any]) -> float | None:
    """USD for one row's tokens at ``price`` (cached input billed at the cached rate if known)."""
    if price is None:
        return None
    cached = min(int(row["cached_tokens"]), int(row["in_tokens"]))
    fresh = int(row["in_tokens"]) - cached
    cached_rate = price.cached if price.cached is not None else price.input
    usd = fresh * price.input + cached * cached_rate + int(row["out_tokens"]) * price.output
    return usd / 1_000_000


_SUMS = (
    "calls",
    "estimated_calls",
    "in_tokens",
    "out_tokens",
    "reasoning_tokens",
    "cached_tokens",
    "images",
    "latency_ms_sum",
)


def _blank() -> dict[str, Any]:
    acc: dict[str, Any] = dict.fromkeys(_SUMS, 0)
    acc.update(cost_actual=0.0, cost_list=0.0, cost_what_if=0.0, unpriced_calls=0)
    return acc


def _add(
    acc: dict[str, Any], row: dict[str, Any], actual: float, listed: float | None, alt: float
) -> None:
    for key in _SUMS:
        acc[key] += int(row[key])
    acc["cost_actual"] += actual
    acc["cost_what_if"] += alt
    if listed is None:
        acc["unpriced_calls"] += int(row["calls"])
    else:
        acc["cost_list"] += listed


def _finish(acc: dict[str, Any]) -> dict[str, Any]:
    calls = acc["calls"]
    acc["avg_latency_ms"] = round(acc.pop("latency_ms_sum") / calls) if calls else None
    acc["cost_actual"] = round(acc["cost_actual"], 6)
    acc["cost_list"] = round(acc["cost_list"], 6)
    acc["cost_what_if"] = round(acc["cost_what_if"], 6)
    return acc


def _add_waste(
    waste: dict[str, Any], row: dict[str, Any], listed: float | None, alt: float
) -> None:
    """Tokens spent for nothing: cancelled (wrong-lane speculation, cut turns), prewarm, errors."""
    if row["status"] == "error":
        waste["error_calls"] += int(row["calls"])
    kinds = [
        k
        for k, hit in (
            ("cancelled", row["status"] == "cancelled"),
            ("prewarm", row["purpose"] == "prewarm"),
        )
        if hit
    ]
    for kind in kinds:
        waste[f"{kind}_calls"] += int(row["calls"])
        waste[f"{kind}_tokens"] += int(row["in_tokens"]) + int(row["out_tokens"])
        waste[f"{kind}_cost_list"] += listed or 0.0
        waste[f"{kind}_cost_what_if"] += alt


def report(  # noqa: PLR0913 -- one pure builder, inputs listed explicitly
    rows: list[dict[str, Any]],
    *,
    prices: list[Price],
    free_providers: list[str],
    compare_models: list[str],
    attempts_by_feature: dict[str, int],
    usd_vnd: float | None,
) -> dict[str, Any]:
    """Build ``GET /admin/stats/llm``. ``rows`` are ``usage_llm_daily`` rows (with ``day``).

    ``cost_what_if`` on every group prices its tokens at the FIRST compare model (0 if none), so
    groups stay comparable while the main branch (Gemma UIT) has no list price.
    """
    free = {p.lower() for p in free_providers}
    totals = _blank()
    by_day: dict[str, dict[str, Any]] = defaultdict(_blank)
    by_feature: dict[str, dict[str, Any]] = defaultdict(_blank)
    by_purpose: dict[str, dict[str, Any]] = defaultdict(_blank)
    by_model: dict[tuple[str, str], dict[str, Any]] = defaultdict(_blank)
    waste = {
        "cancelled_calls": 0,
        "cancelled_tokens": 0,
        "cancelled_cost_list": 0.0,
        "cancelled_cost_what_if": 0.0,
        "prewarm_calls": 0,
        "prewarm_tokens": 0,
        "prewarm_cost_list": 0.0,
        "prewarm_cost_what_if": 0.0,
        "error_calls": 0,
    }
    head = compare_models[0] if compare_models else None
    what_if: dict[str, dict[str, Any]] = {
        m: {"model": m, "cost": 0.0, "priced": False} for m in compare_models
    }
    for row in rows:
        day: date = row["day"]
        listed = cost(price_of(prices, row["model"], day), row)
        actual = 0.0 if str(row["provider"]).lower() in free else (listed or 0.0)
        alt_head = (cost(price_of(prices, head, day), row) or 0.0) if head else 0.0
        for acc in (
            totals,
            by_day[day.isoformat()],
            by_feature[row["feature"]],
            by_purpose[row["purpose"]],
            by_model[(row["provider"], row["model"])],
        ):
            _add(acc, row, actual, listed, alt_head)
        _add_waste(waste, row, listed, alt_head)
        for name, item in what_if.items():
            alt = cost(price_of(prices, name, day), row)
            if alt is not None:
                item["cost"] += alt
                item["priced"] = True

    attempts = sum(attempts_by_feature.values())
    features = []
    for name, acc in sorted(by_feature.items()):
        out = _finish(acc)
        n = attempts_by_feature.get(name, 0)
        out.update(
            feature=name,
            attempts=n,
            cost_list_per_attempt=round(out["cost_list"] / n, 6) if n else None,
            cost_what_if_per_attempt=round(out["cost_what_if"] / n, 6) if n else None,
        )
        features.append(out)
    for item in what_if.values():
        item["cost"] = round(item["cost"], 6)
        item["per_attempt"] = (
            round(item["cost"] / attempts, 6) if attempts and item["priced"] else None
        )
    for key in (
        "cancelled_cost_list",
        "cancelled_cost_what_if",
        "prewarm_cost_list",
        "prewarm_cost_what_if",
    ):
        waste[key] = round(waste[key], 6)
    return {
        "currency": "USD",
        "what_if_model": head,
        "usd_vnd": usd_vnd,
        "attempts": attempts,
        "totals": _finish(totals),
        "waste": waste,
        "by_day": [{"day": d, **_finish(acc)} for d, acc in sorted(by_day.items())],
        "by_feature": features,
        "by_purpose": [{"purpose": p, **_finish(acc)} for p, acc in sorted(by_purpose.items())],
        "by_model": [
            {"provider": p, "model": m, **_finish(acc)} for (p, m), acc in sorted(by_model.items())
        ],
        "what_if": list(what_if.values()),
    }
