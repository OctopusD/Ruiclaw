"""Structured progress tracking for one agent run."""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, cast

from ruiclaw.agent.hook import AgentHook, AgentHookContext, AgentRunHookContext
from ruiclaw.runtime_context import RuntimeContextBlock, wrap_runtime_context_lines

MAX_ACTIONS = 24
MAX_ARTIFACTS = 24
MAX_TEXT = 240


def _short(value: object) -> str:
    text = str(value or "").replace("\n", " ").strip()
    return text[:MAX_TEXT]


@dataclass(slots=True)
class RunProgressSnapshot:
    """Bounded, serializable execution state for a single run."""

    run_id: str
    session_id: str | None = None
    current_goal: str | None = None
    status: str = "running"
    completed_actions: list[dict[str, str]] = field(default_factory=list)
    failed_actions: list[dict[str, str | bool]] = field(default_factory=list)
    pending_actions: list[dict[str, str]] = field(default_factory=list)
    last_checkpoint_id: str | None = None
    artifacts: list[str] = field(default_factory=list)
    blocker: str | None = None
    resume_hint: str | None = None
    updated_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    @property
    def pending_tool_calls(self) -> list[dict[str, str]]:
        """Backward-compatible view for callers using the old field name."""
        return self.pending_actions

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "run_id": self.run_id,
            "session_id": self.session_id,
            "current_goal": self.current_goal,
            "status": self.status,
            "completed_actions": list(self.completed_actions),
            "failed_actions": list(self.failed_actions),
            "pending_actions": list(self.pending_actions),
            "blocker": self.blocker,
            "last_checkpoint_id": self.last_checkpoint_id,
            "artifacts": list(self.artifacts),
            "resume_hint": self.resume_hint,
            "updated_at": self.updated_at,
        }

    def render(self) -> str:
        completed = ", ".join(
            f"{item['tool']} ({item.get('target', 'completed')})"
            for item in self.completed_actions
        ) or "(none)"
        failed = "; ".join(
            f"{item['tool']}: {item.get('error_summary', 'failed')}"
            for item in self.failed_actions
        ) or "(none)"
        pending = ", ".join(
            item.get("action") or item.get("tool", "unknown")
            for item in self.pending_actions
        ) or "(none)"
        artifacts = ", ".join(self.artifacts) or "(none)"
        lines = [
            "## Current Run Progress",
            "This is a runtime recovery snapshot for the current run.",
            "Do not repeat completed actions unless new evidence invalidates them.",
            f"Run ID: {self.run_id}",
            f"Current goal: {self.current_goal or '(not specified)'}",
            f"Status: {self.status}",
            f"Completed: {completed}",
            f"Failed: {failed}",
            f"Pending: {pending}",
            f"Artifacts: {artifacts}",
        ]
        if self.blocker:
            lines.append(f"Blocker: {self.blocker}")
        if self.last_checkpoint_id:
            lines.append(f"Last checkpoint: {self.last_checkpoint_id}")
        if self.resume_hint:
            lines.append(f"Resume hint: {self.resume_hint}")
        return wrap_runtime_context_lines(lines)

    def runtime_context_block(self) -> RuntimeContextBlock:
        return RuntimeContextBlock(source="run_progress", content=self.render())


