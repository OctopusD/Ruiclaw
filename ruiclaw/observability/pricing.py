"""Versioned token-price snapshots and deterministic Run cost estimates."""

from __future__ import annotations

import json
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, cast

DEFAULT_PRICING_PATH = Path(__file__).with_name("pricing") / "v1.json"


@dataclass(frozen=True, slots=True)
class ModelPrice:
    input_usd_per_million: Decimal
    output_usd_per_million: Decimal
    cache_read_usd_per_million: Decimal | None = None
    cache_write_usd_per_million: Decimal | None = None


@dataclass(frozen=True, slots=True)
class PricingSnapshot:
    snapshot_id: str
    captured_at: str
    currency: str
    source_url: str
    scope: str
    prices: dict[tuple[str, str], ModelPrice]


def load_pricing_snapshot(path: Path = DEFAULT_PRICING_PATH) -> PricingSnapshot:
    """Load the immutable local price snapshot used to rebuild Run reports."""
    value: object = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("pricing snapshot must be a JSON object")
    data = cast(dict[str, object], value)
    if data.get("schema_version") != 1:
        raise ValueError("unsupported pricing snapshot schema_version")
    snapshot_id = _required_text(data, "snapshot_id")
    captured_at = _required_text(data, "captured_at")
    currency = _required_text(data, "currency")
    source_url = _required_text(data, "source_url")
    scope = _required_text(data, "scope")
    models_value = data.get("models")
    if not isinstance(models_value, list):
        raise ValueError("pricing snapshot models must be a list")
    prices: dict[tuple[str, str], ModelPrice] = {}
    for value in cast(list[object], models_value):
        if not isinstance(value, dict):
            raise ValueError("pricing snapshot model entries must be objects")
        row = cast(dict[str, object], value)
        key = (_required_text(row, "provider"), _required_text(row, "model"))
        if key in prices:
            raise ValueError(f"duplicate pricing entry: {key[0]}/{key[1]}")
        prices[key] = ModelPrice(
            input_usd_per_million=_required_decimal(row, "input_usd_per_million"),
            output_usd_per_million=_required_decimal(row, "output_usd_per_million"),
            cache_read_usd_per_million=_optional_decimal(
                row,
                "cache_read_usd_per_million",
            ),
            cache_write_usd_per_million=_optional_decimal(
                row,
                "cache_write_usd_per_million",
            ),
        )
    return PricingSnapshot(snapshot_id, captured_at, currency, source_url, scope, prices)


def estimate_run_cost(
    model_calls: list[dict[str, Any]],
    snapshot: PricingSnapshot,
) -> dict[str, Any]:
    """Estimate token cost without converting unknown prices into zero cost."""
    component_totals = {
        "input_usd": Decimal(0),
        "output_usd": Decimal(0),
        "cache_read_usd": Decimal(0),
        "cache_write_usd": Decimal(0),
    }
    priced_calls = 0
    unknown: set[str] = set()
    missing_usage_calls = 0
    for event in model_calls:
        payload_value = cast(object, event.get("payload"))
        if not isinstance(payload_value, dict):
            missing_usage_calls += 1
            continue
        payload = cast(dict[str, object], payload_value)
        provider = payload.get("provider")
        model = payload.get("model")
        usage_value = payload.get("usage")
        identity = (
            f"{provider}/{model}"
            if isinstance(provider, str) and isinstance(model, str)
            else "unknown/unknown"
        )
        if not isinstance(usage_value, dict):
            missing_usage_calls += 1
            unknown.add(identity)
            continue
        usage = cast(dict[str, object], usage_value)
        price = snapshot.prices.get((provider, model)) if isinstance(provider, str) and isinstance(model, str) else None
        if price is None:
            unknown.add(identity)
            continue
        call_cost = _call_cost(usage, price)
        if call_cost is None:
            unknown.add(identity)
            continue
        priced_calls += 1
        for key, amount in call_cost.items():
            component_totals[key] += amount

    call_count = len(model_calls)
    unpriced_calls = call_count - priced_calls
    status = "estimated" if unpriced_calls == 0 else "partial" if priced_calls else "unknown"
    partial_total = sum(component_totals.values(), Decimal(0))
    return {
        "status": status,
        "currency": snapshot.currency,
        "estimated_cost_usd": _money(partial_total) if status == "estimated" else None,
        "partial_estimated_cost_usd": _money(partial_total) if priced_calls else None,
        "pricing_snapshot_id": snapshot.snapshot_id,
        "pricing_captured_at": snapshot.captured_at,
        "pricing_source_url": snapshot.source_url,
        "pricing_scope": snapshot.scope,
        "priced_model_calls": priced_calls,
        "unpriced_model_calls": unpriced_calls,
        "missing_usage_calls": missing_usage_calls,
        "unknown_models": sorted(unknown),
        "breakdown": {
            key: None if status == "unknown" else _money(value)
            for key, value in component_totals.items()
        },
    }


def _call_cost(usage: dict[str, object], price: ModelPrice) -> dict[str, Decimal] | None:
    input_tokens = _token_count(usage.get("input_tokens"))
    output_tokens = _token_count(usage.get("output_tokens"))
    if input_tokens is None or output_tokens is None:
        return None
    cache_read = _optional_token_count(usage.get("cache_read_tokens"))
    cache_write = _optional_token_count(usage.get("cache_write_tokens"))
    if cache_read is None or cache_write is None:
        return None
    if cache_read + cache_write > input_tokens:
        return None
    if cache_read and price.cache_read_usd_per_million is None:
        return None
    if cache_write and price.cache_write_usd_per_million is None:
        return None
    regular_input = input_tokens - cache_read - cache_write
    unit = Decimal(1_000_000)
    return {
        "input_usd": Decimal(regular_input) * price.input_usd_per_million / unit,
        "output_usd": Decimal(output_tokens) * price.output_usd_per_million / unit,
        "cache_read_usd": Decimal(cache_read) * (price.cache_read_usd_per_million or 0) / unit,
        "cache_write_usd": Decimal(cache_write) * (price.cache_write_usd_per_million or 0) / unit,
    }


def _required_text(data: dict[str, object], key: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"pricing snapshot {key} must be non-empty text")
    return value


def _required_decimal(data: dict[str, object], key: str) -> Decimal:
    value = _optional_decimal(data, key)
    if value is None:
        raise ValueError(f"pricing snapshot {key} is required")
    return value


def _optional_decimal(data: dict[str, object], key: str) -> Decimal | None:
    value = data.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"pricing snapshot {key} must be a decimal string")
    try:
        parsed = Decimal(value)
    except InvalidOperation as exc:
        raise ValueError(f"pricing snapshot {key} is invalid") from exc
    if not parsed.is_finite() or parsed < 0:
        raise ValueError(f"pricing snapshot {key} must be non-negative")
    return parsed


def _token_count(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def _optional_token_count(value: object) -> int | None:
    return 0 if value is None else _token_count(value)


def _money(value: Decimal) -> float:
    return float(value.quantize(Decimal("0.00000001")))
