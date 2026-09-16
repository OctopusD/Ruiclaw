from __future__ import annotations

import json
from pathlib import Path

import pytest

from ruiclaw.agent.context import ContextBuilder, TranscriptInput
from ruiclaw.agent.hook import AgentHookContext
from ruiclaw.agent.working_memory import WORKING_MEMORY_META, WorkingMemoryHook, WorkingMemoryStore
from ruiclaw.providers.base import ToolCallRequest


def test_working_memory_retrieves_relevant_file_and_rejects_stale_summary(
    tmp_path: Path,
) -> None:
    source = tmp_path / "ruiclaw" / "agent" / "loop.py"
    source.parent.mkdir(parents=True)
    source.write_text("class AgentLoop:\n    pass\n", encoding="utf-8")
    store = WorkingMemoryStore(tmp_path)

    entry = store.remember_file(
        source,
        "AgentLoop coordinates session admission and runner execution.",
    )
    assert entry is not None
    retrieval = store.retrieve("change ruiclaw/agent/loop.py AgentLoop")
    assert [item["memory_id"] for item in retrieval.selected] == [entry["memory_id"]]
    assert retrieval.selected[0]["reasons"] == [
        "path_match",
        "tag_match",
        "keyword_overlap",
    ]

    source.write_text("class AgentLoop:\n    changed = True\n", encoding="utf-8")
    stale = store.retrieve("change ruiclaw/agent/loop.py AgentLoop")
    assert stale.selected == ()
    assert stale.stale_rejected_count == 1


@pytest.mark.asyncio
async def test_working_memory_hook_captures_successful_read_without_secrets(
    tmp_path: Path,
) -> None:
    source = tmp_path / "config.py"
    source.write_text("API_KEY = 'not-persisted'\nMODE = 'test'\n", encoding="utf-8")
    hook = WorkingMemoryHook(tmp_path)

    await hook.after_execute_tool(
        AgentHookContext(iteration=0, messages=[]),
        ToolCallRequest(id="read-1", name="read_file", arguments={"path": "config.py"}),
        object(),
        {"path": "config.py"},
        "1: API_KEY = not-persisted\n2: MODE = test",
    )

    persisted = (tmp_path / ".ruiclaw" / "memory" / "entries.jsonl").read_text(
        encoding="utf-8"
    )
    assert "not-persisted" not in persisted
    assert "[REDACTED]" in persisted


def test_context_injects_relevant_memory_and_exposes_content_free_metadata(
    tmp_path: Path,
) -> None:
    source = tmp_path / "runner.py"
    source.write_text("def run():\n    return 'ok'\n", encoding="utf-8")
    store = WorkingMemoryStore(tmp_path)
    entry = store.remember_file(source, "runner.py executes the model loop")
    assert entry is not None

    messages = ContextBuilder(tmp_path).build_transcript(
        TranscriptInput(history=[], current_message="How does runner.py execute?")
    )
    system = messages[0]
    assert "# Relevant Working Memory" in system["content"]
    assert "runner.py executes the model loop" in system["content"]
    metadata = system["_meta"][WORKING_MEMORY_META]
    assert metadata["selected_count"] == 1
    assert metadata["selected"][0]["memory_id"] == entry["memory_id"]
    assert "content" not in metadata["selected"][0]
    assert json.loads(store.index_path.read_text(encoding="utf-8"))["schema_version"] == 1
