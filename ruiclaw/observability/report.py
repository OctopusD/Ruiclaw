"""Pure report projection for RuiClaw run evidence."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, cast

from ruiclaw.observability.pricing import (
    PricingSnapshot,
    estimate_run_cost,
    load_pricing_snapshot,
)

SCHEMA_VERSION = 1


def rebuild_report(
    run_dir: Path,
    *,
    pricing_snapshot: PricingSnapshot | None = None,
) -> dict[str, Any]:
    """Rebuild aggregate metrics solely from a Run manifest and event ledger."""
    manifest = _read_json_object(run_dir / "manifest.json")
    events = _read_events(run_dir / "events.jsonl")
    model_calls = [event for event in events if event.get("event_type") == "model_call_finished"]
    provider_calls = [
        event for event in events if event.get("event_type") == "provider_call_finished"
    ]
    usage_calls = provider_calls or model_calls
    contexts = [event for event in events if event.get("event_type") == "model_context_built"]
    tool_calls = [event for event in events if event.get("event_type") == "tool_call_finished"]
    usage: dict[str, int | None] = {
        "input_tokens": 0,
        "output_tokens": 0,
        "total_tokens": 0,
        "cache_read_tokens": 0,
        "cache_write_tokens": 0,
        "reported_tokens": 0,
        "estimated_tokens": 0,
        "request_count": 0,
    }
    for event in usage_calls:
        payload_value = cast(object, event.get("payload"))
        if not isinstance(payload_value, dict):
            continue
        payload = cast(dict[str, Any], payload_value)
        call_usage_value = cast(object, payload.get("usage"))
        if not isinstance(call_usage_value, dict):
            continue
        call_usage = cast(dict[str, Any], call_usage_value)
        for key in (
            "input_tokens",
            "output_tokens",
            "total_tokens",
            "reported_tokens",
            "estimated_tokens",
            "request_count",
        ):
            value = cast(object, call_usage.get(key))
            if isinstance(value, int) and not isinstance(value, bool):
                current = usage[key]
                usage[key] = (current or 0) + value
        for key in ("cache_read_tokens", "cache_write_tokens"):
            value = cast(object, call_usage.get(key))
            if value is None:
                usage[key] = None
            elif (
                isinstance(value, int)
                and not isinstance(value, bool)
                and usage[key] is not None
            ):
                usage[key] = cast(int, usage[key]) + value
    started = _parse_timestamp(manifest.get("started_at"))
    finished = _parse_timestamp(manifest.get("finished_at"))
    return {
        "schema_version": SCHEMA_VERSION,
        "run_id": manifest.get("run_id"),
        "status": manifest.get("status"),
        "stop_reason": manifest.get("stop_reason"),
        "duration_ms": (
            max(0, round((finished - started) * 1000))
            if started is not None and finished is not None
            else None
        ),
        "model_calls": len(model_calls),
        "failed_model_calls": _failed_count(model_calls),
        "provider_calls": len(provider_calls),
        "failed_provider_calls": _failed_count(provider_calls),
        "tool_calls": len(tool_calls),
        "failed_tool_calls": _failed_count(tool_calls),
        "tool_governance": _tool_governance_report(tool_calls),
        "context": _context_report(contexts),
        "usage": usage,
        "cost": {
            **estimate_run_cost(
                usage_calls,
                pricing_snapshot or load_pricing_snapshot(),
            ),
            "basis": "provider_calls" if provider_calls else "logical_model_calls",
        },
    }


def _failed_count(events: list[dict[str, Any]]) -> int:
    failed = 0
    for event in events:
        payload_value = cast(object, event.get("payload"))
        if not isinstance(payload_value, dict):
            continue
        payload = cast(dict[str, Any], payload_value)
        if payload.get("status") != "succeeded":
            failed += 1
    return failed


def _context_report(events: list[dict[str, Any]]) -> dict[str, Any]:
    totals: list[int] = []
    ratios: list[float] = []
    compacted = 0
    max_source_tokens: dict[str, int] = {}
    for event in events:
        payload_value = cast(object, event.get("payload"))
        if not isinstance(payload_value, dict):
            continue
        payload = cast(dict[str, Any], payload_value)
        total = cast(object, payload.get("total_tokens"))
        if isinstance(total, int) and not isinstance(total, bool):
            totals.append(total)
        ratio = cast(object, payload.get("utilization_ratio"))
        if isinstance(ratio, int | float) and not isinstance(ratio, bool):
            ratios.append(float(ratio))
        if payload.get("compacted") is True:
            compacted += 1
        source_tokens_value = cast(object, payload.get("source_tokens"))
        if isinstance(source_tokens_value, dict):
            for source, value in cast(dict[object, object], source_tokens_value).items():
                if (
                    isinstance(source, str)
                    and isinstance(value, int)
                    and not isinstance(value, bool)
                ):
                    max_source_tokens[source] = max(max_source_tokens.get(source, 0), value)
    return {
        "requests": len(events),
        "max_total_tokens": max(totals, default=0),
        "max_utilization_ratio": max(ratios) if ratios else None,
        "compacted_requests": compacted,
        "max_source_tokens": dict(sorted(max_source_tokens.items())),
        "memory_retrieval": _memory_retrieval_report(events),
    }


def _memory_retrieval_report(events: list[dict[str, Any]]) -> dict[str, int]:
    max_candidates = 0
    max_selected = 0
    max_stale_rejected = 0
    requests_with_hits = 0
    for event in events:
        payload_value = cast(object, event.get("payload"))
        if not isinstance(payload_value, dict):
            continue
        retrieval_value = cast(dict[str, Any], payload_value).get("memory_retrieval")
        if not isinstance(retrieval_value, dict):
            continue
        retrieval = cast(dict[object, object], retrieval_value)
        candidates = _non_negative_int(retrieval.get("candidate_count"))
        selected = _non_negative_int(retrieval.get("selected_count"))
        stale = _non_negative_int(retrieval.get("stale_rejected_count"))
        max_candidates = max(max_candidates, candidates)
        max_selected = max(max_selected, selected)
        max_stale_rejected = max(max_stale_rejected, stale)
        requests_with_hits += int(selected > 0)
    return {
        "requests_with_hits": requests_with_hits,
        "max_candidates": max_candidates,
        "max_selected": max_selected,
        "max_stale_rejected": max_stale_rejected,
    }


def _non_negative_int(value: object) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else 0


def _tool_governance_report(events: list[dict[str, Any]]) -> dict[str, Any]:
    effects: dict[str, int] = {}
    queue_times: list[int] = []
    execution_times: list[int] = []
    timed_out = 0
    cancelled = 0
    batches: set[str] = set()
    for event in events:
        payload_value = cast(object, event.get("payload"))
        if not isinstance(payload_value, dict):
            continue
        payload = cast(dict[str, Any], payload_value)
        effect = payload.get("effect_type")
        effect_name = effect if isinstance(effect, str) and effect else "unknown"
        effects[effect_name] = effects.get(effect_name, 0) + 1
        queue_ms = payload.get("queue_ms")
        if isinstance(queue_ms, int) and not isinstance(queue_ms, bool):
            queue_times.append(queue_ms)
        duration_ms = payload.get("duration_ms")
        if isinstance(duration_ms, int) and not isinstance(duration_ms, bool):
            execution_times.append(duration_ms)
        status = payload.get("status")
        timed_out += int(status == "timed_out")
        cancelled += int(status == "cancelled")
        batch_id = payload.get("batch_id")
        if isinstance(batch_id, str) and batch_id:
            batches.add(batch_id)
    return {
        "effect_counts": dict(sorted(effects.items())),
        "batch_count": len(batches),
        "timed_out_calls": timed_out,
        "cancelled_calls": cancelled,
        "max_queue_ms": max(queue_times, default=0),
        "max_execution_ms": max(execution_times, default=0),
    }


def _read_json_object(path: Path) -> dict[str, Any]:
    value: object = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object in {path}")
    return cast(dict[str, Any], value)


def _read_events(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    events: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        value: object = json.loads(line)
        if isinstance(value, dict):
            events.append(cast(dict[str, Any], value))
    return events


def _parse_timestamp(value: object) -> float | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value).timestamp()
    except ValueError:
        return None
