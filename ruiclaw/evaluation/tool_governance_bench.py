"""Deterministic benchmark for RuiClaw tool execution governance."""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast
from uuid import uuid4

from ruiclaw.agent.hook import AgentHookContext
from ruiclaw.agent.tools.base import Tool, ToolEffect
from ruiclaw.agent.tools.execution import execute_tool_calls
from ruiclaw.agent.tools.registry import ToolRegistry
from ruiclaw.observability.run_ledger import RunLedger, RunLedgerHook
from ruiclaw.providers.base import ToolCallRequest

DEFAULT_TOOL_GOVERNANCE_ARTIFACT_PATH = Path(
    "benchmarks/results/ruiclaw-tool-governance-v1/result.json"
)
DEFAULT_TOOL_GOVERNANCE_WORKSPACE_ROOT = Path(
    "benchmarks/results/ruiclaw-tool-governance-v1/workspace"
)


class _ProbeTool(Tool):
    def __init__(
        self,
        name: str,
        *,
        effect_type: ToolEffect,
        delay_s: float,
        state: dict[str, Any],
    ) -> None:
        self._name = name
        self._effect: ToolEffect = effect_type
        self.delay_s = delay_s
        self.state = state

    @property
    def name(self) -> str:
        return self._name

    @property
    def description(self) -> str:
        return "Deterministic tool-governance benchmark probe."

    @property
    def parameters(self) -> dict[str, Any]:
        return {"type": "object", "properties": {}, "required": []}

    @property
    def read_only(self) -> bool:
        return self._effect == "read_only"

    @property
    def effect_type(self) -> ToolEffect:
        return self._effect

    async def execute(self, **kwargs: Any) -> str:
        del kwargs
        active = int(self.state.get("active", 0)) + 1
        self.state["active"] = active
        self.state["peak"] = max(int(self.state.get("peak", 0)), active)
        if self.effect_type != "read_only" and active > 1:
            self.state["write_overlaps"] = int(self.state.get("write_overlaps", 0)) + 1
        try:
            await asyncio.sleep(self.delay_s)
            return self.name
        finally:
            self.state["active"] = int(self.state["active"]) - 1
            self.state.setdefault("finished", []).append(self.name)


async def _execute_case(
    workspace: Path,
    case_id: str,
    tools: list[_ProbeTool],
    *,
    concurrent: bool,
    max_concurrency: int,
    timeout_s: float,
) -> tuple[list[Any], list[dict[str, str]], dict[str, Any], int]:
    registry = ToolRegistry()
    calls: list[ToolCallRequest] = []
    for index, tool in enumerate(tools):
        registry.register(tool)
        calls.append(ToolCallRequest(id=f"{case_id}-{index}", name=tool.name, arguments={}))
    ledger = RunLedger(
        workspace / ".ruiclaw" / "runs",
        uuid4().hex,
        f"bench:{case_id}",
        f"bench:{case_id}:1",
        trigger="benchmark",
    )
    await ledger.start()
    hook = RunLedgerHook(ledger, model="tool-governance-v1")
    context = AgentHookContext(iteration=0, messages=[], tool_calls=calls)
    await hook.before_execute_tools(context)
    started_at = time.perf_counter()
    results, events = await execute_tool_calls(
        registry,
        calls,
        concurrent=concurrent,
        max_concurrency=max_concurrency,
        timeout_s=timeout_s,
        external_lookup_counts={},
        workspace_violation_counts={},
        hook=hook,
        context=context,
    )
    elapsed_ms = round((time.perf_counter() - started_at) * 1000)
    context.tool_results = results
    context.tool_events = events
    await hook.after_iteration(context)
    await ledger.finish("succeeded", stop_reason="completed")
    report_value: object = json.loads(ledger.report_path.read_text(encoding="utf-8"))
    if not isinstance(report_value, dict):
        raise AssertionError("tool governance report is not an object")
    return results, events, cast(dict[str, Any], report_value), elapsed_ms


