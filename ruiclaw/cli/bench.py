"""CLI for deterministic RuiClaw benchmark runs."""

from __future__ import annotations

from pathlib import Path
from typing import cast

import typer
from rich.console import Console
from rich.table import Table

from ruiclaw.evaluation.bench import (
    DEFAULT_ARTIFACT_PATH,
    DEFAULT_BENCHMARK_PATH,
    DEFAULT_WORKSPACE_ROOT,
    run_benchmark,
)
from ruiclaw.evaluation.evolver import (
    DEFAULT_EVOLVER_ARTIFACT_PATH,
    DEFAULT_EVOLVER_REPORT_PATH,
    DEFAULT_EVOLVER_WORKSPACE_ROOT,
    run_evolver_evaluation,
)
from ruiclaw.evaluation.memory_bench import (
    DEFAULT_MEMORY_ARTIFACT_PATH,
    DEFAULT_MEMORY_WORKSPACE_ROOT,
    run_memory_benchmark,
)
from ruiclaw.evaluation.recovery_bench import (
    DEFAULT_RECOVERY_ARTIFACT_PATH,
    DEFAULT_RECOVERY_WORKSPACE_ROOT,
    run_recovery_benchmark,
)
from ruiclaw.evaluation.tool_governance_bench import (
    DEFAULT_TOOL_GOVERNANCE_ARTIFACT_PATH,
    DEFAULT_TOOL_GOVERNANCE_WORKSPACE_ROOT,
    run_tool_governance_benchmark,
)

bench_app = typer.Typer(help="Run deterministic RuiClaw harness benchmarks")
console = Console()


@bench_app.command("evolve")
def evolve(
    output: Path = typer.Option(DEFAULT_EVOLVER_ARTIFACT_PATH, "--output", "-o"),
    report: Path = typer.Option(DEFAULT_EVOLVER_REPORT_PATH, "--report"),
    workspace_root: Path = typer.Option(
        DEFAULT_EVOLVER_WORKSPACE_ROOT,
        "--workspace-root",
    ),
) -> None:
    """Compare an isolated policy candidate and apply offline release gates."""
    try:
        artifact = run_evolver_evaluation(output, report, workspace_root)
    except (OSError, RuntimeError, ValueError) as exc:
        console.print(f"[red]RuiClaw Evolver failed:[/red] {exc}")
        raise typer.Exit(1) from exc

    results = artifact["results"]
    baseline = results["baseline"]["metrics"]
    candidate = results["candidate"]["metrics"]
    decision = artifact["decision"]
    comparison = artifact["comparison"]
    table = Table(title="RuiClaw Evolver Lite")
    table.add_column("Policy")
    table.add_column("Train", justify="right")
    table.add_column("Holdout", justify="right")
    table.add_column("Memory chars", justify="right")
    table.add_row(
        "baseline (Top-K 3)",
        f"{baseline['train_pass_rate']:.0%}",
        f"{baseline['holdout_pass_rate']:.0%}",
        str(baseline["working_memory_chars"]),
    )
    table.add_row(
        "candidate (Top-K 1)",
        f"{candidate['train_pass_rate']:.0%}",
        f"{candidate['holdout_pass_rate']:.0%}",
        str(candidate["working_memory_chars"]),
    )
    console.print(table)
    console.print(
        f"Context reduction: {comparison['working_memory_char_reduction']:.1%}"
    )
    console.print(f"Decision: {decision['status']} (automatic promotion disabled)")
    console.print(f"Evidence: {output.resolve()}")
    console.print(f"Report: {report.resolve()}")


@bench_app.command("memory")
def memory(
    output: Path = typer.Option(DEFAULT_MEMORY_ARTIFACT_PATH, "--output", "-o"),
    workspace_root: Path = typer.Option(DEFAULT_MEMORY_WORKSPACE_ROOT, "--workspace-root"),
) -> None:
    """Verify relevant recall, stale suppression, and repeated-read avoidance."""
    try:
        artifact = run_memory_benchmark(output, workspace_root)
    except (OSError, RuntimeError, ValueError) as exc:
        console.print(f"[red]RuiClaw Memory Bench failed:[/red] {exc}")
        raise typer.Exit(1) from exc

    summary = cast(dict[str, object], artifact["summary"])
    table = Table(title="RuiClaw Memory Bench")
    table.add_column("Scenarios", justify="right")
    table.add_column("Passed", justify="right")
    table.add_column("Hit rate", justify="right")
    table.add_column("Stale suppression", justify="right")
    table.add_column("Reads on/off", justify="right")
    table.add_row(
        str(summary["total_scenarios"]),
        str(summary["passed"]),
        f"{float(cast(float, summary['memory_hit_rate'])):.1%}",
        f"{float(cast(float, summary['stale_suppression_rate'])):.1%}",
        f"{summary['memory_on_repeated_reads']}/{summary['memory_off_repeated_reads']}",
    )
    console.print(table)
    console.print(f"Evidence: {output.resolve()}")


