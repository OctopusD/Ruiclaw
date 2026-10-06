from __future__ import annotations

import json
from pathlib import Path
from stat import S_IXUSR

import pytest

from ruiclaw.evaluation.gdpevo import (
    load_task_group,
    score_answer,
    stage_solver_input,
    write_execution_plan,
)
from ruiclaw.evaluation.gdpevo_live import render_gdpevo_report


def _write_task_group(root: Path) -> Path:
    group = root / "data" / "task_groups" / "task_group_001"
    for split in ("train", "test"):
        task = group / f"{split}_tasks" / "001"
        (task / "input" / "payloads").mkdir(parents=True)
        (task / "input" / "prompt.txt").write_text("Solve this task.\n", encoding="utf-8")
        (task / "input" / "payloads" / "answer_template.json").write_text("{}\n", encoding="utf-8")
        (task / "output").mkdir()
        (task / "output" / "answer.json").write_text('{"private": true}\n', encoding="utf-8")
        (task / "eval").mkdir()
        evaluator = task / "eval" / "eval.sh"
        evaluator.write_text('#!/bin/sh\nprintf \'{"total_score": 1.0}\\n\'\n', encoding="utf-8")
        evaluator.chmod(evaluator.stat().st_mode | S_IXUSR)
    (group / "env").mkdir()
    (group / "task_group.yaml").write_text(
        """task_group:\n  task_group_id: task_group_001\nenv:\n  state_mode: read_only\ntrain_tasks:\n  - task_id: train_001\n    input: train_tasks/001/input/\n    prompt_txt: train_tasks/001/input/prompt.txt\n    answer_json: train_tasks/001/output/answer.json\n    eval:\n      script: train_tasks/001/eval/eval.sh\ntest_tasks:\n  - task_id: test_001\n    input: test_tasks/001/input/\n    prompt_txt: test_tasks/001/input/prompt.txt\n    answer_json: test_tasks/001/output/answer.json\n    eval:\n      script: test_tasks/001/eval/eval.sh\n""",
        encoding="utf-8",
    )
    return group


def test_gdpevo_adapter_keeps_private_material_out_of_solver_workspace(tmp_path: Path) -> None:
    _write_task_group(tmp_path)
    group = load_task_group(tmp_path, "task_group_001")

    solver_workspace = stage_solver_input(group.test_tasks[0], tmp_path / "solver")

    assert (solver_workspace / "input" / "prompt.txt").is_file()
    assert not (solver_workspace / "output").exists()
    assert not (solver_workspace / "eval").exists()
    assert group.test_tasks[0].answer_path.read_text(encoding="utf-8") == '{"private": true}\n'


def test_gdpevo_adapter_writes_auditable_train_test_plan(tmp_path: Path) -> None:
    _write_task_group(tmp_path)
    group = load_task_group(tmp_path, "task_group_001")
    output = tmp_path / "result" / "plan.json"

    plan = write_execution_plan(group, output)

    assert plan["task_group"] == "task_group_001"
    assert plan["information_boundary"] == {
        "solver_receives": ["input/"],
        "solver_must_not_receive": ["notes/", "output/", "eval/", "env/"],
        "scorer_uses": ["private evaluator_path", "solver-produced answer.json"],
    }
    saved = json.loads(output.read_text(encoding="utf-8"))
    assert saved["train"][0]["id"] == "train_001"
    assert saved["test"][0]["private_evaluator_path"].endswith("test_tasks/001/eval/eval.sh")


def test_gdpevo_adapter_rejects_existing_solver_workspace(tmp_path: Path) -> None:
    _write_task_group(tmp_path)
    group = load_task_group(tmp_path, "task_group_001")
    workspace = tmp_path / "solver"
    workspace.mkdir()

    with pytest.raises(ValueError, match="already exists"):
        stage_solver_input(group.train_tasks[0], workspace)


def test_gdpevo_adapter_scores_with_private_evaluator(tmp_path: Path) -> None:
    _write_task_group(tmp_path)
    group = load_task_group(tmp_path, "task_group_001")
    answer = tmp_path / "solver-answer.json"
    answer.write_text("{}\n", encoding="utf-8")

    result = score_answer(group.test_tasks[0], answer)

    assert result == {"passed": True, "result": {"total_score": 1.0}}


def test_gdpevo_report_includes_self_accuracy_and_lift() -> None:
    report = render_gdpevo_report({
        "task_group": "task_group_001",
        "results": {
            "baseline": {"metrics": {"mean_total_score": 0.48755}},
            "evolved": {
                "metrics": {"mean_total_score": 0.531144},
                "review": {"review_id": "evo_123"},
                "promoted_changes": ["memory/MEMORY.md"],
            },
        },
        "comparison": {"test_score_lift": 0.043594},
    })

    assert "48.75%" in report
    assert "53.11%" in report
    assert "+4.36%" in report
    assert "memory/MEMORY.md" in report
