"""Append-only, local-first audit ledger for one agent run."""

from __future__ import annotations

import asyncio
import errno
import json
import os
import time
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast
from uuid import uuid4

from ruiclaw.agent.hook import AgentHook, AgentHookContext, AgentRunHookContext
from ruiclaw.observability.report import SCHEMA_VERSION, rebuild_report
from ruiclaw.utils.run_records import safe_run_record_name

if TYPE_CHECKING:
    from ruiclaw.llm_usage.models import LLMCallRecord
    from ruiclaw.providers.base import ToolCallRequest


def _timestamp() -> str:
    return datetime.now(UTC).isoformat()


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        with open(tmp_path, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, path)
        with suppress(PermissionError):
            directory_fd = os.open(str(path.parent), os.O_RDONLY)
            try:
                try:
                    os.fsync(directory_fd)
                except OSError as exc:
                    if exc.errno != errno.EINVAL:
                        raise
            finally:
                os.close(directory_fd)
    except BaseException:
        tmp_path.unlink(missing_ok=True)
        raise


@dataclass(slots=True)
class RunLedger:
    """Own the append-only evidence and derived report for one run."""

    runs_dir: Path
    run_id: str
    session_key: str
    turn_id: str
    parent_run_id: str | None = None
    trigger: str = "user"
    _sequence: int = field(init=False, default=0)
    _lock: asyncio.Lock = field(init=False, default_factory=asyncio.Lock)

    @property
    def run_dir(self) -> Path:
        name = safe_run_record_name(self.run_id)
        if not name:
            raise ValueError("run_id must contain a filesystem-safe character")
        return self.runs_dir / name

    @property
    def manifest_path(self) -> Path:
        return self.run_dir / "manifest.json"

    @property
    def events_path(self) -> Path:
        return self.run_dir / "events.jsonl"

    @property
    def report_path(self) -> Path:
        return self.run_dir / "report.json"

    async def start(self) -> None:
        """Create a new manifest, or reopen an unfinished continuation Run."""
        async with self._lock:
            self.run_dir.mkdir(parents=True, exist_ok=True)
            self._sequence = _last_sequence(self.events_path)
            if self.manifest_path.exists():
                return
            started_at = _timestamp()
            _atomic_write_json(self.manifest_path, {
                "schema_version": SCHEMA_VERSION,
                "run_id": self.run_id,
                "session_key": self.session_key,
                "turn_id": self.turn_id,
                "parent_run_id": self.parent_run_id,
                "trigger": self.trigger,
                "status": "running",
                "started_at": started_at,
                "finished_at": None,
                "stop_reason": None,
            })
            self._append_unlocked("run_started", {"trigger": self.trigger})

    async def append(self, event_type: str, payload: dict[str, Any] | None = None) -> None:
        async with self._lock:
            self._append_unlocked(event_type, payload or {})

    async def finish(self, status: str, *, stop_reason: str | None = None) -> None:
        async with self._lock:
            manifest = _read_json_object(self.manifest_path)
            if manifest.get("status") != "running":
                return
            finished_at = _timestamp()
            self._append_unlocked(
                "run_finished",
                {"status": status, "stop_reason": stop_reason},
            )
            manifest.update({
                "status": status,
                "finished_at": finished_at,
                "stop_reason": stop_reason,
            })
            _atomic_write_json(self.manifest_path, manifest)
            _atomic_write_json(self.report_path, rebuild_report(self.run_dir))

    def _append_unlocked(self, event_type: str, payload: dict[str, Any]) -> None:
        self._sequence += 1
        event = {
            "schema_version": SCHEMA_VERSION,
            "event_id": f"{self.run_id}:{self._sequence}",
            "event_type": event_type,
            "occurred_at": _timestamp(),
            "session_key": self.session_key,
            "turn_id": self.turn_id,
            "run_id": self.run_id,
            "parent_run_id": self.parent_run_id,
            "sequence": self._sequence,
            "payload": payload,
        }
        with open(self.events_path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(event, ensure_ascii=False, separators=(",", ":")))
            handle.write("\n")
            handle.flush()


