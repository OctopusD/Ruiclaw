from __future__ import annotations

import json
from pathlib import Path

from ruiclaw.evaluation.evolver import EvolverEvaluator, evaluate_release_gates


def test_evolver_compares_isolated_candidate_and_requires_manual_review(
    tmp_path: Path,
) -> None:
    artifact_path = tmp_path / "comparison.json"
    report_path = tmp_path / "comparison.md"
    artifact = EvolverEvaluator(
        artifact_path=artifact_path,
        report_path=report_path,
        workspace_root=tmp_path / "workspaces",
    ).run()

    assert artifact["search"] == {
        "candidate_budget": 1,
        "candidates_evaluated": 1,
        "candidate_isolation": True,
    }
    results = artifact["results"]
    assert isinstance(results, dict)
    baseline = results["baseline"]["metrics"]
    candidate = results["candidate"]["metrics"]
    assert baseline["train_pass_rate"] == candidate["train_pass_rate"] == 1.0
    assert baseline["holdout_pass_rate"] == candidate["holdout_pass_rate"] == 1.0
    assert candidate["stale_suppression_rate"] == 1.0
    assert candidate["working_memory_chars"] < baseline["working_memory_chars"]
    assert all(gate["passed"] is True for gate in artifact["gates"])
    assert artifact["decision"]["status"] == "recommended_for_manual_review"
    assert artifact["decision"]["automatic_promotion"] is False
    assert json.loads(artifact_path.read_text(encoding="utf-8")) == artifact
    assert "Automatic promotion: `false`" in report_path.read_text(encoding="utf-8")


def test_release_gates_reject_correctness_regression() -> None:
    baseline = {
        "train_pass_rate": 1.0,
        "holdout_pass_rate": 1.0,
        "stale_suppression_rate": 1.0,
        "working_memory_chars": 100,
    }
    candidate = {
        **baseline,
        "holdout_pass_rate": 0.5,
        "working_memory_chars": 50,
    }

    gates, reduction = evaluate_release_gates(baseline, candidate)

    assert reduction == 0.5
    assert next(gate for gate in gates if gate["id"] == "holdout_no_regression")[
        "passed"
    ] is False
