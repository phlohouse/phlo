"""Offline support-status CLI command.

Compares installed Phlo artifacts against the bundled release manifest
without network access. Exit codes are contractual: 0 compatible,
1 incompatible, 2 unknown (no manifest). JSON output is stable and
sorted for machine consumption.
Registered into the phlo CLI by src/phlo/cli/main.py; reads support status from
phlo.capabilities.support_status.
"""

from __future__ import annotations

import json
from typing import Any

import click

from phlo.capabilities.support_status import support_status


def _render_human(status: dict[str, Any]) -> str:
    manifest = status["manifest"]
    lines = [
        "Phlo Support Status",
        "",
        f"Compatible: {status['compatible']}",
        f"Production ready: {status['production_ready']}",
    ]
    lines.append(f"Manifest: {manifest['source']} ({manifest['trust']})")
    lines.append(f"Staleness: {manifest['staleness']}")
    lines.extend(["", "Packages:"])
    for item in status["items"]:
        lines.append(
            f"  {item['status']:<10} {item['name']}: expected {item['expected']}, installed {item['installed']}"
        )
    lines.append(
        "Gates: " + ", ".join(f"{name}={state}" for name, state in status["gates"].items())
    )
    return "\n".join(lines)


@click.group("support")
def support_group() -> None:
    """Inspect the bundled, offline support contract."""


@support_group.command("status")
@click.option("--json", "output_json", is_flag=True, help="Emit stable JSON output.")
def status_cmd(output_json: bool) -> None:
    """Compare installed Phlo artifacts with the bundled release set."""
    status = support_status()
    click.echo(json.dumps(status, sort_keys=True) if output_json else _render_human(status))
    if status["compatible"] is None:
        raise click.exceptions.Exit(2)
    if not status["compatible"]:
        raise click.exceptions.Exit(1)
