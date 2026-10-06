"""Live RuiClaw A/B execution for a read-only GDPevo task group."""

from __future__ import annotations

import asyncio
import shutil
from pathlib import Path
from typing import Any, cast

from ruiclaw.evaluation.gdpevo import GDPevoTask, GDPevoTaskGroup, score_answer, stage_solver_input
from ruiclaw.evaluation.self_evolution_live_bench import (
    _build_live_bot,  # pyright: ignore[reportPrivateUsage]
    _changed_files,  # pyright: ignore[reportPrivateUsage]
    _promote_review_changes,  # pyright: ignore[reportPrivateUsage]
)


def _number(value: object) -> float:
    if isinstance(value, int | float) and not isinstance(value, bool):
        return float(value)
    return 0.0


async def run_gdpevo_ab(
    group: GDPevoTaskGroup,
    *,
    config_path: Path,
    workspace_root: Path,
    environment_url: str,
    model_preset: str | None = None,
) -> dict[str, object]:
    """Compare evolution off/on, using train only for the evolved arm.

    The caller must start the group environment separately.  This avoids the
    evaluator having to grant the solver access to the GDPevo source tree.
    """
    if group.state_mode != "read_only":
        raise ValueError("the RuiClaw adapter currently supports read_only GDPevo groups only")
    root = workspace_root.expanduser().resolve()
    if root.exists():
        shutil.rmtree(root)
    root.mkdir(parents=True)

    baseline = await _run_arm(
        "baseline", group, config_path, root / "baseline", environment_url, model_preset, False
    )
    evolved = await _run_arm(
        "evolved", group, config_path, root / "evolved", environment_url, model_preset, True
    )
    artifact: dict[str, object] = {
        "schema_version": 1,
        "benchmark": "gdpevo-ruiclaw-ab",
        "task_group": group.id,
        "environment_url": environment_url,
        "information_boundary": "train answers/evaluators are never supplied to the agent; test scoring is private",
        "results": {"baseline": baseline, "evolved": evolved},
    }
    artifact["comparison"] = {
        "test_score_lift": round(
            _number(cast(dict[str, object], evolved["metrics"])["mean_total_score"])
            - _number(cast(dict[str, object], baseline["metrics"])["mean_total_score"]),
            6,
        )
    }
    return artifact


async def _run_arm(
    name: str,
    group: GDPevoTaskGroup,
    config_path: Path,
    root: Path,
    environment_url: str,
    model_preset: str | None,
    evolution_enabled: bool,
) -> dict[str, object]:
    learner = _build_live_bot(config_path, root / "learning", evolution_enabled, model_preset)
    learner_workspace = root / "learning" / "workspace"
    train_rows: list[dict[str, object]] = []
    review: dict[str, object] = {"requested": evolution_enabled, "changed_files": []}
    try:
        if evolution_enabled:
            train_rows = await _run_tasks(learner, learner_workspace, group.train_tasks, environment_url, name)
            batch = await learner.runtime.run_evolution_review(force=True)
            review_id = getattr(batch, "review_id", None)
            changed = _changed_files(learner_workspace, review_id if isinstance(review_id, str) else None)
            review = {"requested": True, "completed": batch is not None, "review_id": review_id, "changed_files": changed}
    finally:
        await learner.aclose()

    tester = _build_live_bot(config_path, root / "holdout", False, model_preset)
    tester_workspace = root / "holdout" / "workspace"
    promoted = _promote_review_changes(learner_workspace, tester_workspace, cast(list[str], review["changed_files"]))
    try:
        test_rows = await _run_tasks(tester, tester_workspace, group.test_tasks, environment_url, name)
    finally:
        await tester.aclose()
    scores = [
        _number(cast(dict[str, object], row.get("score", {})).get("total_score", 0))
        for row in test_rows
    ]
    return {
        "train": train_rows,
        "review": review,
        "promoted_changes": promoted,
        "test": test_rows,
        "metrics": {
            "mean_total_score": sum(scores) / len(scores) if scores else 0.0,
            "test_pass_rate": sum(row["passed"] is True for row in test_rows) / len(test_rows),
        },
    }


