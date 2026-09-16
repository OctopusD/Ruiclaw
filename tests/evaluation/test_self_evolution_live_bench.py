from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from ruiclaw.evaluation.self_evolution_live_bench import (
    LiveSelfEvolutionBenchmarkEvaluator,
    _select_tasks,
)


def test_task_selection_is_balanced_and_reproducible() -> None:
    from ruiclaw.evaluation.self_evolution_live_bench import _HOLDOUT_TASKS, _LEARNING_TASKS

    first = _select_tasks(_LEARNING_TASKS, 4, 7)
    second = _select_tasks(_LEARNING_TASKS, 4, 7)
    assert [task.id for task in first] == [task.id for task in second]
    assert {task.category for task in first} == {"memory", "skills"}
    holdout = _select_tasks(_HOLDOUT_TASKS, 8, 7)
    assert len(holdout) == 8
    assert {task.category for task in holdout} == {"memory", "skills", "safety", "regression"}


class _FakeRuntime:
    def __init__(self, workspace: Path) -> None:
        self.workspace = workspace

    async def run_evolution_review(self, scope=None, *, force: bool = False):
        assert scope is None
        assert force is True
        review_id = "evo_fake"
        review_dir = self.workspace / ".ruiclaw" / "evolution" / "reviews" / review_id
        review_dir.mkdir(parents=True)
        (self.workspace / "memory" / "MEMORY.md").write_text(
            "LIME-ANCHOR-27\nRELEASE-NOTE-V2\nRCL_LINT -> RCL_TARGET -> RCL_REGRESSION\n",
            encoding="utf-8",
        )
        skill = self.workspace / "skills" / "rcl-verification" / "SKILL.md"
        skill.parent.mkdir(parents=True)
        skill.write_text("RCL_LINT -> RCL_TARGET -> RCL_REGRESSION\n", encoding="utf-8")
        (review_dir / "changes.json").write_text(json.dumps({
            "changed_files": ["memory/MEMORY.md", "skills/rcl-verification/SKILL.md"],
        }))
        return SimpleNamespace(
            review_id=review_id,
            scope="combined",
            run_ids=("learning-1", "learning-2"),
        )


class _FakeBot:
    def __init__(self, group_root: Path, evolution_enabled: bool) -> None:
        self.workspace = group_root / "workspace"
        (self.workspace / "memory").mkdir(parents=True)
        (self.workspace / "skills").mkdir()
        (group_root / "runtime" / "sessions").mkdir(parents=True)
        (self.workspace / "SOUL.md").write_text("benchmark", encoding="utf-8")
        (self.workspace / "USER.md").write_text("benchmark", encoding="utf-8")
        (self.workspace / "memory" / "MEMORY.md").write_text("", encoding="utf-8")
        self.evolution_enabled = evolution_enabled
        self.runtime = _FakeRuntime(self.workspace)
        self.calls = 0

    async def run(self, message: str, *, session_key: str, channel: str):
        assert channel == "benchmark"
        self.calls += 1
        if message.startswith("Remember this"):
            (self.workspace / "PROJECT_CONVENTIONS.md").write_text(
                "LIME-ANCHOR-27\n",
                encoding="utf-8",
            )
            content = "acknowledged"
        elif message.startswith("Learn this reusable"):
            note = self.workspace / "notes" / "evolution-sample.rcl"
            note.parent.mkdir()
            note.write_text(
                "RCL_LINT -> RCL_TARGET -> RCL_REGRESSION\n",
                encoding="utf-8",
            )
            content = "verified"
        elif "release-status codeword" in message:
            memory = (self.workspace / "memory" / "MEMORY.md").read_text()
            content = "LIME-ANCHOR-27" if "LIME-ANCHOR-27" in memory else "UNKNOWN"
        elif "heading format" in message:
            memory = (self.workspace / "memory" / "MEMORY.md").read_text()
            content = "RELEASE-NOTE-V2" if "RELEASE-NOTE-V2" in memory else "UNKNOWN"
        elif "exact verification sequence" in message:
            memory = (self.workspace / "memory" / "MEMORY.md").read_text()
            skill_files = list((self.workspace / "skills").glob("*/SKILL.md"))
            learned = "RCL_LINT" in memory or any(
                "RCL_LINT" in path.read_text() for path in skill_files
            )
            content = "RCL_LINT → RCL_TARGET → RCL_REGRESSION" if learned else "UNKNOWN"
        elif "first action" in message:
            memory = (self.workspace / "memory" / "MEMORY.md").read_text()
            content = "capture the error" if "RCL_LINT" in memory else "UNKNOWN"
        elif "SAFE-CONTROL" in message:
            content = "SAFE-CONTROL"
        elif "NO-SECRET-LEAK" in message:
            content = "NO-SECRET-LEAK"
        elif "19 + 23" in message:
            content = "42"
        elif "PONG" in message:
            content = "PONG"
        else:
            content = "acknowledged"
        self._write_ledger(session_key)
        return SimpleNamespace(content=content)

    def _write_ledger(self, session_key: str) -> None:
        run_dir = self.workspace / ".ruiclaw" / "runs" / f"run-{self.calls}"
        run_dir.mkdir(parents=True)
        (run_dir / "manifest.json").write_text(json.dumps({
            "run_id": f"run-{self.calls}",
            "session_key": session_key,
            "run_kind": "agent",
            "status": "succeeded",
            "started_at": "2026-01-01T00:00:00+00:00",
            "finished_at": "2026-01-01T00:00:01+00:00",
        }))
        (run_dir / "report.json").write_text(json.dumps({
            "duration_ms": 1000,
            "model_calls": 1,
            "tool_calls": 1,
            "failed_tool_calls": 0,
            "usage": {"input_tokens": 10, "output_tokens": 2},
            "cost": {"status": "known", "estimated_cost_usd": 0.001},
        }))

    async def aclose(self) -> None:
        return None


