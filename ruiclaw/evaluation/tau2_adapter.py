"""Bridge RuiClaw's external-tool runtime to tau2/τ³-bench.

The adapter has no import-time dependency on tau2.  The launcher injects its
message and agent classes, keeping the benchmark an optional development tool.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from typing import Any
from uuid import uuid4

from ruiclaw.agent.tools.base import Tool
from ruiclaw.agent.tools.registry import ToolRegistry
from ruiclaw.ruiclaw import RuiClaw


class _TauToolSchema(Tool):
    """Expose a Tau environment tool's schema without ever executing it here."""

    def __init__(self, schema: dict[str, Any]) -> None:
        function = schema["function"]
        self._name = str(function["name"])
        self._description = str(function.get("description") or self._name)
        self._parameters = dict(function.get("parameters") or {"type": "object"})

    @property
    def name(self) -> str:
        return self._name

    @property
    def description(self) -> str:
        return self._description

    @property
    def parameters(self) -> dict[str, Any]:
        return self._parameters

    async def execute(self, **kwargs: Any) -> Any:
        raise RuntimeError("Tau owns execution of environment tools")


def create_tau2_agent(
    half_duplex_agent_type: type,
    assistant_message_type: type,
    tool_call_type: type,
    tool_message_type: type,
    multi_tool_message_type: type,
    *,
    bot_factory: Callable[..., RuiClaw] = RuiClaw.from_config,
) -> type:
    """Return a Tau ``HalfDuplexAgent`` implementation backed by RuiClaw."""

    class RuiClawTauAgent(half_duplex_agent_type):
        def __init__(
            self,
            tools: list[Any],
            domain_policy: str,
            *,
            ruiclaw_config_path: str | None = None,
            workspace: str | None = None,
            model: str | None = None,
            task: Any | None = None,
            **_: Any,
        ) -> None:
            super().__init__(tools=tools, domain_policy=domain_policy)
            self._registry = ToolRegistry()
            for tool in tools:
                self._registry.register(_TauToolSchema(tool.openai_schema))
            task_id = getattr(task, "id", None) or "task"
            self._session_key = f"tau2:{task_id}:{uuid4().hex}"
            self._bot = bot_factory(
                config_path=ruiclaw_config_path,
                workspace=workspace,
                model=model,
            )

        def get_init_state(self, message_history: list[Any] | None = None) -> dict[str, Any]:
            return {"started": False, "message_history": list(message_history or [])}

        def generate_next_message(self, message: Any, state: dict[str, Any]) -> tuple[Any, dict[str, Any]]:
            if not state["started"]:
                result = _run_sync(self._bot.run_external_tools(
                    self._initial_request(message, state["message_history"]),
                    tools=self._registry,
                    session_key=self._session_key,
                    channel="benchmark",
                    chat_id=self._session_key,
                    sender_id="tau2-user",
                    attributes={"benchmark": "tau2"},
                ))
                state["started"] = True
            elif isinstance(message, (tool_message_type, multi_tool_message_type)):
                result = _run_sync(self._bot.continue_external_tools(
                    _tool_results(message, tool_message_type, multi_tool_message_type),
                    tools=self._registry,
                    session_key=self._session_key,
                    channel="benchmark",
                    chat_id=self._session_key,
                    sender_id="tau2-user",
                    attributes={"benchmark": "tau2"},
                ))
            else:
                result = _run_sync(self._bot.run_external_tools(
                    str(message.content or ""),
                    tools=self._registry,
                    session_key=self._session_key,
                    channel="benchmark",
                    chat_id=self._session_key,
                    sender_id="tau2-user",
                    attributes={"benchmark": "tau2"},
                ))
            state["message_history"].append(message)
            calls = _tool_calls(result.metadata, tool_call_type)
            if calls:
                return assistant_message_type.text("", tool_calls=calls), state
            return assistant_message_type.text(result.content), state

        def stop(self, message: Any | None = None, state: Any | None = None) -> None:
            _run_sync(self._bot.aclose())

        def _initial_request(self, message: Any, history: list[Any]) -> str:
            history_text = "\n".join(
                f"{getattr(item, 'role', 'participant')}: {getattr(item, 'content', '')}"
                for item in history
                if getattr(item, "content", None)
            )
            prior_context = f"\n\nPrior conversation:\n{history_text}" if history_text else ""
            return (
                "You are a customer-service agent in a benchmark environment. "
                "Follow this domain policy exactly and use only the provided environment tools."
                f"\n\nDomain policy:\n{self.domain_policy}{prior_context}"
                f"\n\nCustomer message:\n{message.content or ''}"
            )

    return RuiClawTauAgent


def _tool_calls(metadata: dict[str, Any], tool_call_type: type) -> list[Any]:
    calls: list[Any] = []
    for raw in metadata.get("_external_tool_calls", []):
        function = raw.get("function", {})
        arguments = function.get("arguments", "{}")
        if isinstance(arguments, str):
            arguments = json.loads(arguments)
        calls.append(tool_call_type(
            id=str(raw["id"]),
            name=str(function["name"]),
            arguments=arguments,
            requestor="assistant",
        ))
    return calls


def _tool_results(message: Any, tool_message_type: type, multi_tool_message_type: type) -> list[dict[str, str]]:
    messages = message.tool_messages if isinstance(message, multi_tool_message_type) else [message]
    if not all(isinstance(item, tool_message_type) for item in messages):
        raise TypeError("Tau must resume RuiClaw with tool result messages")
    return [
        {
            "tool_call_id": str(item.id),
            "name": "",
            "content": str(item.content or ""),
        }
        for item in messages
    ]


def _run_sync(coro: Any) -> Any:
    """Tau's half-duplex API is synchronous; benchmark runners call it off-loop."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    raise RuntimeError("tau2 adapter requires Tau's synchronous half-duplex runner")
