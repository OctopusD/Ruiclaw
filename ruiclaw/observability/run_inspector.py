"""Read-only projections over RuiClaw's local run ledger."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, TypedDict, cast

from ruiclaw.observability.report import rebuild_report
from ruiclaw.utils.run_records import safe_run_record_name

MAX_DETAIL_EVENTS = 500


class RunSummary(TypedDict):
    run_id: str
    status: str
    model: str | None
    session_key: str
    started_at: str | None
    duration_ms: int | None
    total_tokens: int
    cost_status: str
    estimated_cost_usd: float | None
    model_calls: int
    provider_calls: int
    tool_calls: int


class RunInspector:
    """Query run records without mutating or trusting their persisted payloads."""

    def __init__(self, workspace: Path) -> None:
        self.runs_dir = workspace.expanduser().resolve() / ".ruiclaw" / "runs"

    def list_runs(self, *, limit: int = 50) -> list[RunSummary]:
        bounded_limit = max(1, min(limit, 200))
        if not self.runs_dir.is_dir():
            return []
        rows: list[RunSummary] = []
        for run_dir in self.runs_dir.iterdir():
            if run_dir.is_symlink() or not run_dir.is_dir():
                continue
            try:
                manifest = _read_object(run_dir / "manifest.json")
                report = _report(run_dir)
                events = _read_events(run_dir / "events.jsonl", limit=MAX_DETAIL_EVENTS)
                rows.append(_summary(manifest, report, events))
            except (OSError, ValueError, json.JSONDecodeError):
                continue
        rows.sort(key=lambda row: row["started_at"] or "", reverse=True)
        return rows[:bounded_limit]

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        run_dir = self._resolve_run_dir(run_id)
        if run_dir is None or not run_dir.is_dir():
            return None
        manifest = _read_object(run_dir / "manifest.json")
        report = _report(run_dir)
        events = _read_events(run_dir / "events.jsonl", limit=MAX_DETAIL_EVENTS)
        total_events = _line_count(run_dir / "events.jsonl")
        return {
            "summary": _summary(manifest, report, events),
            "manifest": manifest,
            "report": report,
            "events": events,
            "event_count": total_events,
            "events_truncated": total_events > len(events),
        }

    def _resolve_run_dir(self, run_id: str) -> Path | None:
        if not run_id or safe_run_record_name(run_id) != run_id:
            return None
        candidate = self.runs_dir / run_id
        if candidate.is_symlink():
            return None
        try:
            resolved = candidate.resolve(strict=False)
        except OSError:
            return None
        return resolved if resolved.parent == self.runs_dir else None


def _read_object(path: Path) -> dict[str, Any]:
    value: object = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object in {path}")
    return cast(dict[str, Any], value)


def _read_events(path: Path, *, limit: int) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    events: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines()[-limit:]:
        value: object = json.loads(line)
        if isinstance(value, dict):
            events.append(cast(dict[str, Any], value))
    return events


def _line_count(path: Path) -> int:
    if not path.is_file():
        return 0
    with open(path, encoding="utf-8") as handle:
        return sum(1 for line in handle if line.strip())


def _report(run_dir: Path) -> dict[str, Any]:
    report_path = run_dir / "report.json"
    if report_path.is_file():
        return _read_object(report_path)
    return rebuild_report(run_dir)


def _summary(
    manifest: dict[str, Any],
    report: dict[str, Any],
    events: list[dict[str, Any]],
) -> RunSummary:
    usage_value = cast(object, report.get("usage"))
    usage = cast(dict[str, object], usage_value) if isinstance(usage_value, dict) else {}
    cost_value = cast(object, report.get("cost"))
    cost = cast(dict[str, object], cost_value) if isinstance(cost_value, dict) else {}
    model: str | None = None
    for event in events:
        if event.get("event_type") != "model_call_started":
            continue
        payload_value = cast(object, event.get("payload"))
        if not isinstance(payload_value, dict):
            continue
        payload = cast(dict[str, object], payload_value)
        model_value = payload.get("model")
        if isinstance(model_value, str):
            model = model_value
            break
    return {
        "run_id": _text(manifest.get("run_id")),
        "status": _text(manifest.get("status")) or "unknown",
        "model": model,
        "session_key": _text(manifest.get("session_key")),
        "started_at": _optional_text(manifest.get("started_at")),
        "duration_ms": _optional_int(report.get("duration_ms")),
        "total_tokens": _integer(usage.get("total_tokens")),
        "cost_status": _text(cost.get("status")) or "unknown",
        "estimated_cost_usd": _optional_float(cost.get("estimated_cost_usd")),
        "model_calls": _integer(report.get("model_calls")),
        "provider_calls": _integer(report.get("provider_calls")),
        "tool_calls": _integer(report.get("tool_calls")),
    }


def _text(value: object) -> str:
    return value if isinstance(value, str) else ""


def _optional_text(value: object) -> str | None:
    return value if isinstance(value, str) else None


def _integer(value: object) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def _optional_int(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _optional_float(value: object) -> float | None:
    if isinstance(value, int | float) and not isinstance(value, bool):
        return float(value)
    return None
