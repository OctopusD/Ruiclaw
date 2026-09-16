"""Bridge RuiClaw runs into a Youtu-Agent benchmark.

The adapter deliberately has no import-time dependency on Youtu-Agent.  Pass
Youtu's ``BaseBenchmark`` to :func:`create_youtu_benchmark` from the launcher
that runs inside a Youtu-Agent environment.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any

from ruiclaw.bus.runtime_events import RUN_ID_METADATA_KEY
from ruiclaw.ruiclaw import RuiClaw


def create_youtu_benchmark(
    base_benchmark_type: type,
    *,
    bot_factory: Callable[..., RuiClaw] = RuiClaw.from_config,
) -> type:
    """Create a Youtu ``BaseBenchmark`` subclass backed by RuiClaw.

    ``base_benchmark_type`` is injected so importing this module does not make
    Youtu-Agent a mandatory RuiClaw dependency.
    """

    class RuiClawBenchmark(base_benchmark_type):
        def __init__(
            self,
            config: Any,
            *,
            ruiclaw_config_path: str | Path | None = None,
            workspace_root: str | Path = "benchmarks/results/youtu/workspaces",
        ) -> None:
            super().__init__(config)
            self.ruiclaw_config_path = ruiclaw_config_path
            self.workspace_root = Path(workspace_root).expanduser().resolve()

        async def rollout_one(self, sample: Any) -> Any:
            """Run one Youtu question through an isolated RuiClaw instance."""
            sample_id = _sample_id(sample)
            workspace = self.workspace_root / str(self.config.exp_id) / sample_id
            workspace.mkdir(parents=True, exist_ok=True)
            session_key = f"youtu:{self.config.exp_id}:{sample_id}"
            bot = bot_factory(config_path=self.ruiclaw_config_path, workspace=workspace)
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

            run_id = _run_id(result.metadata)
            sample.update(
                trace_id=run_id or session_key,
                response=result.content,
                time_cost=time.perf_counter() - started_at,
                trajectories=json.dumps(
                    _trajectory(
                        result=result,
                        run_id=run_id,
                        session_key=session_key,
                        workspace=workspace,
                    ),
                    ensure_ascii=False,
                    default=_json_default,
                ),
                stage="rollout",
            )
            self.dataset.save(sample)
            return sample

    RuiClawBenchmark.__name__ = "RuiClawBenchmark"
    return RuiClawBenchmark


def _sample_id(sample: Any) -> str:
    for field in ("dataset_index", "id"):
        value = getattr(sample, field, None)
        if value is not None:
            return str(value)
    raise ValueError("Youtu evaluation sample must have dataset_index or id")


def _run_id(metadata: dict[str, Any]) -> str | None:
    value = metadata.get(RUN_ID_METADATA_KEY)
    return value if isinstance(value, str) and value else None


def _trajectory(
    *,
    result: Any,
    run_id: str | None,
    session_key: str,
    workspace: Path,
) -> dict[str, Any]:
    ledger = _read_ledger(workspace, run_id)
    return {
        "adapter": "ruiclaw-youtu",
        "run_id": run_id,
        "session_key": session_key,
        "workspace": str(workspace),
        "tools_used": result.tools_used,
        "usage": result.usage,
        "stop_reason": result.stop_reason,
        "error": result.error,
        "messages": result.messages,
        "result_metadata": result.metadata,
        "run_ledger": ledger,
    }


def _read_ledger(workspace: Path, run_id: str | None) -> dict[str, Any] | None:
    if not run_id:
        return None
    ledger_path = workspace / ".ruiclaw" / "runs" / run_id
    manifest_path = ledger_path / "manifest.json"
    events_path = ledger_path / "events.jsonl"
    if not manifest_path.exists():
        return None
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    events = [
        json.loads(line)
        for line in events_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ] if events_path.exists() else []
    return {"manifest": manifest, "events": events}


def _json_default(value: Any) -> Any:
    if is_dataclass(value):
        return asdict(value)
    if isinstance(value, Path):
        return str(value)
    return str(value)
