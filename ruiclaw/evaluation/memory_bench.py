"""Deterministic benchmark for RuiClaw working-memory retrieval."""

from __future__ import annotations

import json
import os
import shutil
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from ruiclaw.agent.context import ContextBuilder, TranscriptInput
from ruiclaw.agent.working_memory import WorkingMemoryStore

DEFAULT_MEMORY_ARTIFACT_PATH = Path(
    "benchmarks/results/ruiclaw-memory-v1/result.json"
)
DEFAULT_MEMORY_WORKSPACE_ROOT = Path(
    "benchmarks/results/ruiclaw-memory-v1/workspace"
)

_TASKS = (
    ("agent_loop.py", "AgentLoop coordinates session admission and message routing."),
    ("runner.py", "AgentRunner executes model iterations and tool calls."),
    ("context.py", "ContextBuilder assembles prompt sections and source metadata."),
    ("memory.py", "Dream archives conversations into durable long-term memory."),
    ("recovery.py", "RecoveryCoordinator classifies interrupted run checkpoints."),
    ("run_ledger.py", "RunLedger persists append-only execution evidence."),
)


class MemoryBenchmarkEvaluator:
    def __init__(
        self,
        artifact_path: Path = DEFAULT_MEMORY_ARTIFACT_PATH,
        workspace_root: Path = DEFAULT_MEMORY_WORKSPACE_ROOT,
    ) -> None:
        self.artifact_path = artifact_path.resolve()
        self.workspace_root = workspace_root.resolve()

    def run(self) -> dict[str, object]:
        if self.workspace_root.exists():
            shutil.rmtree(self.workspace_root)
        self.workspace_root.mkdir(parents=True)
        store = WorkingMemoryStore(self.workspace_root)
        total_read_chars = 0
        for filename, summary in _TASKS:
            path = self.workspace_root / filename
            body = f"# {filename}\n" + (summary + "\n") * 20
            path.write_text(body, encoding="utf-8")
            total_read_chars += len(body)
            if store.remember_file(path, summary) is None:
                raise AssertionError(f"failed to remember {filename}")

        rows: list[dict[str, object]] = []
        hit_count = 0
        precise_count = 0
        for filename, _summary in _TASKS:
            query = f"Continue work in {filename}"
            retrieval = store.retrieve(query, limit=1)
            hit = len(retrieval.selected) == 1
            precise = hit and retrieval.selected[0]["source"] == filename
            hit_count += int(hit)
            precise_count += int(precise)
            transcript = ContextBuilder(self.workspace_root).build_transcript(
                TranscriptInput(history=[], current_message=query)
            )
            injected = "# Relevant Working Memory" in str(transcript[0].get("content", ""))
            rows.append({
                "id": filename,
                "passed": hit and precise and injected,
                "selected_source": retrieval.selected[0]["source"] if hit else None,
                "score": retrieval.selected[0]["score"] if hit else 0,
                "injected": injected,
            })

        stale_path = self.workspace_root / _TASKS[0][0]
        stale_path.write_text("# externally changed\n", encoding="utf-8")
        stale = store.retrieve(f"Continue work in {_TASKS[0][0]}", limit=1)
        stale_suppressed = not stale.selected and stale.stale_rejected_count == 1
        passed = sum(row["passed"] is True for row in rows) + int(stale_suppressed)
        total_scenarios = len(rows) + 1
        artifact: dict[str, object] = {
            "schema_version": 1,
            "benchmark": "ruiclaw-memory-v1",
            "generated_at": datetime.now(UTC).isoformat(),
            "method": (
                "Deterministic production-store ablation: memory_on reuses a relevant "
                "fresh summary; memory_off simulates the required source read."
            ),
            "summary": {
                "total_scenarios": total_scenarios,
                "passed": passed,
                "pass_rate": passed / total_scenarios,
                "task_count": len(_TASKS),
                "memory_hit_rate": hit_count / len(_TASKS),
                "top1_precision": precise_count / len(_TASKS),
                "stale_suppression_rate": float(stale_suppressed),
                "memory_on_repeated_reads": len(_TASKS) - hit_count,
                "memory_off_repeated_reads": len(_TASKS),
                "avoided_read_chars": total_read_chars,
            },
            "rows": rows,
            "stale_case": {
                "passed": stale_suppressed,
                "stale_rejected_count": stale.stale_rejected_count,
                "selected_count": len(stale.selected),
            },
        }
        self.artifact_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.artifact_path.with_name(
            f".{self.artifact_path.name}.{uuid4().hex}.tmp"
        )
        try:
            with open(temporary, "w", encoding="utf-8") as handle:
                json.dump(artifact, handle, ensure_ascii=False, indent=2)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.artifact_path)
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise
        return artifact


def run_memory_benchmark(
    artifact_path: Path = DEFAULT_MEMORY_ARTIFACT_PATH,
    workspace_root: Path = DEFAULT_MEMORY_WORKSPACE_ROOT,
) -> dict[str, object]:
    return MemoryBenchmarkEvaluator(artifact_path, workspace_root).run()
