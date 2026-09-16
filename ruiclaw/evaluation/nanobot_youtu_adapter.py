"""Run the unmodified local nanobot source through a Youtu-Agent benchmark.

The launcher supplies ``Nanobot`` at runtime after placing the local source
checkout on ``sys.path``.  Keeping this module free of a nanobot import means
RuiClaw users do not need nanobot installed for normal use.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any


def create_nanobot_youtu_benchmark(
    base_benchmark_type: type,
    *,
    bot_factory: Callable[..., Any],
) -> type:
    """Return a Youtu benchmark backed by an unmodified nanobot SDK instance."""

    class NanobotBenchmark(base_benchmark_type):
        def __init__(
            self,
            config: Any,
            *,
            nanobot_config_path: str | Path | None = None,
            workspace_root: str | Path = "benchmarks/results/youtu/nanobot-workspaces",
        ) -> None:
            super().__init__(config)
            self.nanobot_config_path = nanobot_config_path
            self.workspace_root = Path(workspace_root).expanduser().resolve()

        async def rollout_one(self, sample: Any) -> Any:
            sample_id = _sample_id(sample)
            workspace = self.workspace_root / str(self.config.exp_id) / sample_id
            workspace.mkdir(parents=True, exist_ok=True)
            session_key = f"youtu:{self.config.exp_id}:{sample_id}"
            bot = bot_factory(config_path=self.nanobot_config_path, workspace=workspace)
            started_at = time.perf_counter()
            try:
                result = await bot.run(
                    sample.augmented_question or sample.raw_question,
                    session_key=session_key,
                    channel="benchmark",
                    chat_id=sample_id,
                    sender_id="youtu",
                    attributes={
                        "benchmark": "youtu",
                        "experiment_id": self.config.exp_id,
                        "sample_id": sample_id,
                    },
                )
            finally:
                await bot.aclose()

            sample.update(
                trace_id=session_key,
                response=result.content,
                time_cost=time.perf_counter() - started_at,
                trajectories=json.dumps(
                    {
                        "adapter": "nanobot-youtu-baseline",
                        "session_key": session_key,
                        "workspace": str(workspace),
                        "tools_used": result.tools_used,
                        "usage": result.usage,
                        "stop_reason": result.stop_reason,
                        "error": result.error,
                        "messages": result.messages,
                        "result_metadata": result.metadata,
                    },
                    ensure_ascii=False,
                    default=_json_default,
                ),
                stage="rollout",
            )
            self.dataset.save(sample)
            return sample

    NanobotBenchmark.__name__ = "NanobotBenchmark"
    return NanobotBenchmark


def _sample_id(sample: Any) -> str:
    for field in ("dataset_index", "id"):
        value = getattr(sample, field, None)
        if value is not None:
            return str(value)
    raise ValueError("Youtu evaluation sample must have dataset_index or id")


def _json_default(value: Any) -> Any:
    if is_dataclass(value):
        return asdict(value)
    if isinstance(value, Path):
        return str(value)
    return str(value)
