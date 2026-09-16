"""Structured progress tracking for one agent run."""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

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
    status: str = "running"
    completed_actions: list[dict[str, str]] = field(default_factory=list)
    failed_actions: list[dict[str, str | bool]] = field(default_factory=list)
    pending_tool_calls: list[dict[str, str]] = field(default_factory=list)
    last_checkpoint_id: str | None = None
    artifacts: list[str] = field(default_factory=list)
    resume_hint: str | None = None
    updated_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "run_id": self.run_id,
            "session_id": self.session_id,
            "status": self.status,
            "completed_actions": list(self.completed_actions),
            "failed_actions": list(self.failed_actions),
            "pending_tool_calls": list(self.pending_tool_calls),
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
        pending = ", ".join(item["tool"] for item in self.pending_tool_calls) or "(none)"
        artifacts = ", ".join(self.artifacts) or "(none)"
        lines = [
            "## Current Run Progress",
            "This is a runtime recovery snapshot for the current run.",
            "Do not repeat completed actions unless new evidence invalidates them.",
            f"Run ID: {self.run_id}",
            f"Status: {self.status}",
            f"Completed: {completed}",
            f"Failed: {failed}",
            f"Pending: {pending}",
            f"Artifacts: {artifacts}",
        ]
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
            payload = json.loads(self.persist_path.read_text(encoding="utf-8"))
            if payload.get("run_id") != self.snapshot.run_id:
                return
            for key in (
                "status", "completed_actions", "failed_actions", "pending_tool_calls",
                "last_checkpoint_id", "artifacts", "resume_hint", "updated_at",
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

    def set_pending(self, tool_calls: list[dict[str, Any]]) -> None:
        self.snapshot.pending_tool_calls = [
            {"tool": _short(call.get("function", {}).get("name") or call.get("name")),
             "call_id": _short(call.get("id"))}
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
        self.snapshot.pending_tool_calls = []
        self._touch()

    def observe_checkpoint(self, checkpoint_id: str | None, status: str | None = None) -> None:
        if checkpoint_id:
            self.snapshot.last_checkpoint_id = checkpoint_id
        if status:
            self.snapshot.status = status
        self._touch()

    def finish(self, status: str = "completed") -> None:
        self.snapshot.status = status
        self.snapshot.pending_tool_calls = []
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
