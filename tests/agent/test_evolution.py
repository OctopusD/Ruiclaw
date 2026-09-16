from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from ruiclaw.agent.evolution import (
    EvolutionBatch,
    EvolutionCoordinator,
    EvolutionFileJournal,
    EvolutionObservation,
    EvolutionSettings,
    EvolutionStateStore,
    observation_from_run,
)
from ruiclaw.agent.loop import AgentLoop
from ruiclaw.bus.queue import MessageBus
from ruiclaw.providers.base import GenerationSettings, LLMResponse


def _observation(
    number: int,
    *,
    memory_turns: int = 0,
    skill_tool_iterations: int = 0,
) -> EvolutionObservation:
    return EvolutionObservation(
        run_id=f"run-{number}",
        session_key=f"session-{number}",
        turn_id=f"turn-{number}",
        memory_turns=memory_turns,
        skill_tool_iterations=skill_tool_iterations,
    )


@pytest.mark.asyncio
async def test_memory_and_skill_thresholds_are_independent(tmp_path) -> None:
    store = EvolutionStateStore(
        tmp_path,
        EvolutionSettings(
            memory_valid_turns=2,
            skill_tool_iterations=3,
            skill_minimum_candidate_runs=2,
        ),
    )

    assert not await store.record(_observation(1, memory_turns=1, skill_tool_iterations=3))
    assert await store.record(_observation(2, memory_turns=1))

    memory = await store.claim()
    assert memory is not None
    assert memory.scope == "memory"
    await store.complete(memory, succeeded=True)

    assert await store.record(_observation(3, skill_tool_iterations=1))
    skills = await store.claim()
    assert skills is not None
    assert skills.scope == "skills"


@pytest.mark.asyncio
async def test_due_domains_are_claimed_as_one_combined_review(tmp_path) -> None:
    store = EvolutionStateStore(
        tmp_path,
        EvolutionSettings(
            memory_valid_turns=2,
            skill_tool_iterations=2,
            skill_minimum_candidate_runs=2,
        ),
    )

    await store.record(_observation(1, memory_turns=1, skill_tool_iterations=1))
    assert await store.record(_observation(2, memory_turns=1, skill_tool_iterations=1))

    batch = await store.claim()
    assert batch is not None
    assert batch.scope == "combined"
    assert batch.run_ids == ("run-1", "run-2")


@pytest.mark.asyncio
async def test_duplicate_run_does_not_increment_counters(tmp_path) -> None:
    store = EvolutionStateStore(
        tmp_path,
        EvolutionSettings(memory_valid_turns=2),
    )
    observation = _observation(1, memory_turns=1)

    assert not await store.record(observation)
    assert not await store.record(observation)
    snapshot = await store.snapshot()

    assert len(snapshot["memory_review"]["pending"]) == 1
    assert snapshot["memory_review"]["status"] == "accumulating"


@pytest.mark.asyncio
async def test_failed_review_keeps_candidates_for_retry(tmp_path) -> None:
    store = EvolutionStateStore(
        tmp_path,
        EvolutionSettings(memory_valid_turns=1),
    )
    await store.record(_observation(1, memory_turns=1))
    batch = await store.claim()
    assert batch is not None

    await store.complete(batch, succeeded=False, error="provider_error")
    snapshot = await store.snapshot()

    assert snapshot["memory_review"]["status"] == "review_pending"
    assert snapshot["memory_review"]["last_error"] == "provider_error"
    assert [item["run_id"] for item in snapshot["memory_review"]["pending"]] == ["run-1"]


@pytest.mark.asyncio
async def test_disabled_automatic_domain_can_still_be_forced(tmp_path) -> None:
    store = EvolutionStateStore(
        tmp_path,
        EvolutionSettings(memory_enabled=False, memory_valid_turns=1),
    )

    assert not await store.record(_observation(1, memory_turns=1))
    assert await store.claim("memory") is None
    batch = await store.claim("memory", force=True)

    assert batch is not None
    assert batch.scope == "memory"


@pytest.mark.asyncio
async def test_success_only_removes_claimed_candidates(tmp_path) -> None:
    store = EvolutionStateStore(
        tmp_path,
        EvolutionSettings(memory_valid_turns=1),
    )
    await store.record(_observation(1, memory_turns=1))
    batch = await store.claim()
    assert batch is not None
    await store.record(_observation(2, memory_turns=1))

    await store.complete(batch, succeeded=True)
    snapshot = await store.snapshot()

    assert [item["run_id"] for item in snapshot["memory_review"]["pending"]] == ["run-2"]
    assert snapshot["memory_review"]["status"] == "review_pending"


