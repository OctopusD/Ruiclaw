from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from ruiclaw.evaluation.nanobot_youtu_adapter import create_nanobot_youtu_benchmark
from ruiclaw.sdk.types import RunResult


@dataclass
class _Config:
    exp_id: str = "nanobot-smoke"


@dataclass
class _Sample:
    dataset_index: int = 3
    raw_question: str = "What is two plus two?"
    augmented_question: str = "Answer the question exactly."
    fields: dict[str, object] = field(default_factory=dict)

    def update(self, **kwargs: object) -> None:
        self.fields.update(kwargs)


class _Dataset:
    def __init__(self) -> None:
        self.saved: list[_Sample] = []

    def save(self, sample: _Sample) -> None:
        self.saved.append(sample)


class _BaseBenchmark:
    def __init__(self, config: _Config) -> None:
        self.config = config
        self.dataset = _Dataset()


class _Bot:
    def __init__(self) -> None:
        self.run_kwargs: dict[str, object] | None = None
        self.closed = False

    async def run(self, message: str, **kwargs: object) -> RunResult:
        self.run_kwargs = {"message": message, **kwargs}
        return RunResult(content="4", tools_used=["calculator"])

    async def aclose(self) -> None:
        self.closed = True


@pytest.mark.asyncio
async def test_nanobot_youtu_adapter_runs_an_isolated_baseline_turn(tmp_path: Path) -> None:
    bot = _Bot()
    benchmark_type = create_nanobot_youtu_benchmark(_BaseBenchmark, bot_factory=lambda **_: bot)
    benchmark = benchmark_type(_Config(), workspace_root=tmp_path)
    sample = _Sample()

    result = await benchmark.rollout_one(sample)

    assert result is sample
    assert bot.closed is True
    assert bot.run_kwargs == {
        "message": "Answer the question exactly.",
        "session_key": "youtu:nanobot-smoke:3",
        "channel": "benchmark",
        "chat_id": "3",
        "sender_id": "youtu",
        "attributes": {
            "benchmark": "youtu",
            "experiment_id": "nanobot-smoke",
            "sample_id": "3",
        },
    }
    assert sample.fields["response"] == "4"
    assert sample.fields["trace_id"] == "youtu:nanobot-smoke:3"
    trajectory = json.loads(str(sample.fields["trajectories"]))
    assert trajectory["adapter"] == "nanobot-youtu-baseline"
    assert "run_ledger" not in trajectory
    assert trajectory["tools_used"] == ["calculator"]
    assert benchmark.dataset.saved == [sample]
