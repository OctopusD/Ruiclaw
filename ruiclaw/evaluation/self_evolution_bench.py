"""Isolated deterministic A/B benchmark for the self-evolution pipeline."""

from __future__ import annotations

import asyncio
import json
import os
import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast
from uuid import uuid4

from ruiclaw.agent.context import ContextBuilder, TranscriptInput
from ruiclaw.agent.evolution import (
    EvolutionBatch,
    EvolutionCoordinator,
    EvolutionFileJournal,
    EvolutionObservation,
    EvolutionSettings,
    EvolutionStateStore,
)

DEFAULT_SELF_EVOLUTION_ARTIFACT_PATH = Path(
    "benchmarks/results/ruiclaw-self-evolution-v1/comparison.json"
)
DEFAULT_SELF_EVOLUTION_REPORT_PATH = Path(
    "benchmarks/results/ruiclaw-self-evolution-v1/comparison.md"
)
DEFAULT_SELF_EVOLUTION_WORKSPACE_ROOT = Path(
    "benchmarks/results/ruiclaw-self-evolution-v1/workspaces"
)

_MEMORY_TEXT = (
    "# Long-term Memory\n\n"
    "- Responses must be concise and in Chinese.\n"
    "- This project uses uv; do not use pip.\n"
)
_SKILL_TEXT = """---
name: verified-python-workflow
description: Verify Python changes with the project's required lint and test sequence.
always: true
---

# Verified Python workflow

1. Run `uv run --no-sync ruff check ruiclaw tests` before the test suite.
2. Run targeted pytest tests before broader regression tests.
"""
_TRANSIENT_NOISE = "TEMPORARY_BENCHMARK_SECRET"


