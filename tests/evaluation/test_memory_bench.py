from __future__ import annotations

import json
from pathlib import Path

from ruiclaw.evaluation.memory_bench import MemoryBenchmarkEvaluator


def test_memory_benchmark_persists_measured_invariants(tmp_path: Path) -> None:
    artifact_path = tmp_path / "result.json"
    artifact = MemoryBenchmarkEvaluator(
        artifact_path=artifact_path,
        workspace_root=tmp_path / "workspace",
    ).run()

    summary = artifact["summary"]
    assert isinstance(summary, dict)
    assert summary["total_scenarios"] == 7
    assert summary["passed"] == 7
    assert summary["pass_rate"] == 1.0
    assert summary["memory_hit_rate"] == 1.0
    assert summary["top1_precision"] == 1.0
    assert summary["stale_suppression_rate"] == 1.0
    assert summary["memory_on_repeated_reads"] == 0
    assert summary["memory_off_repeated_reads"] == 6
    assert isinstance(summary["avoided_read_chars"], int)
    assert summary["avoided_read_chars"] > 0
    assert json.loads(artifact_path.read_text(encoding="utf-8")) == artifact
