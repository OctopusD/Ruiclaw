"""GDPevo task-group adapter.

GDPevo keeps solver inputs, reference answers, and evaluators in one source
tree.  This module makes the boundary explicit for RuiClaw: only ``input/``
is staged for a solver, while answer and evaluator paths remain private to
the benchmark runner.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, cast

import yaml

TaskSplit = Literal["train", "test"]


@dataclass(frozen=True, slots=True)
class GDPevoTask:
    """One task with its public solver input and private scoring material."""

    id: str
    split: TaskSplit
    input_dir: Path
    prompt_path: Path
    answer_path: Path
    evaluator_path: Path


@dataclass(frozen=True, slots=True)
class GDPevoTaskGroup:
    """A validated GDPevo task group."""

    id: str
    root: Path
    environment_dir: Path
    state_mode: str
    train_tasks: tuple[GDPevoTask, ...]
    test_tasks: tuple[GDPevoTask, ...]


def load_task_group(dataset_root: Path, task_group_id: str) -> GDPevoTaskGroup:
    """Load one official GDPevo task group without exposing private files."""
    root = (dataset_root.expanduser().resolve() / "data" / "task_groups" / task_group_id)
    manifest_path = root / "task_group.yaml"
    if not manifest_path.is_file():
        raise ValueError(f"GDPevo task group not found: {manifest_path}")

    try:
        loaded: object = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ValueError(f"invalid GDPevo manifest: {manifest_path}") from exc
    if not isinstance(loaded, dict):
        raise ValueError(f"GDPevo manifest must be a mapping: {manifest_path}")
    value = cast(dict[str, object], loaded)

    group_value = value.get("task_group")
    if not isinstance(group_value, dict):
        raise ValueError(f"GDPevo manifest has no task_group mapping: {manifest_path}")
    group = cast(dict[str, object], group_value)
    declared_id = group.get("task_group_id")
    if declared_id != task_group_id:
        raise ValueError(f"GDPevo manifest id mismatch: expected {task_group_id!r}")
    environment_value = value.get("env")
    if not isinstance(environment_value, dict):
        raise ValueError(f"GDPevo manifest has no env mapping: {manifest_path}")
    environment = cast(dict[str, object], environment_value)
    environment_dir = root / "env"
    state_mode = environment.get("state_mode")
    if not environment_dir.is_dir() or not isinstance(state_mode, str):
        raise ValueError(f"GDPevo environment contract is incomplete: {manifest_path}")

    train_tasks = _load_tasks(root, value, "train")
    test_tasks = _load_tasks(root, value, "test")
    if not train_tasks or not test_tasks:
        raise ValueError(f"GDPevo task group must contain train and test tasks: {manifest_path}")
    return GDPevoTaskGroup(
        id=task_group_id,
        root=root,
        environment_dir=environment_dir,
        state_mode=state_mode,
        train_tasks=train_tasks,
        test_tasks=test_tasks,
    )


def stage_solver_input(task: GDPevoTask, destination: Path) -> Path:
    """Stage exactly one task's public input into an empty solver workspace."""
    destination = destination.expanduser().resolve()
    if destination.exists():
        raise ValueError(f"solver workspace already exists: {destination}")
    destination.mkdir(parents=True)
    shutil.copytree(task.input_dir, destination / "input")
    return destination


