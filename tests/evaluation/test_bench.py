from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from ruiclaw.evaluation.bench import BenchmarkEvaluator, load_benchmark

BENCHMARK = Path("benchmarks/ruiclaw_tasks.json")


def test_load_benchmark_validates_fixed_schema() -> None:
    _, tasks = load_benchmark(BENCHMARK)

    assert len(tasks) == 6
    assert {task["category"] for task in tasks} == {
        "documentation",
        "text-edit",
        "tool-boundary",
        "context-governance",
        "observability",
    }
    assert all(task["step_budget"] >= len(task["script"]) for task in tasks)


def test_load_benchmark_rejects_fixture_escape(tmp_path: Path) -> None:
    benchmark_dir = tmp_path / "benchmarks"
    benchmark_dir.mkdir()
    path = benchmark_dir / "bad.json"
    path.write_text(json.dumps({
        "schema_version": 1,
        "tasks": [{
            "id": "escape",
            "prompt": "bad",
            "fixture_repo": "../../outside",
            "allowed_tools": ["read_file"],
            "step_budget": 1,
            "expected_artifact": "README.md",
            "verifier": {"kind": "run_bundle", "required_events": []},
            "category": "security",
            "script": [{"content": "done", "tool_calls": []}],
        }],
    }), encoding="utf-8")

    with pytest.raises(ValueError, match="escapes"):
        load_benchmark(path)


def test_benchmark_runs_isolated_tasks_and_persists_evidence(tmp_path: Path) -> None:
    artifact_path = tmp_path / "result.json"
    workspace_root = tmp_path / "workspaces"
    original = Path("benchmarks/fixtures/bench_repo_patch/sample.txt").read_text(encoding="utf-8")

    artifact = asyncio.run(BenchmarkEvaluator(
        benchmark_path=BENCHMARK,
        artifact_path=artifact_path,
        workspace_root=workspace_root,
    ).run())

    summary = artifact["summary"]
    assert isinstance(summary, dict)
    assert {key: summary[key] for key in (
        "total_tasks",
        "passed",
        "failed",
        "pass_rate",
        "within_budget_rate",
        "verifier_pass_rate",
        "model_calls",
        "cost_status",
        "estimated_total_cost_usd",
        "partial_estimated_total_cost_usd",
        "cost_per_successful_task_usd",
        "unknown_cost_tasks",
        "pricing_snapshot_id",
    )} == {
        "total_tasks": 6,
        "passed": 6,
        "failed": 0,
        "pass_rate": 1.0,
        "within_budget_rate": 1.0,
        "verifier_pass_rate": 1.0,
        "model_calls": 20,
        "cost_status": "unknown",
        "estimated_total_cost_usd": None,
        "partial_estimated_total_cost_usd": None,
        "cost_per_successful_task_usd": None,
        "unknown_cost_tasks": 6,
        "pricing_snapshot_id": "openai-standard-2026-09-13-v1",
    }
    assert int(summary["input_tokens"]) > 0
    assert int(summary["output_tokens"]) > 0
    assert artifact_path.exists()
    assert json.loads(artifact_path.read_text(encoding="utf-8")) == artifact
    assert Path("benchmarks/fixtures/bench_repo_patch/sample.txt").read_text(encoding="utf-8") == original

    rows = cast_rows(artifact["rows"])
    for row in rows:
        run_dir = workspace_root / str(row["run_dir"])
        assert (run_dir / "manifest.json").exists()
        assert (run_dir / "events.jsonl").exists()
        assert (run_dir / "report.json").exists()
        assert row["passed"] is True


def cast_rows(value: object) -> list[dict[str, object]]:
    assert isinstance(value, list)
    assert all(isinstance(row, dict) for row in value)
    return value