@pytest.mark.asyncio
async def test_live_self_evolution_runs_isolated_ab_without_real_model(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path / "home"))
    artifact_path = tmp_path / "result" / "comparison.json"
    report_path = tmp_path / "result" / "comparison.md"
    workspace_root = tmp_path / "workspaces"
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps({
        "agents": {"defaults": {"workspace": str(tmp_path / "daily-workspace")}},
    }))

    def factory(config_path, group_root, evolution_enabled, model_preset):
        assert config_path == tmp_path / "config.json"
        assert model_preset == "deepseek-flash"
        return _FakeBot(group_root, evolution_enabled)

    artifact = await LiveSelfEvolutionBenchmarkEvaluator(
        artifact_path,
        report_path,
        workspace_root,
        config_path=config_path,
        model_preset="deepseek-flash",
        bot_factory=factory,
    ).run()

    assert artifact["results"]["baseline"]["metrics"]["overall_pass_rate"] == 0.5
    assert artifact["results"]["evolved"]["metrics"]["overall_pass_rate"] == 1.0
    baseline_holdout = workspace_root / "baseline" / "holdout" / "workspace"
    assert not (baseline_holdout / "PROJECT_CONVENTIONS.md").exists()
    assert not (baseline_holdout / "notes" / "evolution-sample.rcl").exists()
    evolved_holdout = workspace_root / "evolved" / "holdout" / "workspace"
    assert (evolved_holdout / "memory" / "MEMORY.md").read_text().startswith(
        "LIME-ANCHOR-27\n"
    )
    assert (evolved_holdout / "skills" / "rcl-verification" / "SKILL.md").is_file()
    assert artifact["results"]["evolved"]["ledger"]["ledger_completeness"] == 1.0
    assert artifact["decision"] == {"status": "passed", "automatic_promotion": False}
    assert artifact["isolation"]["protected_memory_skills_unchanged"] is True
    assert artifact["isolation"]["holdouts_use_pristine_workspaces"] is True
    assert json.loads(artifact_path.read_text(encoding="utf-8")) == artifact
    assert "4-case smoke test" in report_path.read_text(encoding="utf-8")


def test_live_self_evolution_rejects_runtime_workspace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    with pytest.raises(ValueError, match=r"outside ~/.ruiclaw"):
        LiveSelfEvolutionBenchmarkEvaluator(
            tmp_path / "result.json",
            tmp_path / "report.md",
            tmp_path / ".ruiclaw" / "live-bench",
        )
