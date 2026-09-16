"""CLI inspection commands for RuiClaw run records."""

from __future__ import annotations

from typing import Any, cast

import typer
from rich.console import Console
from rich.markup import escape
from rich.table import Table

from ruiclaw.cli.runtime_config import _load_inspection_config
from ruiclaw.observability.run_inspector import RunInspector

runs_app = typer.Typer(help="Inspect RuiClaw agent runs")
console = Console()


@runs_app.command("list")
def list_runs(
    limit: int = typer.Option(20, "--limit", "-n", min=1, max=200),
    workspace: str | None = typer.Option(None, "--workspace", "-w"),
    config: str | None = typer.Option(None, "--config", "-c"),
) -> None:
    """List recent RuiClaw runs."""
    _, loaded = _load_inspection_config(config=config, workspace=workspace)
    rows = RunInspector(loaded.workspace_path).list_runs(limit=limit)
    table = Table(title="RuiClaw Runs")
    for column in (
        "Run ID",
        "Status",
        "Model",
        "Duration",
        "Tokens",
        "Cost",
        "Provider calls",
        "Tools",
        "Started",
    ):
        table.add_column(column)
    for row in rows:
        duration = f"{row['duration_ms']} ms" if row["duration_ms"] is not None else "-"
        table.add_row(
            row["run_id"],
            row["status"],
            row["model"] or "-",
            duration,
            str(row["total_tokens"]),
            _format_cost(row["cost_status"], row["estimated_cost_usd"]),
            str(row["provider_calls"] or row["model_calls"]),
            str(row["tool_calls"]),
            row["started_at"] or "-",
        )
    console.print(table)
    if not rows:
        console.print("[dim]No RuiClaw runs recorded in this workspace.[/dim]")


@runs_app.command("show")
def show_run(
    run_id: str = typer.Argument(..., help="Run ID"),
    workspace: str | None = typer.Option(None, "--workspace", "-w"),
    config: str | None = typer.Option(None, "--config", "-c"),
) -> None:
    """Show one RuiClaw run, its context distribution, and timeline."""
    _, loaded = _load_inspection_config(config=config, workspace=workspace)
    detail = RunInspector(loaded.workspace_path).get_run(run_id)
    if detail is None:
        console.print(f"[red]Run not found: {escape(run_id)}[/red]")
        raise typer.Exit(1)

    summary = cast(dict[str, Any], detail["summary"])
    console.print(f"[bold]RuiClaw Run {escape(run_id)}[/bold]")
    console.print(
        f"Status: {escape(str(summary['status']))}  "
        f"Model: {escape(str(summary.get('model') or '-'))}  "
        f"Duration: {summary.get('duration_ms') or '-'} ms  "
        f"Tokens: {summary.get('total_tokens', 0)}  "
        f"Cost: {_format_cost(summary.get('cost_status'), summary.get('estimated_cost_usd'))}  "
        f"Provider calls: {summary.get('provider_calls') or summary.get('model_calls', 0)}"
    )

    report = cast(dict[str, Any], detail["report"])
    context_value = cast(object, report.get("context"))
    context = cast(dict[str, object], context_value) if isinstance(context_value, dict) else {}
    source_tokens_value = context.get("max_source_tokens")
    raw_source_tokens = (
        cast(dict[object, object], source_tokens_value)
        if isinstance(source_tokens_value, dict)
        else {}
    )
    source_tokens = [
        (source, tokens)
        for source, tokens in raw_source_tokens.items()
        if isinstance(source, str)
        and isinstance(tokens, int)
        and not isinstance(tokens, bool)
    ]
    context_table = Table(title="Context Sources")
    context_table.add_column("Source")
    context_table.add_column("Peak tokens", justify="right")
    for source, tokens in sorted(source_tokens, key=lambda item: item[1], reverse=True):
        context_table.add_row(str(source), str(tokens))
    console.print(context_table)

    timeline = Table(title="Timeline")
    timeline.add_column("Seq", justify="right")
    timeline.add_column("Event")
    timeline.add_column("Status / Target")
    timeline.add_column("Duration", justify="right")
    for raw_event in cast(list[object], detail["events"]):
        if not isinstance(raw_event, dict):
            continue
        event = cast(dict[str, Any], raw_event)
        payload_value = cast(object, event.get("payload"))
        payload = (
            cast(dict[str, object], payload_value)
            if isinstance(payload_value, dict)
            else {}
        )
        target = payload.get("tool_name") or payload.get("model") or payload.get("status") or ""
        duration = payload.get("duration_ms")
        timeline.add_row(
            str(event.get("sequence", "")),
            str(event.get("event_type", "")),
            str(target),
            f"{duration} ms" if isinstance(duration, int | float) else "",
        )
    console.print(timeline)
    if detail["events_truncated"]:
        console.print(
            f"[dim]Showing the latest {len(detail['events'])} of "
            f"{detail['event_count']} events.[/dim]"
        )


def _format_cost(status: object, value: object) -> str:
    if status != "estimated" or not isinstance(value, int | float) or isinstance(value, bool):
        return "cost_unknown" if status == "unknown" else str(status or "cost_unknown")
    return f"${float(value):.6f}"
