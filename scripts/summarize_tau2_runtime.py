#!/usr/bin/env python3
"""Aggregate RuiClaw Run Ledgers belonging to one Tau benchmark result file."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _percentile(values: list[int], percentile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * percentile / 100
    lower, upper = int(position), min(int(position) + 1, len(ordered) - 1)
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def _latest_session_reports(task_dir: Path) -> list[dict[str, Any]]:
    """Return reports from the latest complete session for one Tau task workspace."""
    grouped: dict[str, list[tuple[str, Path]]] = {}
    for manifest_path in task_dir.glob(".ruiclaw/runs/*/manifest.json"):
        manifest = _read_json(manifest_path)
        session_key = manifest.get("session_key")
        finished_at = manifest.get("finished_at")
        if not isinstance(session_key, str) or not isinstance(finished_at, str):
            continue
        report_path = manifest_path.with_name("report.json")
        if report_path.exists():
            grouped.setdefault(session_key, []).append((finished_at, report_path))
    if not grouped:
        return []
    latest = max(grouped.values(), key=lambda reports: max(item[0] for item in reports))
    return [_read_json(path) for _, path in latest]


def summarize(results_path: Path, workspace_root: Path) -> dict[str, Any]:
    result = _read_json(results_path)
    domain = str(result["domain"])
    reports: list[dict[str, Any]] = []
    selected_sessions = 0
    for task in result.get("tasks", []):
        task_id = str(task["task_id"])
        task_reports = _latest_session_reports(workspace_root / domain / task_id)
        if task_reports:
            selected_sessions += 1
            reports.extend(task_reports)

    durations = [int(report.get("duration_ms", 0)) for report in reports]
    usage = [report.get("usage", {}) for report in reports]
    tool_calls = sum(int(report.get("tool_calls", 0)) for report in reports)
    failed_tools = sum(int(report.get("failed_tool_calls", 0)) for report in reports)
    costs = Counter(str(report.get("cost", {}).get("status", "unknown")) for report in reports)
    ledgers_complete = sum(
        1
        for report in reports
        if report.get("run_id") and report.get("status") and report.get("usage") is not None
    )
    reward_rows = list(result.get("tasks", []))
    reward_successes = sum(1 for row in reward_rows if row.get("reward") == 1.0)
    return {
        "benchmark": result.get("benchmark"),
        "domain": domain,
        "split": result.get("split"),
        "seed": result.get("seed"),
        "task_count": len(reward_rows),
        "reward_successes": reward_successes,
        "reward_success_rate": reward_successes / len(reward_rows) if reward_rows else None,
        "selected_task_sessions": selected_sessions,
        "run_count": len(reports),
        "run_success_rate": (
            sum(report.get("status") == "succeeded" for report in reports) / len(reports)
            if reports else None
        ),
        "run_duration_ms": {
            "p50": _percentile(durations, 50),
            "p95": _percentile(durations, 95),
            "max": max(durations) if durations else None,
        },
        "model_calls": {
            "total": sum(int(report.get("model_calls", 0)) for report in reports),
            "failed": sum(int(report.get("failed_model_calls", 0)) for report in reports),
            "per_task": (
                sum(int(report.get("model_calls", 0)) for report in reports) / len(reward_rows)
                if reward_rows else None
            ),
        },
        "usage": {
            "input_tokens": sum(int(row.get("input_tokens", 0)) for row in usage),
            "output_tokens": sum(int(row.get("output_tokens", 0)) for row in usage),
            "cache_read_tokens": sum(int(row.get("cache_read_tokens", 0) or 0) for row in usage),
        },
        "internal_tools": {
            "calls": tool_calls,
            "failed": failed_tools,
            "success_rate": (tool_calls - failed_tools) / tool_calls if tool_calls else None,
            "timed_out": sum(
                int(report.get("tool_governance", {}).get("timed_out_calls", 0))
                for report in reports
            ),
        },
        "external_tool_turns": sum(
            report.get("stop_reason") == "awaiting_external_tools" for report in reports
        ),
        "cost_statuses": dict(costs),
        "ledger_complete_rate": ledgers_complete / len(reports) if reports else None,
        "notes": {
            "external_tool_turns": "One turn may request multiple Tau tools; exact external call and success counts require adapter ledger events.",
            "cost": "Unknown pricing is preserved as unknown rather than reported as zero.",
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results", type=Path, required=True)
    parser.add_argument(
        "--workspace-root",
        type=Path,
        default=Path("tau2-bench/benchmarks/results/tau2/workspaces"),
    )
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    summary = summarize(args.results, args.workspace_root)
    output = args.output or args.results.with_name(f"{args.results.stem}-runtime.json")
    output.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(f"Saved: {output}")


if __name__ == "__main__":
    main()
