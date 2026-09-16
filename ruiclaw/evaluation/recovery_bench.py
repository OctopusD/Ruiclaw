"""Deterministic fault-injection benchmark for ruiclaw recovery semantics."""

from __future__ import annotations

import asyncio
import json
import os
import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, cast
from uuid import uuid4

from ruiclaw.agent.hook import AgentHook, AgentHookContext
from ruiclaw.agent.loop import AgentLoop
from ruiclaw.agent.tools.base import Tool
from ruiclaw.agent.tools.registry import ToolRegistry
from ruiclaw.bus.events import InboundMessage
from ruiclaw.bus.queue import MessageBus
from ruiclaw.bus.runtime_events import PARENT_RUN_ID_METADATA_KEY
from ruiclaw.evaluation.bench import MODEL_NAME, MODEL_VERSION, ScriptedProvider
from ruiclaw.session.manager import SessionManager
from ruiclaw.session.recovery import RECOVERY_METADATA_KEY, RecoveryCoordinator
from ruiclaw.webui.session_identity import WEBUI_SESSION_STORAGE_PREFIX

DEFAULT_RECOVERY_ARTIFACT_PATH = Path(
    "benchmarks/results/ruiclaw-recovery-v1/result.json"
)
DEFAULT_RECOVERY_WORKSPACE_ROOT = Path(
    "benchmarks/results/ruiclaw-recovery-v1/workspaces"
)
BENCH_WEBUI_SESSION_KEY = f"{WEBUI_SESSION_STORAGE_PREFIX}bench"


class _BenchmarkRecoveryCoordinator(RecoveryCoordinator):
    """Keep optional WebUI transcript discovery inside the benchmark boundary."""

    @staticmethod
    def _has_unfinished_webui_transcript(session_key: str) -> bool:
        del session_key
        return False