class RunLedgerHook(AgentHook):
    """Record model and tool boundaries without retaining their content."""

    def __init__(
        self,
        ledger: RunLedger,
        *,
        provider: str = "unknown",
        model: str,
        physical_calls: list[LLMCallRecord] | None = None,
    ) -> None:
        super().__init__()
        self.ledger = ledger
        runtime_provider = cast(object, provider)
        runtime_model = cast(object, model)
        self.provider = (
            runtime_provider
            if isinstance(runtime_provider, str) and runtime_provider
            else "unknown"
        )
        self.model = (
            runtime_model if isinstance(runtime_model, str) and runtime_model else "unknown"
        )
        self.physical_calls = physical_calls
        self._physical_call_index = 0
        self._model_started_at: float | None = None
        self._model_call_id: str | None = None
        self._tool_started_at: dict[str, tuple[str, int, float]] = {}
        self._tool_effects: dict[str, str] = {}

    async def before_model_call(self, context: AgentHookContext) -> None:
        self._model_started_at = time.perf_counter()
        self._model_call_id = uuid4().hex
        metrics = context.model_request
        if metrics is not None:
            await self.ledger.append("model_context_built", {
                "model_call_id": self._model_call_id,
                "iteration": context.iteration,
                "message_count": metrics.message_count,
                "role_tokens": {
                    "system": metrics.system_tokens,
                    "user": metrics.user_tokens,
                    "assistant": metrics.assistant_tokens,
                    "tool_result": metrics.tool_result_tokens,
                    "other": metrics.other_tokens,
                },
                "tool_definition_tokens": metrics.tool_definition_tokens,
                "total_tokens": metrics.total_tokens,
                "token_source": metrics.token_source,
                "context_window_tokens": metrics.context_window_tokens,
                "input_budget_tokens": metrics.input_budget_tokens,
                "utilization_ratio": metrics.utilization_ratio,
                "compacted": metrics.compacted,
                "runtime_context_sources": list(metrics.runtime_context_sources),
                "source_tokens": dict(metrics.source_tokens),
                "memory_retrieval": {
                    "candidate_count": metrics.memory_candidate_count,
                    "selected_count": metrics.memory_selected_count,
                    "stale_rejected_count": metrics.memory_stale_rejected_count,
                    "selected": [
                        {
                            "memory_id": memory_id,
                            "score": score,
                            "reasons": list(reasons),
                            "source": source,
                        }
                        for memory_id, score, reasons, source in metrics.memory_selected
                    ],
                },
            })
        await self.ledger.append("model_call_started", {
            "model_call_id": self._model_call_id,
            "iteration": context.iteration,
            "provider": self.provider,
            "model": self.model,
        })

    async def after_model_call(self, context: AgentHookContext) -> None:
        duration_ms = _elapsed_ms(self._model_started_at)
        response = context.response
        await self._flush_physical_calls()
        await self.ledger.append("model_call_finished", {
            "model_call_id": self._model_call_id,
            "iteration": context.iteration,
            "provider": self.provider,
            "model": self.model,
            "status": "failed" if response and response.finish_reason == "error" else "succeeded",
            "finish_reason": response.finish_reason if response else None,
            "duration_ms": duration_ms,
            "usage": context.usage.to_dict() if context.usage is not None else None,
        })
        self._model_started_at = None
        self._model_call_id = None

    async def on_model_call_error(
        self,
        context: AgentHookContext,
        error: BaseException,
    ) -> None:
        await self._flush_physical_calls()
        await self.ledger.append("model_call_finished", {
            "model_call_id": self._model_call_id,
            "iteration": context.iteration,
            "provider": self.provider,
            "model": self.model,
            "status": "cancelled" if isinstance(error, asyncio.CancelledError) else "failed",
            "error_kind": type(error).__name__,
            "duration_ms": _elapsed_ms(self._model_started_at),
            "usage": None,
        })
        self._model_started_at = None
        self._model_call_id = None

    async def before_execute_tools(self, context: AgentHookContext) -> None:
        for tool_call in context.tool_calls:
            self._tool_started_at[tool_call.id] = (
                tool_call.name,
                context.iteration,
                time.perf_counter(),
            )
            await self.ledger.append("tool_call_started", {
                "iteration": context.iteration,
                "tool_call_id": tool_call.id,
                "tool_name": tool_call.name,
            })

    async def before_execute_tool(
        self,
        context: AgentHookContext,
        tool_call: ToolCallRequest,
        tool: Any,
        params: Any,
    ) -> None:
        del context, params
        effect_type = getattr(tool, "effect_type", "unknown")
        self._tool_effects[tool_call.id] = (
            effect_type if isinstance(effect_type, str) else "unknown"
        )

    async def after_iteration(self, context: AgentHookContext) -> None:
        for tool_call, event in zip(context.tool_calls, context.tool_events):
            started = self._tool_started_at.pop(tool_call.id, None)
            event_status = event.get("status")
            governance = context.tool_governance.get(tool_call.id, {})
            effect_type = governance.get(
                "effect_type",
                self._tool_effects.pop(tool_call.id, "unknown"),
            )
            measured_duration = _parse_non_negative_int(governance.get("duration_ms"))
            await self.ledger.append("tool_call_finished", {
                "iteration": context.iteration,
                "tool_call_id": tool_call.id,
                "tool_name": tool_call.name,
                "status": (
                    "succeeded"
                    if event_status == "ok"
                    else "timed_out" if event_status == "timeout" else "failed"
                ),
                "duration_ms": (
                    measured_duration
                    if measured_duration is not None
                    else _elapsed_ms(started[2] if started is not None else None)
                ),
                "queue_ms": _parse_non_negative_int(governance.get("queue_ms")),
                "timeout_seconds": _parse_positive_float(governance.get("timeout_s")),
                "batch_id": governance.get("batch_id"),
                "effect_type": effect_type,
                "error_kind": (
                    "timeout" if event_status == "timeout"
                    else "tool_error" if event_status != "ok" else None
                ),
            })

    async def on_finally(self, context: AgentRunHookContext) -> None:
        await self._flush_physical_calls()
        for tool_call_id, (tool_name, iteration, started_at) in list(
            self._tool_started_at.items()
        ):
            await self.ledger.append("tool_call_finished", {
                "iteration": iteration,
                "tool_call_id": tool_call_id,
                "tool_name": tool_name,
                "status": "cancelled",
                "duration_ms": _elapsed_ms(started_at),
                "effect_type": self._tool_effects.pop(tool_call_id, "unknown"),
                "error_kind": "cancelled",
            })
            self._tool_started_at.pop(tool_call_id, None)

    async def _flush_physical_calls(self) -> None:
        if self.physical_calls is None:
            return
        pending = self.physical_calls[self._physical_call_index:]
        self._physical_call_index = len(self.physical_calls)
        for call in pending:
            await self.ledger.append("provider_call_finished", {
                "started_at_ms": call.started_at_ms,
                "duration_ms": call.duration_ms,
                "provider": call.provider,
                "model": call.model,
                "source": call.source,
                "stream": call.stream,
                "status": (
                    "failed"
                    if call.error_kind is not None or call.finish_reason in {"error", "cancelled"}
                    else "succeeded"
                ),
                "finish_reason": call.finish_reason,
                "error_status_code": call.error_status_code,
                "error_kind": call.error_kind,
                "usage": call.usage.to_dict() if call.usage is not None else None,
            })


def _read_json_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object in {path}")
    return cast(dict[str, Any], value)


def _parse_non_negative_int(value: object) -> int | None:
    if isinstance(value, int) and not isinstance(value, bool):
        return value if value >= 0 else None
    if not isinstance(value, str):
        return None
    try:
        parsed = int(value)
    except ValueError:
        return None
    return parsed if parsed >= 0 else None


def _parse_positive_float(value: object) -> float | None:
    if isinstance(value, int | float) and not isinstance(value, bool):
        return float(value) if value > 0 else None
    if not isinstance(value, str):
        return None
    try:
        parsed = float(value)
    except ValueError:
        return None
    return parsed if parsed > 0 else None


def _read_events(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    events: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        value = json.loads(line)
        if isinstance(value, dict):
            events.append(cast(dict[str, Any], value))
    return events


def _last_sequence(path: Path) -> int:
    events = _read_events(path)
    if not events:
        return 0
    sequence = events[-1].get("sequence")
    return sequence if isinstance(sequence, int) and not isinstance(sequence, bool) else 0


def _elapsed_ms(started_at: float | None) -> int | None:
    if started_at is None:
        return None
    return max(0, round((time.perf_counter() - started_at) * 1000))
