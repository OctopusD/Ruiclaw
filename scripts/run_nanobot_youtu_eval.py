#!/usr/bin/env python3
"""Run the local unmodified nanobot source as a Youtu-Agent baseline.

Run from the local ``youtu-agent`` checkout. The launcher imports the local
source checkout directly, so it does not install or modify nanobot::

    .venv/bin/python ../scripts/run_nanobot_youtu_eval.py --exp-id nanobot-ww15
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
NANOBOT_SOURCE = PROJECT_ROOT / "nanobot源码"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--exp-id", required=True, help="Unique Youtu experiment ID.")
    parser.add_argument("--config-name", default="ww", help="Youtu eval config name.")
    parser.add_argument("--dataset", default="WebWalkerQA_15", help="Prepared Youtu dataset name.")
    parser.add_argument("--concurrency", type=int, default=1, help="Baseline rollout concurrency.")
    parser.add_argument("--judge-concurrency", type=int, default=1, help="Youtu judge concurrency.")
    parser.add_argument("--step", choices=("all", "rollout", "judge"), default="all")
    parser.add_argument("--nanobot-config", type=Path, default=Path.home() / ".ruiclaw/config.json")
    parser.add_argument(
        "--workspace-root",
        type=Path,
        default=PROJECT_ROOT / "benchmarks/results/youtu/nanobot-workspaces",
        help="Directory for isolated nanobot workspaces.",
    )
    return parser.parse_args()


async def main() -> None:
    args = parse_args()
    if not NANOBOT_SOURCE.is_dir():
        raise FileNotFoundError(f"nanobot source checkout not found: {NANOBOT_SOURCE}")
    if str(NANOBOT_SOURCE) not in sys.path:
        sys.path.insert(0, str(NANOBOT_SOURCE))

    os.environ.setdefault("UTU_LLM_TYPE", "openai")
    os.environ.setdefault("UTU_LLM_MODEL", "unused-by-nanobot-adapter")
    from nanobot.nanobot import Nanobot
    from utu.config import ConfigLoader
    from utu.eval import BaseBenchmark

    from ruiclaw.evaluation.nanobot_youtu_adapter import create_nanobot_youtu_benchmark

    config = ConfigLoader.load_eval_config(args.config_name)
    config.exp_id = args.exp_id
    config.data.dataset = args.dataset
    config.concurrency = args.concurrency
    config.judge_concurrency = args.judge_concurrency
    benchmark_type = create_nanobot_youtu_benchmark(BaseBenchmark, bot_factory=Nanobot.from_config)
    benchmark = benchmark_type(
        config,
        nanobot_config_path=args.nanobot_config,
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