def write_execution_plan(group: GDPevoTaskGroup, output: Path) -> dict[str, object]:
    """Persist the train/test contract for a later isolated live evaluation."""
    output = output.expanduser().resolve()
    plan: dict[str, object] = {
        "schema_version": 1,
        "benchmark": "gdpevo",
        "task_group": group.id,
        "environment": {
            "path": str(group.environment_dir),
            "state_mode": group.state_mode,
        },
        "train": [_task_plan(task) for task in group.train_tasks],
        "test": [_task_plan(task) for task in group.test_tasks],
        "information_boundary": {
            "solver_receives": ["input/"],
            "solver_must_not_receive": ["notes/", "output/", "eval/", "env/"],
            "scorer_uses": ["private evaluator_path", "solver-produced answer.json"],
        },
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(plan, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return plan


def score_answer(task: GDPevoTask, answer_path: Path, *, timeout_seconds: int = 30) -> dict[str, object]:
    """Run the task's private official evaluator against a solver-produced answer.

    The evaluator is deliberately invoked only by the benchmark process.  Its
    path and any reference answer are never staged in the agent workspace.
    """
    answer_path = answer_path.expanduser().resolve()
    if not answer_path.is_file():
        return {"passed": False, "error": f"answer file not found: {answer_path}"}
    command = [str(task.evaluator_path), str(answer_path)]
    if task.evaluator_path.suffix == ".sh":
        command.insert(0, "bash")
    elif task.evaluator_path.suffix == ".py":
        command.insert(0, sys.executable)
    try:
        completed = subprocess.run(
            command,
            cwd=task.evaluator_path.parent,
            capture_output=True,
            check=False,
            text=True,
            timeout=timeout_seconds,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"passed": False, "error": str(exc)}
    if completed.returncode != 0:
        return {
            "passed": False,
            "error": completed.stderr.strip() or completed.stdout.strip() or "evaluator failed",
        }
    try:
        loaded: object = json.loads(completed.stdout)
    except json.JSONDecodeError:
        return {"passed": False, "error": "evaluator did not return JSON"}
    if not isinstance(loaded, dict):
        return {"passed": False, "error": "evaluator returned a non-object JSON value"}
    value = cast(dict[str, object], loaded)
    score = value.get("total_score")
    passed = isinstance(score, int | float) and not isinstance(score, bool) and score == 1.0
    return {"passed": passed, "result": value}


def _load_tasks(
    root: Path,
    manifest: dict[str, object],
    split: TaskSplit,
) -> tuple[GDPevoTask, ...]:
    raw_tasks = manifest.get(f"{split}_tasks")
    if not isinstance(raw_tasks, list):
        raise ValueError(f"GDPevo manifest has no {split}_tasks list")
    tasks: list[GDPevoTask] = []
    for item_value in cast(list[object], raw_tasks):
        if not isinstance(item_value, dict):
            raise ValueError(f"GDPevo {split} task entry must be a mapping")
        item = cast(dict[str, object], item_value)
        task_id = item.get("task_id")
        input_relative = item.get("input")
        prompt_relative = item.get("prompt_txt")
        answer_relative = item.get("answer_json")
        evaluation = item.get("eval")
        evaluator_relative = (
            cast(dict[str, object], evaluation).get("script")
            if isinstance(evaluation, dict)
            else None
        )
        values = (task_id, input_relative, prompt_relative, answer_relative, evaluator_relative)
        if not all(isinstance(value, str) and value for value in values):
            raise ValueError(f"GDPevo {split} task entry is incomplete: {item!r}")
        input_dir = _contained_path(root, cast(str, input_relative))
        prompt_path = _contained_path(root, cast(str, prompt_relative))
        answer_path = _contained_path(root, cast(str, answer_relative))
        evaluator_path = _contained_path(root, cast(str, evaluator_relative))
        if not input_dir.is_dir() or not prompt_path.is_file() or not evaluator_path.is_file():
            raise ValueError(f"GDPevo {split} task files are incomplete: {task_id}")
        tasks.append(GDPevoTask(
            id=cast(str, task_id),
            split=split,
            input_dir=input_dir,
            prompt_path=prompt_path,
            answer_path=answer_path,
            evaluator_path=evaluator_path,
        ))
    return tuple(tasks)


def _contained_path(root: Path, relative: str) -> Path:
    path = (root / relative).resolve()
    if root not in path.parents:
        raise ValueError(f"GDPevo path escapes task group: {relative!r}")
    return path


def _task_plan(task: GDPevoTask) -> dict[str, str]:
    return {
        "id": task.id,
        "prompt_path": str(task.prompt_path),
        "input_dir": str(task.input_dir),
        "private_answer_path": str(task.answer_path),
        "private_evaluator_path": str(task.evaluator_path),
    }
