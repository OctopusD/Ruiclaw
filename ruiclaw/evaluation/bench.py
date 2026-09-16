"""Deterministic RuiClaw harness regression benchmark.

The design follows PicoBench's fixed fixtures, scripted model, isolated task
workspaces, deterministic verifiers, and persisted evidence.  Execution uses
ruiclaw's real AgentRunner, filesystem tools, and RuiClaw RunLedger.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import locale
import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, TypedDict, cast
from uuid import uuid4

from ruiclaw.agent.runner import AgentRunner, AgentRunSpec
from ruiclaw.agent.tools.file_state import FileStates
from ruiclaw.agent.tools.filesystem import EditFileTool, ReadFileTool
from ruiclaw.agent.tools.registry import ToolRegistry
from ruiclaw.llm_usage import capture_llm_call, capture_llm_calls
from ruiclaw.llm_usage.models import LLMCallRecord
from ruiclaw.observability.run_ledger import RunLedger, RunLedgerHook
from ruiclaw.providers.base import LLMProvider, LLMResponse, ToolCallRequest
from ruiclaw.utils.llm_runtime import LLMRuntime

SCHEMA_VERSION = 1
MODEL_NAME = "ruiclaw-scripted"
MODEL_VERSION = "deterministic-v1"
SAFE_TASK_ID = re.compile(r"^[a-z0-9][a-z0-9_-]{0,79}$")
SUPPORTED_TOOLS = frozenset({"read_file", "edit_file"})
DEFAULT_BENCHMARK_PATH = Path("benchmarks/ruiclaw_tasks.json")
DEFAULT_ARTIFACT_PATH = Path("benchmarks/results/ruiclaw-bench-v1/result.json")
DEFAULT_WORKSPACE_ROOT = Path("benchmarks/results/ruiclaw-bench-v1/workspaces")


class ScriptedToolCall(TypedDict):
    id: str
    name: str
    arguments: dict[str, object]


class ScriptedResponse(TypedDict):
    content: str
    tool_calls: list[ScriptedToolCall]


class FileContainsVerifier(TypedDict):
    kind: Literal["file_contains"]
    path: str
    contains: str


class RunBundleVerifier(TypedDict):
    kind: Literal["run_bundle"]
    required_events: list[str]


VerifierSpec = FileContainsVerifier | RunBundleVerifier


class BenchmarkTask(TypedDict):
    id: str
    prompt: str
    fixture_repo: str
    allowed_tools: list[str]
    step_budget: int
    expected_artifact: str
    verifier: VerifierSpec
    category: str
    script: list[ScriptedResponse]


@dataclass(slots=True)
class _VerifierResult:
    passed: bool
    detail: str


class ScriptedProvider(LLMProvider):
    """Provider that returns a fixed sequence while exercising real runner code."""

    def __init__(self, responses: list[ScriptedResponse]) -> None:
        super().__init__(provider_name="ruiclaw-bench")
        self._responses = iter(responses)
        self.call_count = 0

    async def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        model: str | None = None,
        max_tokens: int = 4096,
        temperature: float = 0.0,
        reasoning_effort: str | None = None,
        tool_choice: str | dict[str, Any] | None = None,
    ) -> LLMResponse:
        del messages, tools, model, max_tokens, temperature, reasoning_effort, tool_choice
        try:
            scripted = next(self._responses)
        except StopIteration as exc:
            raise RuntimeError("scripted provider exhausted before the run completed") from exc
        self.call_count += 1
        tool_calls = [
            ToolCallRequest(
                id=call["id"],
                name=call["name"],
                arguments=dict(call["arguments"]),
            )
            for call in scripted["tool_calls"]
        ]
        return LLMResponse(
            content=scripted["content"],
            tool_calls=tool_calls,
            finish_reason="tool_calls" if tool_calls else "stop",
        )

    def get_default_model(self) -> str:
        return MODEL_NAME


def _json_object(path: Path) -> dict[str, object]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object in {path}")
    return cast(dict[str, object], value)


def _path_within(root: Path, value: str, *, label: str) -> Path:
    root = root.resolve()
    candidate = (root / value).resolve()
    if candidate != root and root not in candidate.parents:
        raise ValueError(f"{label} escapes its root: {value}")
    return candidate


def _validate_script(value: object, *, task_id: str) -> list[ScriptedResponse]:
    if not isinstance(value, list) or not value:
        raise ValueError(f"benchmark task {task_id} script must be a non-empty list")
    responses: list[ScriptedResponse] = []
    for index, raw_response in enumerate(cast(list[object], value)):
        if not isinstance(raw_response, dict):
            raise ValueError(f"benchmark task {task_id} script row {index} must be an object")
        response = cast(dict[str, object], raw_response)
        content = response.get("content", "")
        calls_value = response.get("tool_calls", [])
        if not isinstance(content, str) or not isinstance(calls_value, list):
            raise ValueError(f"benchmark task {task_id} has an invalid script row")
        calls: list[ScriptedToolCall] = []
        for raw_call in cast(list[object], calls_value):
            if not isinstance(raw_call, dict):
                raise ValueError(f"benchmark task {task_id} tool call must be an object")
            call = cast(dict[str, object], raw_call)
            call_id, name, arguments = call.get("id"), call.get("name"), call.get("arguments")
            argument_keys = cast(dict[object, object], arguments).keys() if isinstance(arguments, dict) else ()
            if (
                not isinstance(call_id, str)
                or not call_id
                or not isinstance(name, str)
                or not name
                or not isinstance(arguments, dict)
                or any(not isinstance(key, str) for key in argument_keys)
            ):
                raise ValueError(f"benchmark task {task_id} has an invalid tool call")
            arguments_object = cast(dict[str, object], arguments)
            calls.append({"id": call_id, "name": name, "arguments": dict(arguments_object)})
        responses.append({"content": content, "tool_calls": calls})
    return responses


def load_benchmark(path: Path = DEFAULT_BENCHMARK_PATH) -> tuple[Path, list[BenchmarkTask]]:
    """Load and validate the fixed benchmark at its untrusted JSON boundary."""
    path = path.resolve()
    data = _json_object(path)
    if data.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("unsupported benchmark schema_version")
    tasks_value = data.get("tasks")
    if not isinstance(tasks_value, list) or not tasks_value:
        raise ValueError("benchmark tasks must be a non-empty list")

    repo_root = path.parent.parent
    seen: set[str] = set()
    tasks: list[BenchmarkTask] = []
    for raw_task in cast(list[object], tasks_value):
        if not isinstance(raw_task, dict):
            raise ValueError("benchmark task must be an object")
        task = cast(dict[str, object], raw_task)
        task_id = task.get("id")
        if not isinstance(task_id, str) or not SAFE_TASK_ID.fullmatch(task_id):
            raise ValueError(f"invalid benchmark task id: {task_id!r}")
        if task_id in seen:
            raise ValueError(f"duplicate benchmark task id: {task_id}")
        seen.add(task_id)

        required_text = ("prompt", "fixture_repo", "expected_artifact", "category")
        if any(not isinstance(task.get(key), str) or not task.get(key) for key in required_text):
            raise ValueError(f"benchmark task {task_id} is missing required text fields")
        fixture_repo = cast(str, task["fixture_repo"])
        fixture_path = _path_within(repo_root, fixture_repo, label="fixture_repo")
        if not fixture_path.is_dir() or fixture_path.is_symlink():
            raise ValueError(f"benchmark task {task_id} fixture repo does not exist")

        tools_value = task.get("allowed_tools")
        if not isinstance(tools_value, list) or not tools_value:
            raise ValueError(f"benchmark task {task_id} allowed_tools must be non-empty")
        allowed_tools = [str(tool) for tool in cast(list[object], tools_value)]
        if any(tool not in SUPPORTED_TOOLS for tool in allowed_tools):
            raise ValueError(f"benchmark task {task_id} contains an unsupported tool")
        budget = task.get("step_budget")
        if isinstance(budget, bool) or not isinstance(budget, int) or budget < 1:
            raise ValueError(f"benchmark task {task_id} step_budget must be positive")
        verifier_value = task.get("verifier")
        if not isinstance(verifier_value, dict):
            raise ValueError(f"benchmark task {task_id} verifier must be an object")
        verifier = cast(dict[str, object], verifier_value)
        kind = verifier.get("kind")
        if kind not in {"file_contains", "run_bundle"}:
            raise ValueError(f"benchmark task {task_id} verifier kind is unsupported")
        if kind == "file_contains":
            verifier_path, contains = verifier.get("path"), verifier.get("contains")
            if not isinstance(verifier_path, str) or not isinstance(contains, str):
                raise ValueError(f"benchmark task {task_id} file verifier is invalid")
            normalized_verifier: VerifierSpec = {
                "kind": "file_contains",
                "path": verifier_path,
                "contains": contains,
            }
        else:
            required_events = verifier.get("required_events", [])
            if not isinstance(required_events, list) or any(
                not isinstance(event, str) for event in cast(list[object], required_events)
            ):
                raise ValueError(f"benchmark task {task_id} run verifier is invalid")
            normalized_verifier = {
                "kind": "run_bundle",
                "required_events": cast(list[str], required_events),
            }

        script = _validate_script(task.get("script"), task_id=task_id)
        scripted_tools = {call["name"] for response in script for call in response["tool_calls"]}
        if not scripted_tools.issubset(set(allowed_tools)):
            raise ValueError(f"benchmark task {task_id} script uses a disallowed tool")
        tasks.append({
            "id": task_id,
            "prompt": cast(str, task["prompt"]),
            "fixture_repo": fixture_repo,
            "allowed_tools": allowed_tools,
            "step_budget": budget,
            "expected_artifact": cast(str, task["expected_artifact"]),
            "verifier": normalized_verifier,
            "category": cast(str, task["category"]),
            "script": script,
        })
    return repo_root, tasks


def _fixture_snapshot(paths: list[Path]) -> str:
    digest = hashlib.sha256()
    for root in sorted(set(paths), key=str):
        for path in sorted((item for item in root.rglob("*") if item.is_file()), key=str):
            digest.update(root.name.encode())
            digest.update(b"\0")
            digest.update(str(path.relative_to(root)).encode())
            digest.update(b"\0")
            digest.update(path.read_bytes())
            digest.update(b"\0")
    return f"sha256:{digest.hexdigest()}"


def _git_value(repo_root: Path, *args: str) -> str:
    try:
        result = subprocess.run(
            ["git", *args], cwd=repo_root, capture_output=True, text=True, timeout=5, check=True
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return result.stdout.strip()


def _tool_registry(workspace: Path, allowed_tools: list[str]) -> ToolRegistry:
    registry = ToolRegistry()
    states = FileStates()
    for name in allowed_tools:
        if name == "read_file":
            tool = ReadFileTool(  # pyright: ignore[reportAbstractUsage]
                workspace=workspace,
                allowed_dir=workspace,
                file_states=states,
                restrict_to_workspace=True,
            )
        else:
            tool = EditFileTool(  # pyright: ignore[reportAbstractUsage]
                workspace=workspace,
                allowed_dir=workspace,
                file_states=states,
                restrict_to_workspace=True,
            )
        registry.register(tool)
    return registry


def _read_events(path: Path) -> list[dict[str, object]]:
    events: list[dict[str, object]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        value = json.loads(line)
        if isinstance(value, dict):
            events.append(cast(dict[str, object], value))
    return events


def _verify(task: BenchmarkTask, fixture: Path, run_dir: Path) -> _VerifierResult:
    verifier = task["verifier"]
    if verifier["kind"] == "file_contains":
        path = _path_within(fixture, verifier["path"], label="verifier path")
        if not path.is_file() or path.is_symlink():
            return _VerifierResult(False, f"missing verifier file: {verifier['path']}")
        passed = verifier["contains"] in path.read_text(encoding="utf-8")
        return _VerifierResult(passed, "content matched" if passed else "content did not match")

    required_files = ("manifest.json", "events.jsonl", "report.json")
    missing = [name for name in required_files if not (run_dir / name).is_file()]
    if missing:
        return _VerifierResult(False, f"missing run evidence: {', '.join(missing)}")
    event_types = {str(event.get("event_type", "")) for event in _read_events(run_dir / "events.jsonl")}
    required_events = set(verifier.get("required_events", []))
    missing_events = sorted(required_events - event_types)
    if missing_events:
        return _VerifierResult(False, f"missing events: {', '.join(missing_events)}")
    return _VerifierResult(True, "run evidence bundle complete")


def _remove_task_workspace(path: Path) -> None:
    if not path.exists() and not path.is_symlink():
        return
    if path.is_symlink() or path.is_file():
        path.unlink()
    else:
        shutil.rmtree(path)


class BenchmarkEvaluator:
    """Run a fixed RuiClaw harness regression benchmark."""

    def __init__(
        self,
        benchmark_path: Path = DEFAULT_BENCHMARK_PATH,
        artifact_path: Path = DEFAULT_ARTIFACT_PATH,
        workspace_root: Path = DEFAULT_WORKSPACE_ROOT,
    ) -> None:
        self.benchmark_path = benchmark_path
        self.artifact_path = artifact_path
        self.workspace_root = workspace_root.resolve()

    async def run(self) -> dict[str, object]:
        repo_root, tasks = load_benchmark(self.benchmark_path)
        rows = [await self.run_task(repo_root, task) for task in tasks]
        passed = sum(1 for row in rows if row["passed"])
        within_budget = sum(1 for row in rows if row["within_budget"])
        verifier_passes = sum(1 for row in rows if row["verifier_passed"])
        total = len(rows)
        total_model_calls = sum(_integer(row["model_calls"]) for row in rows)
        total_input_tokens = 0
        total_output_tokens = 0
        priced_costs: list[float] = []
        partial_costs: list[float] = []
        unknown_cost_tasks = 0
        pricing_snapshot_id: str | None = None
        for row in rows:
            report = cast(dict[str, object], row["report"])
            usage_value = report.get("usage")
            if isinstance(usage_value, dict):
                usage = cast(dict[str, object], usage_value)
                total_input_tokens += _integer(usage.get("input_tokens"))
                total_output_tokens += _integer(usage.get("output_tokens"))
            cost_value = report.get("cost")
            if not isinstance(cost_value, dict):
                unknown_cost_tasks += 1
                continue
            cost = cast(dict[str, object], cost_value)
            snapshot_value = cost.get("pricing_snapshot_id")
            if pricing_snapshot_id is None and isinstance(snapshot_value, str):
                pricing_snapshot_id = snapshot_value
            value = cost.get("estimated_cost_usd")
            if cost.get("status") == "estimated" and isinstance(value, int | float):
                priced_costs.append(float(value))
            else:
                unknown_cost_tasks += 1
                partial_value = cost.get("partial_estimated_cost_usd")
                if cost.get("status") == "partial" and isinstance(
                    partial_value,
                    int | float,
                ):
                    partial_costs.append(float(partial_value))
        all_costed = unknown_cost_tasks == 0
        estimated_total_cost = round(sum(priced_costs), 8) if all_costed else None
        partial_total_cost = (
            round(sum(priced_costs) + sum(partial_costs), 8)
            if priced_costs or partial_costs
            else None
        )
        failure_counts: dict[str, int] = {}
        for row in rows:
            category = row.get("failure_category")
            if isinstance(category, str):
                failure_counts[category] = failure_counts.get(category, 0) + 1
        summary = {
            "total_tasks": total,
            "passed": passed,
            "failed": total - passed,
            "pass_rate": passed / total,
            "within_budget_rate": within_budget / total,
            "verifier_pass_rate": verifier_passes / total,
            "model_calls": total_model_calls,
            "input_tokens": total_input_tokens,
            "output_tokens": total_output_tokens,
            "cost_status": (
                "estimated" if all_costed else "partial" if partial_total_cost is not None else "unknown"
            ),
            "estimated_total_cost_usd": estimated_total_cost,
            "partial_estimated_total_cost_usd": partial_total_cost,
            "cost_per_successful_task_usd": (
                round(estimated_total_cost / passed, 8)
                if estimated_total_cost is not None and passed
                else None
            ),
            "unknown_cost_tasks": unknown_cost_tasks,
            "pricing_snapshot_id": pricing_snapshot_id,
        }
        artifact: dict[str, object] = {
            "schema_version": SCHEMA_VERSION,
            "captured_at": datetime.now(UTC).isoformat(),
            "runtime": {
                "commit_sha": _git_value(repo_root, "rev-parse", "HEAD"),
                "branch": _git_value(repo_root, "branch", "--show-current"),
            },
            "benchmark": {
                "source": str(self.benchmark_path.resolve().relative_to(repo_root)),
                "task_count": total,
            },
            "reproducibility": {
                "fixture_snapshot_id": _fixture_snapshot([
                    _path_within(repo_root, task["fixture_repo"], label="fixture_repo")
                    for task in tasks
                ]),
                "model_name": MODEL_NAME,
                "model_version": MODEL_VERSION,
                "decoding": {"temperature": 0.0},
                "timezone": "UTC",
                "locale": locale.setlocale(locale.LC_CTYPE),
            },
            "summary": summary,
            "failure_category_counts": failure_counts,
            "rows": rows,
        }
        self.artifact_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.artifact_path.with_name(f".{self.artifact_path.name}.{uuid4().hex}.tmp")
        try:
            temporary.write_text(
                json.dumps(artifact, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            os.replace(temporary, self.artifact_path)
        finally:
            temporary.unlink(missing_ok=True)
        return artifact

    async def run_task(self, repo_root: Path, task: BenchmarkTask) -> dict[str, object]:
        source = _path_within(repo_root, task["fixture_repo"], label="fixture_repo")
        fixture = self.workspace_root / task["id"] / source.name
        _remove_task_workspace(fixture.parent)
        fixture.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(source, fixture)

        provider = ScriptedProvider(task["script"])
        provider.set_llm_call_observer(capture_llm_call)
        tools = _tool_registry(fixture, task["allowed_tools"])
        run_id = uuid4().hex
        ledger = RunLedger(
            runs_dir=fixture / ".ruiclaw" / "runs",
            run_id=run_id,
            session_key=f"bench:{task['id']}",
            turn_id=f"bench:{task['id']}:1",
            trigger="benchmark",
        )
        await ledger.start()
        await ledger.append("context_built", {"benchmark_task_id": task["id"]})
        physical_calls: list[LLMCallRecord] = []
        with capture_llm_calls(physical_calls):
            result = await AgentRunner().run(AgentRunSpec(
                initial_messages=[{"role": "user", "content": task["prompt"]}],
                tools=tools,
                runtime=LLMRuntime.capture(provider, MODEL_NAME, context_window_tokens=32_000),
                max_iterations=task["step_budget"],
                max_tool_result_chars=16_000,
                hook=RunLedgerHook(
                    ledger,
                    provider=provider.provider_name,
                    model=MODEL_NAME,
                    physical_calls=physical_calls,
                ),
                concurrent_tools=False,
                workspace=fixture,
                session_key=f"bench:{task['id']}",
                finalize_on_max_iterations=False,
            ))
        run_status = "succeeded" if result.stop_reason == "completed" else "failed"
        await ledger.finish(run_status, stop_reason=result.stop_reason)

        run_dir = ledger.run_dir
        verifier = _verify(task, fixture, run_dir)
        expected_path = _path_within(fixture, task["expected_artifact"], label="expected_artifact")
        expected_exists = expected_path.is_file() and not expected_path.is_symlink()
        within_budget = provider.call_count <= task["step_budget"]
        passed = (
            verifier.passed
            and expected_exists
            and within_budget
            and result.stop_reason == "completed"
        )
        if not expected_exists:
            failure_category = "missing_artifact"
        elif not within_budget:
            failure_category = "budget_exceeded"
        elif not verifier.passed:
            failure_category = "verifier_failed"
        elif result.stop_reason != "completed":
            failure_category = "failure_stop_reason"
        else:
            failure_category = None
        report = _json_object(run_dir / "report.json")
        return {
            "id": task["id"],
            "category": task["category"],
            "status": "pass" if passed else "fail",
            "passed": passed,
            "failure_category": failure_category,
            "allowed_tools": task["allowed_tools"],
            "step_budget": task["step_budget"],
            "model_calls": provider.call_count,
            "within_budget": within_budget,
            "verifier_passed": verifier.passed,
            "verifier_detail": verifier.detail,
            "expected_artifact": task["expected_artifact"],
            "expected_artifact_exists": expected_exists,
            "stop_reason": result.stop_reason,
            "run_id": run_id,
            "run_dir": str(run_dir.relative_to(self.workspace_root)),
            "report": report,
        }


def run_benchmark(
    benchmark_path: Path = DEFAULT_BENCHMARK_PATH,
    artifact_path: Path = DEFAULT_ARTIFACT_PATH,
    workspace_root: Path = DEFAULT_WORKSPACE_ROOT,
) -> dict[str, object]:
    """Synchronous entry point for CLI and scripts."""
    return asyncio.run(BenchmarkEvaluator(benchmark_path, artifact_path, workspace_root).run())


def _integer(value: object) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else 0
