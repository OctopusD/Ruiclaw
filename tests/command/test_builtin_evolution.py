from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from ruiclaw.agent.evolution import EvolutionBatch, EvolutionObservation
from ruiclaw.bus.events import InboundMessage
from ruiclaw.command.builtin import cmd_evolve
from ruiclaw.command.router import CommandContext


class _Bus:
    def __init__(self) -> None:
        self.messages = []

    async def publish_outbound(self, message) -> None:
        self.messages.append(message)


def _state(*, memory_status: str = "accumulating") -> dict:
    return {
        "memory_review": {
            "status": memory_status,
            "pending": [{"memory_turns": 3}],
        },
        "skill_review": {
            "status": "accumulating",
            "pending": [{"skill_tool_iterations": 4}],
        },
        "current_review": None,
    }


def _context(tmp_path, *, args: str, state: dict, batch=None):
    msg = InboundMessage(channel="cli", sender_id="u1", chat_id="direct", content="/evolve")
    bus = _Bus()
    scheduled = []
    loop = SimpleNamespace(
        bus=bus,
        evolution_workspace=lambda _ctx: tmp_path,
        evolution_status=AsyncMock(return_value=state),
        run_evolution_review=AsyncMock(return_value=batch),
        schedule_background=scheduled.append,
    )
    return (
        CommandContext(
            msg=msg,
            session=None,
            key=msg.session_key,
            raw="/evolve",
            args=args,
            loop=loop,
        ),
        loop,
        bus,
        scheduled,
    )


@pytest.mark.asyncio
async def test_evolve_without_due_work_displays_status(tmp_path) -> None:
    ctx, loop, _bus, scheduled = _context(tmp_path, args="", state=_state())

    response = await cmd_evolve(ctx)

    assert "Memory: accumulating (3 candidate turns)" in response.content
    assert "4 tool iterations across 1 runs" in response.content
    assert scheduled == []
    loop.run_evolution_review.assert_not_awaited()


@pytest.mark.asyncio
async def test_evolve_memory_forces_review_and_reports_completion(tmp_path) -> None:
    observation = EvolutionObservation("run-1", "cli:test", "turn-1", memory_turns=1)
    batch = EvolutionBatch(
        review_id="evo-test",
        scope="memory",
        memory_candidates=(observation,),
        trigger="manual",
    )
    ctx, loop, bus, scheduled = _context(
        tmp_path,
        args="memory",
        state=_state(),
        batch=batch,
    )

    response = await cmd_evolve(ctx)
    assert response.content == "Evolution review queued..."
    assert len(scheduled) == 1

    await scheduled[0]

    loop.run_evolution_review.assert_awaited_once_with(tmp_path, "memory", force=True)
    assert bus.messages[0].content == "Evolution memory review completed (1 candidate runs)."