@pytest.mark.asyncio
async def test_coordinator_serializes_review_claims(tmp_path) -> None:
    store = EvolutionStateStore(
        tmp_path,
        EvolutionSettings(memory_valid_turns=1),
    )
    reviewed: list[str] = []

    async def reviewer(batch) -> bool:
        reviewed.append(batch.review_id)
        return True

    coordinator = EvolutionCoordinator(store, reviewer)
    await coordinator.observe(_observation(1, memory_turns=1))

    first, second = await __import__("asyncio").gather(
        coordinator.run(),
        coordinator.run(),
    )

    assert sum(item is not None for item in (first, second)) == 1
    assert len(reviewed) == 1


def test_observation_uses_model_and_tool_counts() -> None:
    observation = observation_from_run(
        run_id="run-1",
        session_key="session-1",
        turn_id="turn-1",
        status="succeeded",
        user_text="Please inspect and repair the project",
        report={"model_calls": 2, "tool_calls": 4},
    )

    assert observation is not None
    assert observation.memory_turns == 1
    assert observation.skill_tool_iterations == 4


def test_file_journal_records_changes_and_backs_up_skills(tmp_path) -> None:
    skill = tmp_path / "skills" / "example" / "SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text("before", encoding="utf-8")
    memory = tmp_path / "memory" / "MEMORY.md"
    memory.parent.mkdir(parents=True)
    memory.write_text("old fact", encoding="utf-8")
    batch = EvolutionBatch(
        review_id="evo-test",
        scope="combined",
        memory_candidates=(_observation(1, memory_turns=1),),
        skill_candidates=(_observation(1, skill_tool_iterations=2),),
    )
    journal = EvolutionFileJournal(tmp_path, batch)

    journal.capture_before()
    skill.write_text("after", encoding="utf-8")
    memory.write_text("new fact", encoding="utf-8")
    changed = journal.finish(succeeded=True)

    assert changed == ("memory/MEMORY.md", "skills/example/SKILL.md")
    assert (journal.review_dir / "before" / "skills/example/SKILL.md").read_text() == "before"
    manifest = json.loads(journal.manifest_path.read_text(encoding="utf-8"))
    assert manifest["status"] == "succeeded"
    assert manifest["input_run_ids"] == ["run-1"]
    assert "old fact" not in journal.manifest_path.read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_completed_user_run_triggers_non_recursive_evolution_run(tmp_path) -> None:
    provider = MagicMock()
    provider.get_default_model.return_value = "test-model"
    provider.generation = GenerationSettings()
    provider.chat_stream_with_retry = AsyncMock(
        return_value=LLMResponse(content="done", tool_calls=[], finish_reason="stop")
    )
    loop = AgentLoop(
        bus=MessageBus(),
        provider=provider,
        workspace=tmp_path,
        evolution_settings=EvolutionSettings(
            memory_valid_turns=1,
            skills_enabled=False,
        ),
    )

    await loop.process_direct("Remember this durable project decision", session_key="cli:test")
    await loop.aclose()

    state = await loop.evolution_status(tmp_path)
    assert state["memory_review"]["pending"] == []
    session = loop.sessions.read_session_snapshot("cli:test")
    assert session is not None
    tagged = [message for message in session.messages if message.get("_ruiclaw_run_id")]
    assert [message["role"] for message in tagged] == ["user", "assistant"]
    assert len({message["_ruiclaw_run_id"] for message in tagged}) == 1
    manifests = [
        json.loads(path.read_text(encoding="utf-8"))
        for path in (tmp_path / ".ruiclaw" / "runs").glob("*/manifest.json")
    ]
    assert [item["run_kind"] for item in manifests].count("agent") == 1
    assert [item["run_kind"] for item in manifests].count("evolution") == 1
    assert provider.chat_stream_with_retry.await_count == 2


@pytest.mark.parametrize(
    ("status", "text", "model_calls", "run_kind"),
    [
        ("failed", "Please inspect this", 1, "agent"),
        ("succeeded", "/status", 1, "agent"),
        ("succeeded", "Please inspect this", 0, "agent"),
        ("succeeded", "Please inspect this", 1, "evolution"),
    ],
)
def test_observation_rejects_internal_or_non_agent_runs(
    status: str,
    text: str,
    model_calls: int,
    run_kind: str,
) -> None:
    assert observation_from_run(
        run_id="run-1",
        session_key="session-1",
        turn_id="turn-1",
        status=status,
        user_text=text,
        report={"model_calls": model_calls, "tool_calls": 1},
        run_kind=run_kind,
    ) is None
