"""Core metrics CLI commands.

Exposes pipeline and per-asset metric summaries from the shared
metrics collector, plus export to JSON, CSV, or Prometheus text.
Every command accepts --json for stable machine-readable output;
period strings (24h/7d/2w) are parsed to hours with a documented
fallback for unknown suffixes.
Imported by the phlo CLI main (src/phlo/cli/main.py) to expose metric commands.
Surfaces phlo.metrics collector data and phlo.capabilities.maintenance rendering.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import click
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from phlo.capabilities.maintenance import render_maintenance_prometheus
from phlo.cli.output import json_envelope
from phlo.logging import get_logger
from phlo.metrics import get_metrics_collector

console = Console()
logger = get_logger(__name__)


@click.group(name="metrics")
def metrics_group() -> None:
    """Pipeline and data metrics exposure."""


@metrics_group.command(name="summary")
@click.option("--period", type=str, default="24h", help="Time period to analyze (e.g., 24h, 7d)")
@click.option("--json", "output_json", is_flag=True, help="Emit machine-readable JSON.")
def metrics_summary(period: str, output_json: bool) -> None:
    """Show key metrics overview."""
    collector = get_metrics_collector()
    period_hours = _parse_period(period)
    metrics = collector.collect_summary(period_hours)
    if output_json:
        click.echo(
            json_envelope(
                data={
                    "period": period,
                    "period_hours": period_hours,
                    "metrics": _dataclass_to_dict(metrics),
                }
            )
        )
        return
    summary_text = f"""
[bold]Platform Metrics Summary[/bold]

[cyan]Runs (last {period})[/cyan]
  Total:     {metrics.total_runs_24h}
  Success:   {metrics.successful_runs_24h} ({_percentage(metrics.successful_runs_24h, metrics.total_runs_24h)}%)
  Failure:   {metrics.failed_runs_24h} ({_percentage(metrics.failed_runs_24h, metrics.total_runs_24h)}%)

[cyan]Data Volume[/cyan]
  Rows:      {_format_number(metrics.total_rows_processed_24h)}
  Bytes:     {_format_bytes(metrics.total_bytes_written_24h)}

[cyan]Latency (seconds)[/cyan]
  p50:       {metrics.p50_duration_seconds:.2f}s
  p95:       {metrics.p95_duration_seconds:.2f}s
  p99:       {metrics.p99_duration_seconds:.2f}s

[cyan]Assets[/cyan]
  Active:    {metrics.active_assets_count}
  Success:   {metrics.assets_by_status.get("success", 0)}
  Warning:   {metrics.assets_by_status.get("warning", 0)}
  Failure:   {metrics.assets_by_status.get("failure", 0)}