class RunProgressTracker:
    """Build a deterministic progress snapshot from runner-visible events."""

    def __init__(
        self,
        run_id: str,
        session_id: str | None = None,
        persist_path: Path | None = None,
    ) -> None:
        self.persist_path = persist_path
        self.snapshot = RunProgressSnapshot(run_id=run_id, session_id=session_id)
        self._load_existing()

    def _load_existing(self) -> None:
        if self.persist_path is None or not self.persist_path.is_file():
            return
        try:
            payload_value: object = json.loads(self.persist_path.read_text(encoding="utf-8"))
            if not isinstance(payload_value, dict):
                return
            payload = cast(dict[str, Any], payload_value)
            if payload.get("run_id") != self.snapshot.run_id:
                return
            if "pending_actions" not in payload and "pending_tool_calls" in payload:
                legacy_pending = payload.get("pending_tool_calls")
                payload["pending_actions"] = [
                    {
                        "action": str(item_data.get("tool", "unknown")),
                        "tool": str(item_data.get("tool", "unknown")),
                        "call_id": str(item_data.get("call_id", "")),
                    }
                    for item in cast(list[object], legacy_pending)
                    if isinstance(item, dict)
                    for item_data in (cast(dict[object, object], item),)
                ] if isinstance(legacy_pending, list) else []
            for key in (
                "current_goal", "status", "completed_actions", "failed_actions",
                "pending_actions", "last_checkpoint_id", "artifacts", "blocker",
                "resume_hint", "updated_at",
            ):
                if key in payload:
                    setattr(self.snapshot, key, payload[key])
        except (OSError, TypeError, ValueError, json.JSONDecodeError):
            # A corrupt progress file must not prevent checkpoint recovery.
            return

    def _persist(self) -> None:
        if self.persist_path is None:
            return
        self.persist_path.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(
            prefix=f".{self.persist_path.name}.",
            dir=self.persist_path.parent,
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump(self.snapshot.to_dict(), stream, ensure_ascii=False, indent=2)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.persist_path)
        finally:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass

    def _touch(self) -> None:
        self.snapshot.updated_at = datetime.now(timezone.utc).isoformat()
        self._persist()

    def set_goal(self, goal: str | None) -> None:
        if self.snapshot.current_goal is None and goal:
            self.snapshot.current_goal = _short(goal)
            self._touch()

    def set_pending(self, tool_calls: list[dict[str, Any]]) -> None:
        self.snapshot.pending_actions = [
            {
                "action": _short(
                    call.get("function", {}).get("name") or call.get("name")
                ),
                "tool": _short(
                    call.get("function", {}).get("name") or call.get("name")
                ),
                "call_id": _short(call.get("id")),
            }
            for call in tool_calls
        ]
        self._touch()

    def observe_results(
        self,
        tool_calls: list[Any],
        events: list[dict[str, Any]],
    ) -> None:
        for call, event in zip(tool_calls, events):
            name = _short(getattr(call, "name", None) or event.get("name"))
            status = event.get("status")
            target = ""
            arguments = getattr(call, "arguments", None)
            if isinstance(arguments, dict):
                arguments = cast(dict[str, Any], arguments)
                for key in ("path", "file", "query", "command", "url"):
                    if arguments.get(key):
                        target = _short(arguments[key])
                        break
            action = {"tool": name, "target": target or "completed"}
            if status == "ok":
                self._append_unique(self.snapshot.completed_actions, action)
            else:
                self._append_unique(
                    self.snapshot.failed_actions,
                    {
                        "tool": name,
                        "error_summary": _short(event.get("detail") or "tool failed"),
                        "retryable": True,
                    },
                )
                self.snapshot.resume_hint = f"Review the failed {name} call before retrying it"
        self.snapshot.pending_actions = []
        self._touch()

    def set_blocker(self, blocker: str | None) -> None:
        self.snapshot.blocker = _short(blocker) if blocker else None
        self._touch()

    def observe_checkpoint(self, checkpoint_id: str | None, status: str | None = None) -> None:
        if checkpoint_id:
            self.snapshot.last_checkpoint_id = checkpoint_id
        if status:
            self.snapshot.status = status
        self._touch()

    def finish(self, status: str = "completed") -> None:
        self.snapshot.status = status
        self.snapshot.pending_actions = []
        if status in {"completed", "succeeded"}:
            self.snapshot.blocker = None
        self._touch()

    def add_artifact(self, path: str) -> None:
        path = _short(path)
        if path and path not in self.snapshot.artifacts:
            self.snapshot.artifacts.append(path)
            del self.snapshot.artifacts[:-MAX_ARTIFACTS]
            self._touch()

    @staticmethod
    def _append_unique(items: list[dict[str, Any]], value: dict[str, Any]) -> None:
        if value not in items:
            items.append(value)
            del items[:-MAX_ACTIONS]

    def as_json(self) -> str:
        return json.dumps(self.snapshot.to_dict(), ensure_ascii=False, sort_keys=True)

    def runtime_context_block(self) -> RuntimeContextBlock:
        return self.snapshot.runtime_context_block()


class RunProgressHook(AgentHook):
    """Update task progress from deterministic runner lifecycle events."""

    def __init__(self, tracker: RunProgressTracker) -> None:
        super().__init__()
        self.tracker = tracker

    async def before_execute_tools(self, context: AgentHookContext) -> None:
        self.tracker.set_pending([
            {
                "id": call.id,
                "name": call.name,
            }
            for call in context.tool_calls
        ])

    async def after_execute_tool(
        self,
        context: AgentHookContext,
        tool_call: Any,
        tool: Any,
        params: Any,
        result: Any,
    ) -> None:
        del context, tool
        failed = isinstance(result, str) and result.startswith("Error")
        self.tracker.observe_results(
            [tool_call],
            [{
                "name": tool_call.name,
                "status": "error" if failed else "ok",
                "detail": _short(result),
            }],
        )
        if not failed and isinstance(params, dict):
            params_data = cast(dict[object, object], params)
            for key in ("path", "file"):
                artifact = params_data.get(key)
                if isinstance(artifact, str) and artifact:
                    self.tracker.add_artifact(artifact)
                    break
        if failed:
            self.tracker.set_blocker(result)

    async def on_execute_tool_error(
        self,
        context: AgentHookContext,
        tool_call: Any,
        tool: Any,
        params: Any,
        error: Any,
    ) -> None:
        del context, tool, params
        self.tracker.observe_results(
            [tool_call],
            [{"name": tool_call.name, "status": "error", "detail": _short(error)}],
        )
        self.tracker.set_blocker(_short(error))

    async def after_run(self, context: AgentRunHookContext) -> None:
        if context.stop_reason in {"error", "tool_error"} or context.error:
            self.tracker.finish("failed")
        else:
            self.tracker.finish("completed")

    async def on_error(self, context: AgentRunHookContext) -> None:
        self.tracker.set_blocker(context.error or "agent run failed")
