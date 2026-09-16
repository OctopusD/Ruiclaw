"""Offline policy comparison and release gates for RuiClaw."""

from __future__ import annotations

import json
import os
import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import TypedDict, cast
from uuid import uuid4

from ruiclaw.agent.context import ContextBuilder, TranscriptInput
from ruiclaw.agent.working_memory import WORKING_MEMORY_META, WorkingMemoryStore

DEFAULT_EVOLVER_ARTIFACT_PATH = Path(
    "benchmarks/results/ruiclaw-evolver-v1/comparison.json"
)
DEFAULT_EVOLVER_REPORT_PATH = Path(
    "benchmarks/results/ruiclaw-evolver-v1/comparison.md"
)
DEFAULT_EVOLVER_WORKSPACE_ROOT = Path(
    "benchmarks/results/ruiclaw-evolver-v1/workspaces"
)

_TRAIN_TASKS = (
    ("runtime/agent_loop.py", "RuiClaw runtime coordinates session admission and routing."),
    ("runtime/runner.py", "RuiClaw runtime executes model iterations and tool calls."),
    ("context/context.py", "RuiClaw context assembles prompt sections and evidence."),
    ("memory/working_memory.py", "RuiClaw memory retrieves fresh workspace evidence."),
)
_HOLDOUT_TASKS = (
    ("recovery/coordinator.py", "RuiClaw recovery classifies interrupted checkpoints."),
    ("observability/run_ledger.py", "RuiClaw observability persists execution evidence."),
)


class Policy(TypedDict):
    name: str
    working_memory_top_k: int


class PolicyMetrics(TypedDict):
    train_pass_rate: float
    holdout_pass_rate: float
    stale_suppression_rate: float
    working_memory_chars: int


class PolicyResult(TypedDict):
    policy: Policy
    metrics: PolicyMetrics
    rows: list[dict[str, object]]
    stale_case: dict[str, object]


class EvolverDecision(TypedDict):
    status: str
    automatic_promotion: bool
    statistical_confidence: str
    reason: str


class EvolverArtifact(TypedDict):
    schema_version: int
    benchmark: str
    generated_at: str
    method: str
    search: dict[str, object]
    datasets: dict[str, int]
    results: dict[str, PolicyResult]
    comparison: dict[str, float]
    gates: list[dict[str, object]]
    decision: EvolverDecision


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


def _working_memory_section(prompt: str) -> str:
    return next(
        (section for section in prompt.split("\n\n---\n\n") if section.startswith("# Relevant Working Memory")),
        "",
    )


def _evaluate_policy(policy: Policy, workspace: Path) -> PolicyResult:
    workspace.mkdir(parents=True)
    store = WorkingMemoryStore(workspace)
    tasks = (*_TRAIN_TASKS, *_HOLDOUT_TASKS)
    for relative, summary in tasks:
        path = workspace / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"# {relative}\n{summary}\n", encoding="utf-8")
        if store.remember_file(path, summary) is None:
            raise RuntimeError(f"failed to create working memory for {relative}")

    builder = ContextBuilder(
        workspace,
        disabled_skills=[],
        working_memory_limit=policy["working_memory_top_k"],
    )
    rows: list[dict[str, object]] = []
    working_memory_chars = 0
    for index, (relative, _summary) in enumerate(tasks):
        split = "train" if index < len(_TRAIN_TASKS) else "holdout"
        transcript = builder.build_transcript(TranscriptInput(
            history=[],
            current_message=f"Continue RuiClaw work in {relative}",
        ))
        system = transcript[0]
        system_metadata = system.get("_meta")
        if not isinstance(system_metadata, dict):
            raise RuntimeError("context builder omitted system metadata")
        metadata = cast(dict[str, object], system_metadata).get(WORKING_MEMORY_META)
        if not isinstance(metadata, dict):
            raise RuntimeError("context builder omitted working-memory evidence")
        selected_value = cast(dict[str, object], metadata).get("selected")
        if not isinstance(selected_value, list):
            raise RuntimeError("context builder returned invalid working-memory evidence")
        selected_items = cast(list[object], selected_value)
        if not all(isinstance(item, dict) for item in selected_items):
            raise RuntimeError("context builder returned invalid working-memory evidence")
        selected = cast(list[dict[str, object]], selected_items)
        selected_sources = [str(item.get("source", "")) for item in selected]
        section = _working_memory_section(str(system.get("content", "")))
        working_memory_chars += len(section)
        rows.append({
            "id": relative,
            "split": split,
            "passed": bool(selected_sources) and selected_sources[0] == relative,
            "selected_sources": selected_sources,
            "working_memory_chars": len(section),
        })

    stale_relative = _HOLDOUT_TASKS[0][0]
    (workspace / stale_relative).write_text("# externally changed\n", encoding="utf-8")
    stale = store.retrieve(
        f"Continue RuiClaw work in {stale_relative}",
        limit=policy["working_memory_top_k"],
    )
    stale_sources = [item["source"] for item in stale.selected]
    stale_passed = stale.stale_rejected_count == 1 and stale_relative not in stale_sources

    train_rows = [row for row in rows if row["split"] == "train"]
    holdout_rows = [row for row in rows if row["split"] == "holdout"]
    metrics: PolicyMetrics = {
        "train_pass_rate": sum(row["passed"] is True for row in train_rows) / len(train_rows),
        "holdout_pass_rate": sum(row["passed"] is True for row in holdout_rows) / len(holdout_rows),
        "stale_suppression_rate": float(stale_passed),
        "working_memory_chars": working_memory_chars,
    }
    return {
        "policy": policy,
        "metrics": metrics,
        "rows": rows,
        "stale_case": {
            "passed": stale_passed,
            "stale_rejected_count": stale.stale_rejected_count,
            "selected_sources": stale_sources,
        },
    }


