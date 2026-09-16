#!/usr/bin/env python3
"""Run a small τ²/τ³-bench sample through RuiClaw.

Run from the local ``tau2-bench`` checkout so its optional dependencies and
``tau2`` package are available::

    cd tau2-bench
    uv run python ../scripts/run_tau2_eval.py --domain mock --num-tasks 5
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate RuiClaw with tau2/τ³-bench")
    parser.add_argument("--domain", default="mock", choices=["mock", "retail", "airline", "telecom"])
    parser.add_argument("--split", choices=["train", "test", "base"], default="test")
    parser.add_argument("--num-tasks", type=int, default=5)
    parser.add_argument(
        "--task-ids",
        help="Comma-separated task IDs; overrides --split and --num-tasks.",
    )
    parser.add_argument("--config", dest="config_path")
    parser.add_argument("--workspace", default="benchmarks/results/tau2/workspaces")
    parser.add_argument("--user-model", default="openai/gpt-4.1-mini")
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    from tau2.agent.base_agent import HalfDuplexAgent
    from tau2.data_model.message import AssistantMessage, MultiToolMessage, ToolCall, ToolMessage
    from tau2.data_model.simulation import TextRunConfig
    from tau2.registry import registry
    from tau2.runner import get_tasks, run_single_task

    from ruiclaw.evaluation.tau2_adapter import create_tau2_agent

    agent_type = create_tau2_agent(
        HalfDuplexAgent,
        AssistantMessage,
        ToolCall,
        ToolMessage,
        MultiToolMessage,
    )

    def factory(tools: list[Any], domain_policy: str, **kwargs: Any) -> Any:
        task = kwargs.get("task")
        task_id = getattr(task, "id", "task")
        workspace = Path(args.workspace).resolve() / args.domain / str(task_id)
        return agent_type(
            tools,
            domain_policy,
            ruiclaw_config_path=args.config_path,
            workspace=str(workspace),
            task=task,
        )

    agent_name = "ruiclaw_tau2"
    registry.register_agent_factory(factory, agent_name)
    task_ids = args.task_ids.split(",") if args.task_ids else None
    tasks = get_tasks(
        args.domain,
        task_split_name=None if task_ids is not None else args.split,
        task_ids=task_ids,
        num_tasks=None if task_ids is not None else args.num_tasks,
    )
    if not tasks:
        raise ValueError("No tasks matched the requested domain, split, and task IDs")
    config = TextRunConfig(
        domain=args.domain,
        agent=agent_name,
        llm_agent="ruiclaw-managed",
        llm_user=args.user_model,
        max_concurrency=1,
    )
    rows: list[dict[str, Any]] = []
    for offset, task in enumerate(tasks):
        result = run_single_task(config, task, seed=args.seed + offset)
        reward = result.reward_info.reward if result.reward_info is not None else None
        rows.append({"task_id": result.task_id, "reward": reward})
        print(f"{result.task_id}: reward={reward}")

    rewards = [row["reward"] for row in rows if isinstance(row["reward"], (int, float))]
    summary = {
        "benchmark": "tau2",
        "domain": args.domain,
        "split": args.split if task_ids is None else None,
        "user_model": args.user_model,
        "seed": args.seed,
        "num_tasks": len(rows),
        "mean_reward": sum(rewards) / len(rewards) if rewards else None,
        "tasks": rows,
    }
    selection = args.split if task_ids is None else "selected"
    output = PROJECT_ROOT / "benchmarks" / "results" / "tau2" / (
        f"{args.domain}-{selection}-seed{args.seed}-n{len(rows)}.json"
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Saved: {output}")


if __name__ == "__main__":
    main()