def _atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        with open(temporary, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def _resolve_outside_runtime(path: Path, *, label: str) -> Path:
    resolved = path.expanduser().resolve()
    runtime = (Path.home().resolve() / ".ruiclaw").resolve()
    if resolved == runtime or runtime in resolved.parents:
        raise ValueError(f"self-evolution benchmark {label} must be outside ~/.ruiclaw")
    return resolved


def _assert_safe_workspace_root(path: Path) -> Path:
    resolved = _resolve_outside_runtime(path, label="workspace")
    if resolved in {Path(resolved.anchor), Path.home().resolve()}:
        raise ValueError("self-evolution benchmark workspace root is too broad")
    return resolved


def _reset_workspace(path: Path) -> None:
    if path.exists():
        shutil.rmtree(path)
    (path / "memory").mkdir(parents=True)
    (path / "skills").mkdir()
    (path / "SOUL.md").write_text(
        "# Benchmark Agent\n\nYou are the RuiClaw benchmark agent.\n",
        encoding="utf-8",
    )
    (path / "USER.md").write_text(
        "# Benchmark User\n\nBenchmark user profile.\n",
        encoding="utf-8",
    )
    (path / "memory" / "MEMORY.md").write_text(
        "# Long-term Memory\n",
        encoding="utf-8",
    )


def _evaluate_holdout(workspace: Path) -> dict[str, object]:
    transcript = ContextBuilder(workspace).build_transcript(
        TranscriptInput(
            history=[],
            current_message="Update this Python project and verify the result.",
        )
    )
    prompt = str(transcript[0].get("content", ""))
    rows = [
        {
            "id": "memory-language-preference",
            "category": "memory",
            "passed": "Responses must be concise and in Chinese." in prompt,
        },
        {
            "id": "memory-package-manager",
            "category": "memory",
            "passed": "This project uses uv; do not use pip." in prompt,
        },
        {
            "id": "skill-lint-before-tests",
            "category": "skills",
            "passed": "uv run --no-sync ruff check ruiclaw tests" in prompt,
        },
        {
            "id": "skill-targeted-tests",
            "category": "skills",
            "passed": "targeted pytest tests before broader regression tests" in prompt,
        },
        {
            "id": "transient-noise-suppressed",
            "category": "safety",
            "passed": _TRANSIENT_NOISE not in prompt,
        },
        {
            "id": "soul-bootstrap-preserved",
            "category": "regression",
            "passed": "RuiClaw benchmark agent" in prompt,
        },
        {
            "id": "user-bootstrap-preserved",
            "category": "regression",
            "passed": "Benchmark user profile" in prompt,
        },
    ]
    learned = [row for row in rows if row["category"] in {"memory", "skills"}]
    safety = [row for row in rows if row["category"] == "safety"]
    regression = [row for row in rows if row["category"] == "regression"]
    return {
        "metrics": {
            "learning_holdout_pass_rate": sum(row["passed"] is True for row in learned)
            / len(learned),
            "safety_pass_rate": sum(row["passed"] is True for row in safety) / len(safety),
            "regression_pass_rate": sum(row["passed"] is True for row in regression)
            / len(regression),
            "overall_pass_rate": sum(row["passed"] is True for row in rows) / len(rows),
        },
        "rows": rows,
    }


async def _apply_evolution(workspace: Path) -> dict[str, object]:
    settings = EvolutionSettings(
        memory_valid_turns=2,
        skill_tool_iterations=4,
        skill_minimum_candidate_runs=2,
    )
    store = EvolutionStateStore(workspace, settings)
    review_evidence: dict[str, object] = {}

    async def reviewer(batch: EvolutionBatch) -> bool:
        journal = EvolutionFileJournal(workspace, batch)
        journal.capture_before()
        (workspace / "memory" / "MEMORY.md").write_text(_MEMORY_TEXT, encoding="utf-8")
        skill_path = workspace / "skills" / "verified-python-workflow" / "SKILL.md"
        skill_path.parent.mkdir(parents=True)
        skill_path.write_text(_SKILL_TEXT, encoding="utf-8")
        changed_files = journal.finish(succeeded=True)
        review_evidence.update({
            "review_id": batch.review_id,
            "scope": batch.scope,
            "trigger": batch.trigger,
            "input_run_ids": list(batch.run_ids),
            "changed_files": list(changed_files),
            "journal": str(journal.manifest_path.relative_to(workspace)),
        })
        return True

    coordinator = EvolutionCoordinator(store, reviewer)
    observations = (
        EvolutionObservation("learn-memory-1", "bench:memory-1", "turn-1", memory_turns=1),
        EvolutionObservation(
            "learn-skill-1",
            "bench:skill-1",
            "turn-2",
            skill_tool_iterations=2,
        ),
        EvolutionObservation(
            "learn-combined-1",
            "bench:combined-1",
            "turn-3",
            memory_turns=1,
            skill_tool_iterations=2,
        ),
    )
    due = False
    for observation in observations:
        due = await coordinator.observe(observation)
    if not due:
        raise RuntimeError("self-evolution fixture did not reach its configured thresholds")
    batch = await coordinator.run()
    if batch is None or batch.scope != "combined":
        raise RuntimeError("self-evolution fixture did not claim one combined review")
    state = await store.snapshot()
    review_evidence["state_drained"] = (
        not cast(dict[str, Any], state["memory_review"])["pending"]
        and not cast(dict[str, Any], state["skill_review"])["pending"]
        and state["current_review"] is None
    )
    return review_evidence


def _markdown_report(artifact: dict[str, object]) -> str:
    results = cast(dict[str, dict[str, object]], artifact["results"])
    baseline = cast(dict[str, float], results["baseline"]["metrics"])
    evolved = cast(dict[str, float], results["evolved"]["metrics"])
    comparison = cast(dict[str, float], artifact["comparison"])
    return "\n".join([
        "# RuiClaw Self-Evolution A/B Report",
        "",
        "This deterministic fixture validates the isolated evolution pipeline. It does not",
        "measure live-model learning quality and must not be reported as a model benchmark.",
        "",
        "| Group | Learning holdout | Safety | Regression | Overall |",
        "| --- | ---: | ---: | ---: | ---: |",
        f"| baseline (Evolution off) | {baseline['learning_holdout_pass_rate']:.0%} | {baseline['safety_pass_rate']:.0%} | {baseline['regression_pass_rate']:.0%} | {baseline['overall_pass_rate']:.0%} |",
        f"| evolved (Evolution on) | {evolved['learning_holdout_pass_rate']:.0%} | {evolved['safety_pass_rate']:.0%} | {evolved['regression_pass_rate']:.0%} | {evolved['overall_pass_rate']:.0%} |",
        "",
        f"Learning-evidence availability delta: {comparison['learning_holdout_delta']:+.0%}",
        "Reviewer: `scripted_deterministic` (no model calls or token-cost claim)",
        "Automatic promotion: `false`",
        "",
    ])


class SelfEvolutionBenchmarkEvaluator:
    def __init__(
        self,
        artifact_path: Path = DEFAULT_SELF_EVOLUTION_ARTIFACT_PATH,
        report_path: Path = DEFAULT_SELF_EVOLUTION_REPORT_PATH,
        workspace_root: Path = DEFAULT_SELF_EVOLUTION_WORKSPACE_ROOT,
        *,
        keep_workspaces: bool = True,
    ) -> None:
        self.artifact_path = _resolve_outside_runtime(artifact_path, label="artifact")
        self.report_path = _resolve_outside_runtime(report_path, label="report")
        self.workspace_root = _assert_safe_workspace_root(workspace_root)
        self.keep_workspaces = keep_workspaces

    def run(self) -> dict[str, object]:
        baseline_workspace = self.workspace_root / "baseline"
        evolved_workspace = self.workspace_root / "evolved"
        self.workspace_root.mkdir(parents=True, exist_ok=True)
        _reset_workspace(baseline_workspace)
        _reset_workspace(evolved_workspace)

        baseline = _evaluate_holdout(baseline_workspace)
        review = asyncio.run(_apply_evolution(evolved_workspace))
        evolved = _evaluate_holdout(evolved_workspace)
        baseline_metrics = cast(dict[str, float], baseline["metrics"])
        evolved_metrics = cast(dict[str, float], evolved["metrics"])
        gates = [
            {
                "id": "learning_improves",
                "passed": evolved_metrics["learning_holdout_pass_rate"]
                > baseline_metrics["learning_holdout_pass_rate"],
            },
            {
                "id": "safety_no_regression",
                "passed": evolved_metrics["safety_pass_rate"]
                >= baseline_metrics["safety_pass_rate"],
            },
            {
                "id": "regression_no_regression",
                "passed": evolved_metrics["regression_pass_rate"]
                >= baseline_metrics["regression_pass_rate"],
            },
            {"id": "combined_review", "passed": review.get("scope") == "combined"},
            {"id": "state_drained", "passed": review.get("state_drained") is True},
        ]
        artifact: dict[str, object] = {
            "schema_version": 1,
            "benchmark": "ruiclaw-self-evolution-v1",
            "generated_at": datetime.now(UTC).isoformat(),
            "method": "isolated_deterministic_ab_with_scripted_reviewer",
            "baseline": "same RuiClaw runtime with Evolution disabled",
            "external_baseline": {
                "nanobot_source_available": Path("nanobot源码").is_dir(),
                "included": False,
                "reason": "nanobot is a separate external reference, not the causal A/B baseline",
            },
            "datasets": {
                "learning_runs": 3,
                "memory_holdouts": 2,
                "skill_holdouts": 2,
                "safety_scenarios": 1,
                "regression_scenarios": 2,
            },
            "results": {"baseline": baseline, "evolved": evolved},
            "comparison": {
                "learning_holdout_delta": (
                    evolved_metrics["learning_holdout_pass_rate"]
                    - baseline_metrics["learning_holdout_pass_rate"]
                ),
            },
            "review": review,
            "gates": gates,
            "decision": {
                "status": "passed" if all(gate["passed"] is True for gate in gates) else "failed",
                "automatic_promotion": False,
            },
            "limitations": [
                "The reviewer is scripted and deterministic.",
                "No LLM calls, token usage, cost, or live-task Pass@1 are measured.",
                "Use a live-model holdout before claiming self-evolution quality gains.",
            ],
            "isolation": {
                "workspace_root": str(self.workspace_root),
                "protected_runtime_root": str((Path.home() / ".ruiclaw").resolve()),
                "writes_confined_to_benchmark_workspaces": True,
            },
        }
        _atomic_write(
            self.artifact_path,
            json.dumps(artifact, ensure_ascii=False, indent=2) + "\n",
        )
        _atomic_write(self.report_path, _markdown_report(artifact))
        if not self.keep_workspaces:
            shutil.rmtree(baseline_workspace)
            shutil.rmtree(evolved_workspace)
        return artifact


def run_self_evolution_benchmark(
    artifact_path: Path = DEFAULT_SELF_EVOLUTION_ARTIFACT_PATH,
    report_path: Path = DEFAULT_SELF_EVOLUTION_REPORT_PATH,
    workspace_root: Path = DEFAULT_SELF_EVOLUTION_WORKSPACE_ROOT,
    *,
    keep_workspaces: bool = True,
) -> dict[str, object]:
    return SelfEvolutionBenchmarkEvaluator(
        artifact_path,
        report_path,
        workspace_root,
        keep_workspaces=keep_workspaces,
    ).run()
