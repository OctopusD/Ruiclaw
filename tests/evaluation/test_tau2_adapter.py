"""Tests for the optional Tau adapter without importing Tau itself."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ruiclaw.evaluation.tau2_adapter import create_tau2_agent
from ruiclaw.sdk.types import RunResult


@dataclass
class _TauCall:
    id: str
    name: str
    arguments: dict[str, Any]
    requestor: str


@dataclass
class _ToolMessage:
    id: str
    content: str


@dataclass
class _MultiToolMessage:
    tool_messages: list[_ToolMessage]


class _AssistantMessage:
    @classmethod
    def text(cls, content: str, *, tool_calls: list[_TauCall] | None = None):
        return {"content": content, "tool_calls": tool_calls or []}


class _HalfDuplexAgent:
    def __init__(self, tools, domain_policy):
        self.tools = tools
        self.domain_policy = domain_policy


class _EnvironmentTool:
    openai_schema = {
        "type": "function",
        "function": {
            "name": "lookup_order",
            "description": "Find an order.",
            "parameters": {"type": "object", "properties": {}},
        },
    }


class _Bot:
    def __init__(self):
        self.received: list[dict[str, str]] = []
        self.user_messages: list[str] = []

    async def run_external_tools(self, message, **_kwargs):
        self.user_messages.append(message)
        return RunResult("", metadata={"_external_tool_calls": [{
            "id": "call_1",
            "function": {"name": "lookup_order", "arguments": "{}"},
        }]})

    async def continue_external_tools(self, results, **_kwargs):
        self.received = results
        return RunResult("Your order is on the way.")

    async def aclose(self):
        return None


def test_tau_adapter_exchanges_tool_calls_and_results():
    bot = _Bot()
    agent_type = create_tau2_agent(
        _HalfDuplexAgent,
        _AssistantMessage,
        _TauCall,
        _ToolMessage,
        _MultiToolMessage,
        bot_factory=lambda **_kwargs: bot,
    )
    agent = agent_type([_EnvironmentTool()], "policy")
    state = agent.get_init_state()

    first, state = agent.generate_next_message(type("User", (), {"content": "where is it?"})(), state)
    assert first["content"] == ""
    assert first["tool_calls"][0].name == "lookup_order"

    next_user, state = agent.generate_next_message(
        type("User", (), {"content": "I want to cancel instead."})(), state,
    )
    assert next_user["tool_calls"][0].name == "lookup_order"
    assert bot.user_messages[-1] == "I want to cancel instead."

    final, _ = agent.generate_next_message(_ToolMessage("call_1", "shipped"), state)
    assert bot.received == [{"tool_call_id": "call_1", "name": "", "content": "shipped"}]
    assert final == {"content": "Your order is on the way.", "tool_calls": []}
