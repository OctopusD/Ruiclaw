from __future__ import annotations

import asyncio
import json
from pathlib import Path

from ruiclaw.evaluation.recovery_bench import RecoveryBenchmarkEvaluator


def test_recovery_benchmark_injects_faults_and_persists_evidence(tmp_path: Path) -> None:
    artifact_path = tmp_path / "result.json"
    artifact = asyncio.run(RecoveryBenchmarkEvaluator(
        artifact_path=artifact_path,
        workspace_root=tmp_path / "workspaces",
    ).run())

    assert artifact["summary"] == {
        "total_scenarios": 3,
        "passed": 3,
        "failed": 0,
        "recovery_success_rate": 1.0,
        "duplicate_side_effects": 0,
        "parent_run_link_rate": 1.0,
    }
    assert json.loads(artifact_path.read_text(encoding="utf-8")) == artifact

    rows_value = artifact["rows"]
    assert isinstance(rows_value, list)
    rows = {row["id"]: row for row in rows_value if isinstance(row, dict)}
    assert rows["uncertain_tool_not_replayed"]["side_effect_count"] == 0
    assert rows["completed_tool_resumes_once"]["side_effect_count"] == 1
    assert rows["completed_tool_resumes_once"]["parent_linked"] is True
    assert rows["final_answer_restored_without_replay"]["additional_model_calls"] == 0