@bench_app.command("run")
def run(
    benchmark: Path = typer.Option(DEFAULT_BENCHMARK_PATH, "--benchmark", "-b"),
    output: Path = typer.Option(DEFAULT_ARTIFACT_PATH, "--output", "-o"),
    workspace_root: Path = typer.Option(DEFAULT_WORKSPACE_ROOT, "--workspace-root"),
) -> None:
    """Run fixed tasks in isolated workspaces and persist the result artifact."""
    try:
        artifact = run_benchmark(benchmark, output, workspace_root)
    except (OSError, RuntimeError, ValueError) as exc:
        console.print(f"[red]RuiClaw Bench failed:[/red] {exc}")
        raise typer.Exit(1) from exc

    summary = cast(dict[str, object], artifact["summary"])
    table = Table(title="RuiClaw Bench")
    table.add_column("Tasks", justify="right")
    table.add_column("Passed", justify="right")
    table.add_column("Pass rate", justify="right")
    table.add_column("Within budget", justify="right")
    table.add_column("Verifier", justify="right")
    table.add_column("Tokens", justify="right")
    table.add_column("Est. cost", justify="right")
    cost_value = summary.get("estimated_total_cost_usd")
    partial_cost_value = summary.get("partial_estimated_total_cost_usd")
    cost = (
        f"${float(cast(float, cost_value)):.6f}"
        if isinstance(cost_value, int | float) and not isinstance(cost_value, bool)
        else f"partial ${float(cast(float, partial_cost_value)):.6f}"
        if isinstance(partial_cost_value, int | float)
        and not isinstance(partial_cost_value, bool)
        else "cost_unknown"
    )
    table.add_row(
        str(summary["total_tasks"]),
        str(summary["passed"]),
        f"{float(cast(float, summary['pass_rate'])):.1%}",
        f"{float(cast(float, summary['within_budget_rate'])):.1%}",
        f"{float(cast(float, summary['verifier_pass_rate'])):.1%}",
        str(int(cast(int, summary["input_tokens"])) + int(cast(int, summary["output_tokens"]))),
        cost,
    )
    console.print(table)
    console.print(f"Evidence: {output.resolve()}")


@bench_app.command("recovery")
def recovery(
    output: Path = typer.Option(DEFAULT_RECOVERY_ARTIFACT_PATH, "--output", "-o"),
    workspace_root: Path = typer.Option(
        DEFAULT_RECOVERY_WORKSPACE_ROOT,
        "--workspace-root",
    ),
) -> None:
    """Inject restart faults and verify checkpoint recovery invariants."""
    try:
        artifact = run_recovery_benchmark(output, workspace_root)
    except (OSError, RuntimeError, ValueError) as exc:
        console.print(f"[red]RuiClaw Recovery Bench failed:[/red] {exc}")
        raise typer.Exit(1) from exc

    summary = cast(dict[str, object], artifact["summary"])
    table = Table(title="RuiClaw Recovery Bench")
    table.add_column("Scenarios", justify="right")
    table.add_column("Passed", justify="right")
    table.add_column("Recovery", justify="right")
    table.add_column("Duplicate effects", justify="right")
    table.add_column("Parent links", justify="right")
    table.add_row(
        str(summary["total_scenarios"]),
        str(summary["passed"]),
        f"{float(cast(float, summary['recovery_success_rate'])):.1%}",
        str(summary["duplicate_side_effects"]),
        f"{float(cast(float, summary['parent_run_link_rate'])):.1%}",
    )
    console.print(table)
    console.print(f"Evidence: {output.resolve()}")


@bench_app.command("tools")
def tools(
    output: Path = typer.Option(DEFAULT_TOOL_GOVERNANCE_ARTIFACT_PATH, "--output", "-o"),
    workspace_root: Path = typer.Option(
        DEFAULT_TOOL_GOVERNANCE_WORKSPACE_ROOT,
        "--workspace-root",
    ),
) -> None:
    """Verify bounded reads, serialized writes, and timeout cleanup."""
    try:
        artifact = run_tool_governance_benchmark(output, workspace_root)
    except (OSError, RuntimeError, ValueError) as exc:
        console.print(f"[red]RuiClaw Tool Governance Bench failed:[/red] {exc}")
        raise typer.Exit(1) from exc

    summary = cast(dict[str, object], artifact["summary"])
    table = Table(title="RuiClaw Tool Governance Bench")
    table.add_column("Scenarios", justify="right")
    table.add_column("Passed", justify="right")
    table.add_column("Read speedup", justify="right")
    table.add_column("Write overlaps", justify="right")
    table.add_column("Timeout residue", justify="right")
    table.add_row(
        str(summary["total_scenarios"]),
        str(summary["passed"]),
        f"{float(cast(float, summary['parallel_speedup'])):.2f}x",
        str(summary["write_overlap_count"]),
        str(summary["timeout_residue_count"]),
    )
    console.print(table)
    console.print(f"Evidence: {output.resolve()}")
