from __future__ import annotations

import json

from ruiclaw.observability.pricing import estimate_run_cost, load_pricing_snapshot


def _event(provider: str, model: str, usage: dict[str, object]) -> dict[str, object]:
    return {
        "event_type": "model_call_finished",
        "payload": {"provider": provider, "model": model, "usage": usage},
    }


def test_cost_estimate_separates_regular_and_cached_input(tmp_path) -> None:
    path = tmp_path / "pricing.json"
    path.write_text(json.dumps({
        "schema_version": 1,
        "snapshot_id": "test-v1",
        "captured_at": "2026-09-13",
        "currency": "USD",
        "source_url": "https://example.invalid/pricing",
        "scope": "test prices",
        "models": [{
            "provider": "test",
            "model": "model",
            "input_usd_per_million": "2",
            "output_usd_per_million": "8",
            "cache_read_usd_per_million": "0.2",
            "cache_write_usd_per_million": "2.5",
        }],
    }), encoding="utf-8")
    snapshot = load_pricing_snapshot(path)

    result = estimate_run_cost([_event("test", "model", {
        "input_tokens": 1_000_000,
        "output_tokens": 100_000,
        "cache_read_tokens": 200_000,
        "cache_write_tokens": 100_000,
    })], snapshot)

    assert result["status"] == "estimated"
    assert result["estimated_cost_usd"] == 2.49
    assert result["breakdown"] == {
        "input_usd": 1.4,
        "output_usd": 0.8,
        "cache_read_usd": 0.04,
        "cache_write_usd": 0.25,
    }


def test_unknown_model_never_becomes_zero_cost() -> None:
    snapshot = load_pricing_snapshot()

    result = estimate_run_cost([_event("other", "model", {
        "input_tokens": 10,
        "output_tokens": 2,
        "cache_read_tokens": None,
        "cache_write_tokens": None,
    })], snapshot)

    assert result["status"] == "unknown"
    assert result["estimated_cost_usd"] is None
    assert result["partial_estimated_cost_usd"] is None
    assert result["unknown_models"] == ["other/model"]
    assert set(result["breakdown"].values()) == {None}


def test_mixed_prices_are_marked_partial() -> None:
    snapshot = load_pricing_snapshot()
    usage = {
        "input_tokens": 1_000_000,
        "output_tokens": 1_000_000,
        "cache_read_tokens": None,
        "cache_write_tokens": None,
    }

    result = estimate_run_cost([
        _event("openai", "gpt-5.3-codex", usage),
        _event("other", "model", usage),
    ], snapshot)

    assert result["status"] == "partial"
    assert result["estimated_cost_usd"] is None
    assert result["partial_estimated_cost_usd"] == 15.75
    assert result["priced_model_calls"] == 1
    assert result["unpriced_model_calls"] == 1
