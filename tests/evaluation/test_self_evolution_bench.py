from __future__ import annotations

import json
from pathlib import Path

import pytest

from ruiclaw.evaluation.self_evolution_bench import SelfEvolutionBenchmarkEvaluator


def test_self_evolution_benchmark_isolated_ab_and_audited(tmp_path: Path) -> None:
    artifact_path = tmp_path / "result" / "comparison.json"
    report_path = tmp_path / "result" / "comparison.md"
    workspace_root = tmp_path / "workspaces"

    artifact = SelfEvolutionBenchmarkEvaluator(
        artifact_path,
        report_path,
        workspace_root,
    ).run()

    results = artifact["results"]
    assert isinstance(results, dict)
    assert results["baseline"]["metrics"]["learning_holdout_pass_rate"] == 0.0
    assert results["evolved"]["metrics"]["learning_holdout_pass_rate"] == 1.0
    assert results["evolved"]["metrics"]["safety_pass_rate"] == 1.0
    assert results["evolved"]["metrics"]["regression_pass_rate"] == 1.0
    assert artifact["review"]["scope"] == "combined"
    assert artifact["review"]["state_drained"] is True
    assert artifact["decision"] == {"status": "passed", "automatic_promotion": False}
    assert json.loads(artifact_path.read_text(encoding="utf-8")) == artifact
    assert "does not\nmeasure live-model learning quality" in report_path.read_text(
        encoding="utf-8"
    )
    assert not (tmp_path / ".ruiclaw").exists()


def test_self_evolution_benchmark_rejects_runtime_data_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))

    with pytest.raises(ValueError, match=r"outside ~/.ruiclaw"):
        SelfEvolutionBenchmarkEvaluator(
            tmp_path / "comparison.json",
            tmp_path / "comparison.md",
            tmp_path / ".ruiclaw" / "bench",
        )

    with pytest.raises(ValueError, match=r"artifact must be outside ~/.ruiclaw"):
        SelfEvolutionBenchmarkEvaluator(
            tmp_path / ".ruiclaw" / "comparison.json",
            tmp_path / "comparison.md",
            tmp_path / "bench",
        )


def test_self_evolution_benchmark_can_remove_only_isolated_workspaces(
    tmp_path: Path,
) -> None:
    workspace_root = tmp_path / "workspaces"
    marker = workspace_root / "keep.txt"
    workspace_root.mkdir()
    marker.write_text("keep", encoding="utf-8")

    SelfEvolutionBenchmarkEvaluator(
        tmp_path / "comparison.json",
        tmp_path / "comparison.md",
        workspace_root,
        keep_workspaces=False,
    ).run()

    assert marker.read_text(encoding="utf-8") == "keep"
    assert not (workspace_root / "baseline").exists()
    assert not (workspace_root / "evolved").exists()