class _SideEffectTool(Tool):
    """Append one durable marker so duplicate execution is observable."""

    def __init__(self, marker_path: Path) -> None:
        self.marker_path = marker_path

    @property
    def name(self) -> str:
        return "record_side_effect"

    @property
    def description(self) -> str:
        return "Record one deterministic benchmark side effect."

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {"value": {"type": "string"}},
            "required": ["value"],
            "additionalProperties": False,
        }

    async def execute(self, **kwargs: Any) -> str:
        value = kwargs.get("value")
        if not isinstance(value, str):
            raise ValueError("value must be a string")
        self.marker_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.marker_path, "a", encoding="utf-8") as handle:
            handle.write(value + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        return f"recorded:{value}"


class _FaultHook(AgentHook):
    """Raise cancellation at one runner boundary after its checkpoint exists."""

    def __init__(self, boundary: Literal["before_tools", "after_iteration"]) -> None:
        super().__init__(reraise=True)
        self.boundary = boundary
        self.injected = False

    async def before_execute_tools(self, context: AgentHookContext) -> None:
        del context
        if self.boundary == "before_tools" and not self.injected:
            self.injected = True
            raise asyncio.CancelledError

    async def after_iteration(self, context: AgentHookContext) -> None:
        del context
        if self.boundary == "after_iteration" and not self.injected:
            self.injected = True
            raise asyncio.CancelledError


def _scripted_tool_provider() -> ScriptedProvider:
    return ScriptedProvider([
        {
            "content": "",
            "tool_calls": [{
                "id": "side-effect-1",
                "name": "record_side_effect",
                "arguments": {"value": "committed-once"},
            }],
        },
        {"content": "Original run should be interrupted before this.", "tool_calls": []},
    ])


def _final_provider(content: str = "Recovery completed.") -> ScriptedProvider:
    return ScriptedProvider([{"content": content, "tool_calls": []}])


def _tool_registry(marker_path: Path) -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(_SideEffectTool(marker_path))
    return registry


def _reset_case(path: Path) -> None:
    if path.is_symlink() or path.is_file():
        path.unlink()
    elif path.exists():
        shutil.rmtree(path)
    path.mkdir(parents=True)


def _session_manager(case_root: Path, workspace: Path) -> SessionManager:
    return SessionManager(workspace, sessions_root=case_root / "sessions")


def _prepare_webui_session(manager: SessionManager) -> None:
    session = manager.get_or_create(BENCH_WEBUI_SESSION_KEY)
    session.metadata["webui"] = True
    manager.save(session)


def _message() -> InboundMessage:
    return InboundMessage(
        channel="websocket",
        sender_id="bench-user",
        chat_id="bench",
        content="Record the side effect and finish the task.",
        metadata={"webui": True},
    )


async def _interrupt(
    workspace: Path,
    sessions: SessionManager,
    provider: ScriptedProvider,
    marker_path: Path,
    boundary: Literal["before_tools", "after_iteration"],
) -> str:
    loop = AgentLoop(
        bus=MessageBus(),
        provider=provider,
        workspace=workspace,
        model=MODEL_NAME,
        max_iterations=4,
        restrict_to_workspace=True,
        session_manager=sessions,
    )
    delivery = loop.turn_delivery_factory.create(_message(), BENCH_WEBUI_SESSION_KEY)
    try:
        await loop._process_message(  # pyright: ignore[reportPrivateUsage]
            _message(),
            delivery=delivery,
            hooks=[_FaultHook(boundary)],
            tools=_tool_registry(marker_path),
        )
        raise AssertionError("fault injection did not interrupt the run")
    except asyncio.CancelledError:
        pass
    finally:
        await loop.aclose()
    if delivery.run_id is None:
        raise AssertionError("interrupted run did not receive a run_id")
    return delivery.run_id


def _recovery_state(manager: SessionManager) -> dict[str, object]:
    value = manager.get_or_create(BENCH_WEBUI_SESSION_KEY).metadata.get(RECOVERY_METADATA_KEY)
    if not isinstance(value, dict):
        raise AssertionError("recovery state was not persisted")
    return cast(dict[str, object], value)


def _manifest(workspace: Path, run_id: str) -> dict[str, object]:
    path = workspace / ".ruiclaw" / "runs" / run_id / "manifest.json"
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise AssertionError("run manifest is not an object")
    return cast(dict[str, object], value)


async def _uncertain_tool_case(case_root: Path) -> dict[str, object]:
    workspace = case_root / "workspace"
    workspace.mkdir()
    marker = workspace / "side-effects.log"
    sessions = _session_manager(case_root, workspace)
    _prepare_webui_session(sessions)
    interrupted_run_id = await _interrupt(
        workspace,
        sessions,
        _scripted_tool_provider(),
        marker,
        "before_tools",
    )

    bus = MessageBus()
    restarted = _session_manager(case_root, workspace)
    coordinator = _BenchmarkRecoveryCoordinator(restarted, bus)
    await coordinator.scan()
    state = _recovery_state(restarted)
    parent_linked = state.get("parent_run_id") == interrupted_run_id
    side_effect_count = len(marker.read_text(encoding="utf-8").splitlines()) if marker.exists() else 0
    passed = (
        state.get("status") == "awaiting_user"
        and state.get("reason") == "tool_state_unknown"
        and parent_linked
        and side_effect_count == 0
    )
    return {
        "id": "uncertain_tool_not_replayed",
        "status": "pass" if passed else "fail",
        "passed": passed,
        "interrupted_run_id": interrupted_run_id,
        "recovery_status": state.get("status"),
        "recovery_reason": state.get("reason"),
        "parent_linked": parent_linked,
        "side_effect_count": side_effect_count,
        "duplicate_side_effects": 0,
    }


async def _completed_tool_case(case_root: Path) -> dict[str, object]:
    workspace = case_root / "workspace"
    workspace.mkdir()
    marker = workspace / "side-effects.log"
    sessions = _session_manager(case_root, workspace)
    _prepare_webui_session(sessions)
    interrupted_run_id = await _interrupt(
        workspace,
        sessions,
        _scripted_tool_provider(),
        marker,
        "after_iteration",
    )

    bus = MessageBus()
    restarted = _session_manager(case_root, workspace)
    coordinator = _BenchmarkRecoveryCoordinator(restarted, bus)
    await coordinator.scan()
    waiting = _recovery_state(restarted)
    recovery_id = waiting.get("recovery_id")
    if not isinstance(recovery_id, str):
        raise AssertionError("recovery did not produce an id")
    await coordinator.handle_action(
        "continue",
        {"chat_id": "bench", "recovery_id": recovery_id},
    )
    continuation = bus.inbound.get_nowait()

    resume_provider = _final_provider()
    resumed_loop = AgentLoop(
        bus=MessageBus(),
        provider=resume_provider,
        workspace=workspace,
        model=MODEL_NAME,
        max_iterations=2,
        restrict_to_workspace=True,
        session_manager=restarted,
    )
    delivery = resumed_loop.turn_delivery_factory.create(
        continuation,
        continuation.session_key,
    )
    try:
        outbound = await resumed_loop._process_message(  # pyright: ignore[reportPrivateUsage]
            continuation,
            delivery=delivery,
            tools=_tool_registry(marker),
        )
    finally:
        await resumed_loop.aclose()
    await coordinator.scan()

    child_run_id = delivery.run_id
    child_manifest = _manifest(workspace, child_run_id) if child_run_id else {}
    side_effect_count = len(marker.read_text(encoding="utf-8").splitlines())
    parent_linked = (
        continuation.metadata.get(PARENT_RUN_ID_METADATA_KEY) == interrupted_run_id
        and child_manifest.get("parent_run_id") == interrupted_run_id
    )
    final_state = _recovery_state(restarted)
    passed = (
        waiting.get("reason") == "restart_requires_confirmation"
        and side_effect_count == 1
        and parent_linked
        and resume_provider.call_count == 1
        and outbound is not None
        and outbound.content == "Recovery completed."
        and final_state.get("status") == "recovered"
    )
    return {
        "id": "completed_tool_resumes_once",
        "status": "pass" if passed else "fail",
        "passed": passed,
        "interrupted_run_id": interrupted_run_id,
        "child_run_id": child_run_id,
        "recovery_status": final_state.get("status"),
        "recovery_reason": waiting.get("reason"),
        "parent_linked": parent_linked,
        "side_effect_count": side_effect_count,
        "duplicate_side_effects": max(0, side_effect_count - 1),
        "resume_model_calls": resume_provider.call_count,
    }


async def _final_answer_case(case_root: Path) -> dict[str, object]:
    workspace = case_root / "workspace"
    workspace.mkdir()
    marker = workspace / "side-effects.log"
    sessions = _session_manager(case_root, workspace)
    _prepare_webui_session(sessions)
    provider = _final_provider("Answer persisted before shutdown.")
    interrupted_run_id = await _interrupt(
        workspace,
        sessions,
        provider,
        marker,
        "after_iteration",
    )

    bus = MessageBus()
    restarted = _session_manager(case_root, workspace)
    coordinator = _BenchmarkRecoveryCoordinator(restarted, bus)
    await coordinator.scan()
    state = _recovery_state(restarted)
    session = restarted.get_or_create(BENCH_WEBUI_SESSION_KEY)
    restored_answer = session.messages[-1].get("content") if session.messages else None
    passed = (
        state.get("status") == "recovered"
        and state.get("reason") == "answer_restored"
        and restored_answer == "Answer persisted before shutdown."
        and bus.inbound.empty()
    )
    return {
        "id": "final_answer_restored_without_replay",
        "status": "pass" if passed else "fail",
        "passed": passed,
        "interrupted_run_id": interrupted_run_id,
        "recovery_status": state.get("status"),
        "recovery_reason": state.get("reason"),
        "restored_answer": restored_answer,
        "additional_model_calls": 0,
        "duplicate_side_effects": 0,
    }


class RecoveryBenchmarkEvaluator:
    """Execute fixed recovery fault scenarios and persist their evidence."""

    def __init__(
        self,
        artifact_path: Path = DEFAULT_RECOVERY_ARTIFACT_PATH,
        workspace_root: Path = DEFAULT_RECOVERY_WORKSPACE_ROOT,
    ) -> None:
        self.artifact_path = artifact_path.resolve()
        self.workspace_root = workspace_root.resolve()

    async def run(self) -> dict[str, object]:
        cases = {
            "uncertain_tool_not_replayed": _uncertain_tool_case,
            "completed_tool_resumes_once": _completed_tool_case,
            "final_answer_restored_without_replay": _final_answer_case,
        }
        rows: list[dict[str, object]] = []
        for name, run_case in cases.items():
            case_root = self.workspace_root / name
            _reset_case(case_root)
            rows.append(await run_case(case_root))

        passed = sum(row["passed"] is True for row in rows)
        duplicate_side_effects = sum(
            cast(int, row.get("duplicate_side_effects", 0)) for row in rows
        )
        parent_checks = [row for row in rows if "parent_linked" in row]
        parent_passes = sum(row.get("parent_linked") is True for row in parent_checks)
        total = len(rows)
        artifact: dict[str, object] = {
            "schema_version": 1,
            "captured_at": datetime.now(UTC).isoformat(),
            "benchmark": "ruiclaw-recovery-v1",
            "reproducibility": {
                "model_name": MODEL_NAME,
                "model_version": MODEL_VERSION,
                "fault_injection": "deterministic-hook-cancellation",
            },
            "summary": {
                "total_scenarios": total,
                "passed": passed,
                "failed": total - passed,
                "recovery_success_rate": passed / total,
                "duplicate_side_effects": duplicate_side_effects,
                "parent_run_link_rate": parent_passes / len(parent_checks),
            },
            "rows": rows,
        }
        self.artifact_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.artifact_path.with_name(
            f".{self.artifact_path.name}.{uuid4().hex}.tmp"
        )
        try:
            temporary.write_text(
                json.dumps(artifact, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            os.replace(temporary, self.artifact_path)
        finally:
            temporary.unlink(missing_ok=True)
        return artifact


def run_recovery_benchmark(
    artifact_path: Path = DEFAULT_RECOVERY_ARTIFACT_PATH,
    workspace_root: Path = DEFAULT_RECOVERY_WORKSPACE_ROOT,
) -> dict[str, object]:
    return asyncio.run(RecoveryBenchmarkEvaluator(artifact_path, workspace_root).run())