async def _run_tasks(
    bot: Any,
    workspace: Path,
    tasks: tuple[GDPevoTask, ...],
    environment_url: str,
    arm: str,
) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for task in tasks:
        input_destination = workspace / "input"
        if input_destination.exists():
            shutil.rmtree(input_destination)
        stage_solver_input(task, workspace / ".gdpevo-stage")
        shutil.move(str(workspace / ".gdpevo-stage" / "input"), input_destination)
        shutil.rmtree(workspace / ".gdpevo-stage")
        answer_path = workspace / "answer.json"
        answer_path.unlink(missing_ok=True)
        prompt = (input_destination / "prompt.txt").read_text(encoding="utf-8")
        result = await bot.run(
            _solver_prompt(prompt, environment_url),
            session_key=f"gdpevo:{arm}:{task.split}:{task.id}",
            channel="benchmark",
        )
        scored = score_answer(task, answer_path) if task.split == "test" else {}
        details_value = scored.get("result")
        details = (
            cast(dict[str, object], details_value)
            if isinstance(details_value, dict)
            else {}
        )
        rows.append({
            "id": task.id,
            "content": str(getattr(result, "content", "")),
            "passed": scored.get("passed") if scored else None,
            "score": details,
            "error": scored.get("error") if scored else None,
        })
    return rows


def _solver_prompt(task_prompt: str, environment_url: str) -> str:
    return (
        "You are solving a GDPevo business task. Public task materials are in ./input. "
        f"The task environment base URL is {environment_url}. Use available tools to solve it. "
        "Do not look for benchmark sources, reference answers, notes, or evaluators. "
        "Write the final JSON answer to ./answer.json (not merely in chat), following the public "
        "answer template in ./input.\n\nTask:\n"
        + task_prompt
    )


def run_gdpevo_ab_sync(
    *,
    group: GDPevoTaskGroup,
    config_path: Path,
    workspace_root: Path,
    environment_url: str,
    model_preset: str | None = None,
) -> dict[str, object]:
    """Synchronous CLI bridge for :func:`run_gdpevo_ab`."""
    return asyncio.run(run_gdpevo_ab(
        group,
        config_path=config_path,
        workspace_root=workspace_root,
        environment_url=environment_url,
        model_preset=model_preset,
    ))


def render_gdpevo_report(artifact: dict[str, object]) -> str:
    """Render the primary GDPevo A/B metrics as a compact Markdown report."""
    results = cast(dict[str, dict[str, object]], artifact["results"])
    baseline = cast(dict[str, object], results["baseline"]["metrics"])
    evolved = cast(dict[str, object], results["evolved"]["metrics"])
    comparison = cast(dict[str, object], artifact["comparison"])
    review = cast(dict[str, object], results["evolved"]["review"])
    promoted = cast(list[object], results["evolved"]["promoted_changes"])
    baseline_score = _number(baseline["mean_total_score"])
    evolved_score = _number(evolved["mean_total_score"])
    lift = _number(comparison["test_score_lift"])
    changes = ", ".join(str(path) for path in promoted) or "None"
    review_id = review.get("review_id") or "None"
    return "\n".join([
        "# RuiClaw GDPevo A/B Report",
        "",
        f"- Task group: `{artifact['task_group']}`",
        "- Protocol: baseline runs held-out test directly; evolved runs train, review, then a fresh held-out test.",
        "- `self / evolved acc` is the average official `total_score` across held-out test tasks.",
        "",
        "| Metric | Baseline | Self / evolved | Delta |",
        "| --- | ---: | ---: | ---: |",
        f"| Held-out test acc | {baseline_score:.2%} | {evolved_score:.2%} | {lift:+.2%} |",
        "",
        "## Evolution evidence",
        "",
        f"- Review ID: `{review_id}`",
        f"- Promoted Memory/Skill changes: {changes}",
        "",
    ])