class ToolGovernanceBenchmarkEvaluator:
    def __init__(
        self,
        artifact_path: Path = DEFAULT_TOOL_GOVERNANCE_ARTIFACT_PATH,
        workspace_root: Path = DEFAULT_TOOL_GOVERNANCE_WORKSPACE_ROOT,
    ) -> None:
        self.artifact_path = artifact_path.resolve()
        self.workspace_root = workspace_root.resolve()

    async def run(self) -> dict[str, object]:
        if self.workspace_root.exists():
            shutil.rmtree(self.workspace_root)
        self.workspace_root.mkdir(parents=True)

        serial_state: dict[str, Any] = {}
        serial_tools = [
            _ProbeTool(f"serial_read_{index}", effect_type="read_only", delay_s=0.04,
                       state=serial_state)
            for index in range(4)
        ]
        _serial_results, _serial_events, _serial_report, serial_ms = await _execute_case(
            self.workspace_root,
            "serial_reads",
            serial_tools,
            concurrent=False,
            max_concurrency=2,
            timeout_s=1,
        )

        parallel_state: dict[str, Any] = {}
        parallel_tools = [
            _ProbeTool(f"parallel_read_{index}", effect_type="read_only", delay_s=0.04,
                       state=parallel_state)
            for index in range(4)
        ]
        _parallel_results, _parallel_events, parallel_report, parallel_ms = (
            await _execute_case(
                self.workspace_root,
                "bounded_parallel_reads",
                parallel_tools,
                concurrent=True,
                max_concurrency=2,
                timeout_s=1,
            )
        )
        speedup = serial_ms / max(parallel_ms, 1)
        parallel_passed = parallel_state.get("peak") == 2 and speedup >= 1.5

        write_state: dict[str, Any] = {}
        write_tools = [
            _ProbeTool(f"write_{index}", effect_type="workspace_write", delay_s=0.02,
                       state=write_state)
            for index in range(3)
        ]
        write_results, _write_events, write_report, _write_ms = await _execute_case(
            self.workspace_root,
            "serialized_writes",
            write_tools,
            concurrent=True,
            max_concurrency=3,
            timeout_s=1,
        )
        write_passed = (
            write_state.get("peak") == 1
            and write_state.get("write_overlaps", 0) == 0
            and write_results == [tool.name for tool in write_tools]
        )

        timeout_state: dict[str, Any] = {}
        timeout_tool = _ProbeTool(
            "timeout_probe",
            effect_type="read_only",
            delay_s=1,
            state=timeout_state,
        )
        _timeout_results, timeout_events, timeout_report, _timeout_ms = await _execute_case(
            self.workspace_root,
            "timeout_cleanup",
            [timeout_tool],
            concurrent=True,
            max_concurrency=1,
            timeout_s=0.02,
        )
        timeout_governance_value = timeout_report.get("tool_governance")
        timeout_governance = (
            cast(dict[str, Any], timeout_governance_value)
            if isinstance(timeout_governance_value, dict)
            else {}
        )
        timeout_passed = (
            timeout_events[0].get("status") == "timeout"
            and timeout_state.get("active") == 0
            and timeout_state.get("finished") == ["timeout_probe"]
            and timeout_governance.get("timed_out_calls") == 1
        )

        write_governance_value = write_report.get("tool_governance")
        write_governance = (
            cast(dict[str, Any], write_governance_value)
            if isinstance(write_governance_value, dict)
            else {}
        )

        rows: list[dict[str, object]] = [
            {
                "id": "bounded_parallel_reads",
                "passed": parallel_passed,
                "serial_elapsed_ms": serial_ms,
                "parallel_elapsed_ms": parallel_ms,
                "speedup": round(speedup, 3),
                "configured_limit": 2,
                "observed_peak": parallel_state.get("peak", 0),
                "run_id": parallel_report.get("run_id"),
            },
            {
                "id": "serialized_writes",
                "passed": write_passed,
                "write_overlaps": write_state.get("write_overlaps", 0),
                "observed_peak": write_state.get("peak", 0),
                "effect_counts": write_governance.get("effect_counts", {}),
                "run_id": write_report.get("run_id"),
            },
            {
                "id": "timeout_cleanup",
                "passed": timeout_passed,
                "timed_out_calls": timeout_governance.get("timed_out_calls", 0),
                "active_after_timeout": timeout_state.get("active", 0),
                "cleanup_completions": len(timeout_state.get("finished", [])),
                "run_id": timeout_report.get("run_id"),
            },
        ]
        passed = sum(row["passed"] is True for row in rows)
        artifact: dict[str, object] = {
            "schema_version": 1,
            "benchmark": "ruiclaw-tool-governance-v1",
            "generated_at": datetime.now(UTC).isoformat(),
            "summary": {
                "total_scenarios": len(rows),
                "passed": passed,
                "pass_rate": passed / len(rows),
                "parallel_speedup": round(speedup, 3),
                "configured_parallel_limit": 2,
                "observed_parallel_peak": parallel_state.get("peak", 0),
                "write_overlap_count": write_state.get("write_overlaps", 0),
                "timeout_residue_count": timeout_state.get("active", 0),
            },
            "rows": rows,
        }
        self.artifact_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.artifact_path.with_name(
            f".{self.artifact_path.name}.{uuid4().hex}.tmp"
        )
        try:
            temporary.write_text(
                json.dumps(artifact, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            os.replace(temporary, self.artifact_path)
        finally:
            temporary.unlink(missing_ok=True)
        return artifact


def run_tool_governance_benchmark(
    artifact_path: Path = DEFAULT_TOOL_GOVERNANCE_ARTIFACT_PATH,
    workspace_root: Path = DEFAULT_TOOL_GOVERNANCE_WORKSPACE_ROOT,
) -> dict[str, object]:
    return asyncio.run(ToolGovernanceBenchmarkEvaluator(artifact_path, workspace_root).run())
