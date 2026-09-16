from __future__ import annotations

import asyncio
import json
from pathlib import Path

from ruiclaw.evaluation.tool_governance_bench import ToolGovernanceBenchmarkEvaluator


def test_tool_governance_benchmark_persists_measured_invariants(tmp_path: Path) -> None:
    artifact_path = tmp_path / "result.json"
    artifact = asyncio.run(ToolGovernanceBenchmarkEvaluator(
        artifact_path=artifact_path,
        workspace_root=tmp_path / "workspace",
    ).run())

    summary = artifact["summary"]
    assert isinstance(summary, dict)
    assert summary["total_scenarios"] == 3
    assert summary["passed"] == 3
    assert summary["pass_rate"] == 1.0
    assert float(summary["parallel_speedup"]) >= 1.5
    assert summary["configured_parallel_limit"] == 2
    assert summary["observed_parallel_peak"] == 2
    assert summary["write_overlap_count"] == 0
    assert summary["timeout_residue_count"] == 0
    assert json.loads(artifact_path.read_text(encoding="utf-8")) == artifact

    rows_value = artifact["rows"]
    assert isinstance(rows_value, list)
    rows = {row["id"]: row for row in rows_value if isinstance(row, dict)}
    assert rows["serialized_writes"]["effect_counts"] == {"workspace_write": 3}
    assert rows["timeout_cleanup"]["timed_out_calls"] == 1
    assert rows["timeout_cleanup"]["cleanup_completions"] == 1