def evaluate_release_gates(
    baseline: PolicyMetrics,
    candidate: PolicyMetrics,
    *,
    minimum_context_reduction: float = 0.20,
) -> tuple[list[dict[str, object]], float]:
    baseline_chars = baseline["working_memory_chars"]
    reduction = (
        (baseline_chars - candidate["working_memory_chars"]) / baseline_chars
        if baseline_chars
        else 0.0
    )
    gates: list[dict[str, object]] = [
        {
            "id": "train_no_regression",
            "passed": candidate["train_pass_rate"] >= baseline["train_pass_rate"],
        },
        {
            "id": "holdout_no_regression",
            "passed": candidate["holdout_pass_rate"] >= baseline["holdout_pass_rate"],
        },
        {
            "id": "stale_safety_no_regression",
            "passed": candidate["stale_suppression_rate"] >= baseline["stale_suppression_rate"],
        },
        {
            "id": "context_reduction",
            "threshold": minimum_context_reduction,
            "actual": reduction,
            "passed": reduction >= minimum_context_reduction,
        },
    ]
    return gates, reduction


def _markdown_report(
    baseline: PolicyMetrics,
    candidate: PolicyMetrics,
    decision: EvolverDecision,
    reduction: float,
) -> str:
    return "\n".join([
        "# RuiClaw Evolver Lite Report",
        "",
        "This deterministic offline experiment compares one isolated candidate with the baseline.",
        "It does not modify runtime configuration or deploy the candidate.",
        "",
        "| Policy | Train pass | Holdout pass | Stale safety | Working-memory chars |",
        "| --- | ---: | ---: | ---: | ---: |",
        f"| baseline (Top-K 3) | {baseline['train_pass_rate']:.0%} | {baseline['holdout_pass_rate']:.0%} | {baseline['stale_suppression_rate']:.0%} | {baseline['working_memory_chars']} |",
        f"| candidate (Top-K 1) | {candidate['train_pass_rate']:.0%} | {candidate['holdout_pass_rate']:.0%} | {candidate['stale_suppression_rate']:.0%} | {candidate['working_memory_chars']} |",
        "",
        f"Context reduction: {reduction:.2%}",
        f"Decision: `{decision['status']}`",
        "Automatic promotion: `false`",
        "Statistical confidence: `insufficient_small_holdout`",
        "",
    ])


class EvolverEvaluator:
    def __init__(
        self,
        artifact_path: Path = DEFAULT_EVOLVER_ARTIFACT_PATH,
        report_path: Path = DEFAULT_EVOLVER_REPORT_PATH,
        workspace_root: Path = DEFAULT_EVOLVER_WORKSPACE_ROOT,
    ) -> None:
        self.artifact_path = artifact_path.resolve()
        self.report_path = report_path.resolve()
        self.workspace_root = workspace_root.resolve()

    def run(self) -> EvolverArtifact:
        if self.workspace_root.exists():
            shutil.rmtree(self.workspace_root)
        baseline = _evaluate_policy(
            {"name": "baseline", "working_memory_top_k": 3},
            self.workspace_root / "baseline",
        )
        candidate = _evaluate_policy(
            {"name": "candidate-top-k-1", "working_memory_top_k": 1},
            self.workspace_root / "candidate-top-k-1",
        )
        baseline_metrics = baseline["metrics"]
        candidate_metrics = candidate["metrics"]
        gates, reduction = evaluate_release_gates(baseline_metrics, candidate_metrics)
        gates_passed = all(gate["passed"] is True for gate in gates)
        decision: EvolverDecision = {
            "status": "recommended_for_manual_review" if gates_passed else "rejected",
            "automatic_promotion": False,
            "statistical_confidence": "insufficient_small_holdout",
            "reason": (
                "all deterministic gates passed"
                if gates_passed
                else "one or more release gates failed"
            ),
        }
        artifact: EvolverArtifact = {
            "schema_version": 1,
            "benchmark": "ruiclaw-evolver-v1",
            "generated_at": datetime.now(UTC).isoformat(),
            "method": (
                "Deterministic offline baseline/candidate comparison using isolated "
                "workspaces and the production WorkingMemoryStore and ContextBuilder."
            ),
            "search": {
                "candidate_budget": 1,
                "candidates_evaluated": 1,
                "candidate_isolation": True,
            },
            "datasets": {
                "train_tasks": len(_TRAIN_TASKS),
                "holdout_tasks": len(_HOLDOUT_TASKS),
                "safety_scenarios": 1,
            },
            "results": {"baseline": baseline, "candidate": candidate},
            "comparison": {"working_memory_char_reduction": reduction},
            "gates": gates,
            "decision": decision,
        }
        _atomic_write(
            self.artifact_path,
            json.dumps(artifact, ensure_ascii=False, indent=2) + "\n",
        )
        _atomic_write(
            self.report_path,
            _markdown_report(baseline_metrics, candidate_metrics, decision, reduction),
        )
        return artifact


def run_evolver_evaluation(
    artifact_path: Path = DEFAULT_EVOLVER_ARTIFACT_PATH,
    report_path: Path = DEFAULT_EVOLVER_REPORT_PATH,
    workspace_root: Path = DEFAULT_EVOLVER_WORKSPACE_ROOT,
) -> EvolverArtifact:
    return EvolverEvaluator(artifact_path, report_path, workspace_root).run()