"""
    console.print(Panel(summary_text, title="Metrics Summary", expand=False))


@metrics_group.command(name="asset")
@click.argument("asset_name")
@click.option("--runs", type=int, default=10, help="Number of past runs to display")
@click.option("--json", "output_json", is_flag=True, help="Emit machine-readable JSON.")
def metrics_asset(asset_name: str, runs: int, output_json: bool) -> None:
    """Show per-asset metrics."""
    collector = get_metrics_collector()
    metrics = collector.collect_asset(asset_name, runs=runs)
    if output_json:
        click.echo(
            json_envelope(
                data={
                    "asset_name": asset_name,
                    "runs": runs,
                    "metrics": _dataclass_to_dict(metrics),
                }
            )
        )
        return

    table = Table(title=f"Metrics for {asset_name}")
    table.add_column("Metric", style="cyan")
    table.add_column("Value", style="magenta")
    table.add_row("Last Run Status", metrics.last_run.status if metrics.last_run else "-")
    table.add_row(
        "Last Run Duration",
        (
            f"{metrics.last_run.duration_seconds:.2f}s"
            if metrics.last_run and metrics.last_run.duration_seconds
            else "-"
        ),
    )
    table.add_row("Average Duration", f"{metrics.average_duration:.2f}s")
    table.add_row("Failure Rate", f"{metrics.failure_rate:.1%}")
    table.add_row("Avg Rows/Run", f"{metrics.average_rows_per_run:,.0f}")
    table.add_row("Data Size", _format_bytes(metrics.data_growth_bytes))
    console.print(table)

    if metrics.last_10_runs:
        console.print()
        run_table = Table(title=f"Last {len(metrics.last_10_runs)} Runs")
        run_table.add_column("Run ID", style="cyan")
        run_table.add_column("Status", style="magenta")
        run_table.add_column("Duration", style="yellow")
        run_table.add_column("Rows", style="green")
        for run in metrics.last_10_runs:
            status_color = "green" if run.status == "success" else "red"
            duration_str = f"{run.duration_seconds:.2f}s" if run.duration_seconds else "-"
            run_table.add_row(
                run.run_id[:8],
                f"[{status_color}]{run.status}[/{status_color}]",
                duration_str,
                f"{run.rows_processed:,}",
            )
        console.print(run_table)


@metrics_group.command(name="export")
@click.option(
    "--format",
    "export_format",
    type=click.Choice(["json", "csv", "prometheus"]),
    default="json",
    help="Export format",
)
@click.option("--output", type=Path, required=True, help="Output file path")
@click.option("--period", type=str, default="24h", help="Time period to analyze (e.g., 24h, 7d)")
@click.option("--json", "output_json", is_flag=True, help="Emit machine-readable JSON.")
def metrics_export(export_format: str, output: Path, period: str, output_json: bool) -> None:
    """Export metrics to JSON, CSV, or Prometheus text."""
    collector = get_metrics_collector()
    period_hours = _parse_period(period)
    metrics = collector.collect_summary(period_hours)
    try:
        if export_format == "json":
            _export_json(metrics, output)
        elif export_format == "csv":
            _export_csv(metrics, output)
        else:
            output.write_text(render_maintenance_prometheus(), encoding="utf-8")
    except Exception:
        logger.warning(
            "metrics_export_failed",
            export_format=export_format,
            output_path=str(output),
            period=period,
            exc_info=True,
        )
        raise
    if output_json:
        click.echo(
            json_envelope(
                data={
                    "format": export_format,
                    "output": str(output),
                    "period": period,
                    "period_hours": period_hours,
                    "exported": True,
                }
            )
        )
        return
    console.print(f"[green]✓[/green] Metrics exported to {output}")


def _parse_period(period_str: str) -> int:
    """Parse a period suffix like ``24h``, ``7d``, or ``2w`` into hours.

    Unparsable or unrecognized values fall back to 24 hours instead of
    failing the command.
    """
    raw_period = period_str
    period_str = period_str.strip()
    fallback_hours = 24
    if period_str.endswith("h"):
        try:
            return int(period_str[:-1])
        except ValueError:
            logger.debug("metrics_period_parse_invalid_hours", period=raw_period)
            return fallback_hours
    if period_str.endswith("d"):
        try:
            return int(period_str[:-1]) * 24
        except ValueError:
            logger.debug("metrics_period_parse_invalid_days", period=raw_period)
            return fallback_hours
    if period_str.endswith("w"):
        try:
            return int(period_str[:-1]) * 24 * 7
        except ValueError:
            logger.debug("metrics_period_parse_invalid_weeks", period=raw_period)
            return fallback_hours
    logger.warning(
        "metrics_period_parse_unrecognized",
        period=raw_period,
        fallback_hours=fallback_hours,
        hint="Use a suffix: h (hours), d (days), or w (weeks)",
    )
    return fallback_hours


def _percentage(part: int, total: int) -> float:
    if total == 0:
        return 0.0
    return (part / total) * 100


def _format_number(num: int) -> str:
    return f"{num:,}"


def _format_bytes(bytes_val: int | float) -> str:
    val = float(bytes_val)
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if val < 1024:
            return f"{val:.2f} {unit}"
        val /= 1024
    return f"{val:.2f} PB"


def _export_json(metrics: object, output: Path) -> None:
    result = _dataclass_to_dict(metrics)
    if isinstance(result, dict):
        result_dict = cast(dict[str, Any], result)
        result_dict["exported_at"] = datetime.now(UTC).isoformat()
        output.write_text(json.dumps(result_dict, indent=2, default=str), encoding="utf-8")
        return
    output.write_text(
        json.dumps(
            {"data": result, "exported_at": datetime.now(UTC).isoformat()},
            indent=2,
            default=str,
        ),
        encoding="utf-8",
    )


def _export_csv(metrics: object, output: Path) -> None:
    import csv

    result = _dataclass_to_dict(metrics)
    if isinstance(result, dict):
        result_dict = cast(dict[str, Any], result)
        result_dict["exported_at"] = datetime.now(UTC).isoformat()
        with output.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=result_dict.keys())
            writer.writeheader()
            writer.writerow(result_dict)
        return

    with output.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["data", "exported_at"])
        writer.writerow([str(result), datetime.now(UTC).isoformat()])


def _dataclass_to_dict(obj: object) -> dict[str, Any] | object:
    import dataclasses

    if not dataclasses.is_dataclass(obj):
        return obj

    result: dict[str, Any] = {}
    for field in dataclasses.fields(obj):
        value = getattr(obj, field.name)
        if dataclasses.is_dataclass(value):
            result[field.name] = _dataclass_to_dict(value)
        elif isinstance(value, dict):
            result[field.name] = {k: _dataclass_to_dict(v) for k, v in value.items()}
        elif isinstance(value, list):
            result[field.name] = [
                _dataclass_to_dict(item) if dataclasses.is_dataclass(item) else item
                for item in value
            ]
        else:
            result[field.name] = value
    return result
