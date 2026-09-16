"""Focused tests for MyTool runtime sync side effects."""

from unittest.mock import MagicMock

import pytest

from ruiclaw.agent.loop import AgentLoop
from ruiclaw.agent.tools.runtime_control import AgentRuntimeControl
from ruiclaw.agent.tools.self import MyTool
from ruiclaw.bus.queue import MessageBus


@pytest.mark.asyncio
async def test_my_tool_max_iterations_syncs_subagent_limit(tmp_path) -> None:
    provider = MagicMock()
    provider.get_default_model.return_value = "test-model"
    loop = AgentLoop(
        bus=MessageBus(),
        provider=provider,
        workspace=tmp_path,
        max_iterations=40,
    )
    tool = MyTool(runtime_control=AgentRuntimeControl(loop))

    result = await tool.execute(action="set", key="max_iterations", value=80)

    assert "Set max_iterations = 80" in result
    assert loop.max_iterations == 80
    assert loop.subagents.max_iterations == 80
