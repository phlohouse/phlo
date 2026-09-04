"""Governance CLI commands.

`check` validates governed tables and exits non-zero on failure for CI gating;
`export` prints the browser-safe read model. Both optionally import user
modules first so declarations register before the surface is built.
Registered into the phlo CLI by src/phlo/cli/main.py; builds on phlo.flow and
phlo.governance.
"""

from __future__ import annotations

import importlib
import importlib.util
import json
from pathlib import Path
from typing import Any

import click

from phlo.governance import build_governance_surface


@click.group(name="governance")
def governance_group() -> None:
    """Check and export governance readiness from Phlo declarations."""


def _datasets_section() -> dict[str, Any]:
    """Build the canonical Dataset section from the core authority.

    Reads the durable Dataset store through the shared projection; when no
    durable store capability is installed the section reports itself
    unavailable instead of failing the declaration checks.
    """
    from phlo.dataset_projection import build_dataset_authority

    try:
        authority = build_dataset_authority()
    except Exception as exc:
        return {"available": False, "reason": str(exc)}
    projections = [authority.projection(table) for table in sorted(authority.surface.tables)]
    return {"available": True, "datasets": projections}


@governance_group.command(name="check")
@click.option("--json", "output_json", is_flag=True, help="Emit machine-readable JSON.")
@click.option(
    "--module",
    "modules",
    multiple=True,
    help="Import a Python module or .py file that registers Phlo declarations.",
)
def check_cmd(output_json: bool, modules: tuple[str, ...]) -> None:
    """Validate governed tables for publish and production readiness.

    Exits with status 1 when any check fails, so it can be used as a CI gate.
    """
    _load_modules(modules)
    surface = build_governance_surface()
    payload = surface.to_check_result()
    payload["datasets"] = _datasets_section()
    if output_json:
        click.echo(json.dumps(payload, indent=2, sort_keys=True))
    else:
        _echo_check_result(payload)
    if not payload["ok"]:
        raise click.exceptions.Exit(1)


@governance_group.command(name="export")
@click.option("--json", "output_json", is_flag=True, help="Emit machine-readable JSON.")
@click.option(
    "--module",
    "modules",
    multiple=True,
    help="Import a Python module or .py file that registers Phlo declarations.",
)
def export_cmd(output_json: bool, modules: tuple[str, ...]) -> None:
    """Export the browser-safe governance read model."""
    _load_modules(modules)
    surface = build_governance_surface()
    payload = surface.to_read_model()
    payload["datasets"] = _datasets_section()
    if output_json:
        click.echo(json.dumps(payload, indent=2, sort_keys=True))
        return
    for table in payload["tables"]:
        click.echo(
            f"{table['table']}: owner={table['owner'] or '-'} published={table['published']}"
        )


def _echo_check_result(payload: dict[str, Any]) -> None:
    if payload["ok"]:
        click.echo("Governance check passed")
        return
    click.echo("Governance check failed")
    warnings = payload.get("warnings", [])
    if not isinstance(warnings, list):
        return
    for warning in warnings:
        if isinstance(warning, dict):
            click.echo(f"- {warning['table']}: {warning['message']}")


def _load_modules(modules: tuple[str, ...]) -> None:
    if modules:
        from phlo.flow import clear_flow_declarations

        clear_flow_declarations()
    for module in modules:
        path = Path(module)
        if path.exists() or module.endswith(".py"):
            _load_module_from_path(path)
        else:
            importlib.import_module(module)


def _load_module_from_path(path: Path) -> None:
    if not path.exists():
        raise click.ClickException(f"Governance module file not found: {path}")
    resolved = path.resolve()
    module_name = f"_phlo_governance_{abs(hash(str(resolved)))}"
    spec = importlib.util.spec_from_file_location(module_name, resolved)
    if spec is None or spec.loader is None:
        raise click.ClickException(f"Could not load governance module: {path}")
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    except Exception as exc:
        raise click.ClickException(f"Could not load governance module: {path}: {exc}") from exc


__all__ = ["governance_group"]
