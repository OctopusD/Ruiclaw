"""Run a small Youtu-Agent benchmark using RuiClaw as the rollout agent.

Run this from the local ``youtu-agent`` checkout after its dependencies and
WebWalkerQA data have been prepared::

    uv run python ../scripts/run_youtu_eval.py --exp-id ruiclaw-ww15
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--exp-id", required=True, help="Unique Youtu experiment ID.")
    parser.add_argument("--config-name", default="ww", help="Youtu eval config name.")
    parser.add_argument("--dataset", default="WebWalkerQA_15", help="Prepared Youtu dataset name.")
    parser.add_argument("--concurrency", type=int, default=1, help="RuiClaw rollout concurrency.")
    parser.add_argument("--judge-concurrency", type=int, default=1, help="Youtu judge concurrency.")
    parser.add_argument("--step", choices=("all", "rollout", "judge"), default="all")
    parser.add_argument("--ruiclaw-config", type=Path, default=None, help="RuiClaw config.json path.")
    parser.add_argument(
        "--workspace-root",
        type=Path,
        default=PROJECT_ROOT / "benchmarks/results/youtu/workspaces",
        help="Directory for isolated RuiClaw workspaces.",
    )
    return parser.parse_args()


async def main() -> None:
    args = parse_args()
    # Youtu validates these variables at package import even though this adapter
    # never builds Youtu's rollout agent. The judge keeps using JUDGE_LLM_*.
    os.environ.setdefault("UTU_LLM_TYPE", "openai")
    os.environ.setdefault("UTU_LLM_MODEL", "unused-by-ruiclaw-adapter")
    from utu.config import ConfigLoader
    from utu.eval import BaseBenchmark

    from ruiclaw.evaluation.youtu_adapter import create_youtu_benchmark

    config = ConfigLoader.load_eval_config(args.config_name)
    config.exp_id = args.exp_id
    config.data.dataset = args.dataset
    config.concurrency = args.concurrency
    config.judge_concurrency = args.judge_concurrency
    benchmark_type = create_youtu_benchmark(BaseBenchmark)
    benchmark = benchmark_type(
        config,
        ruiclaw_config_path=args.ruiclaw_config,
        workspace_root=args.workspace_root,
    )
    if args.step == "all":
        await benchmark.main()
    elif args.step == "rollout":
        benchmark.preprocess()
        await benchmark.rollout()
    else:
        await benchmark.judge(stage="rollout")
        await benchmark.stat()


if __name__ == "__main__":
    asyncio.run(main())
