"""Publishing configuration scaffolding.

Generates `publishing.yaml` entries from a dbt manifest.

This module provides utilities to scaffold and manage publishing configuration
for dbt models. It extracts model metadata from dbt manifests and generates
configuration for publishing data to downstream systems like Postgres.

Example:
    >>> # Via CLI:
    >>> # phlo dbt publishing scaffold --select mrt_* --output publishing.yaml
    >>>
    >>> # Programmatically:
    >>> from phlo_dbt.cli_publishing import scaffold_publishing_config
    >>> config = scaffold_publishing_config(
    ...     existing_config={},
    ...     model_names=["mrt_orders", "mrt_customers"],
    ...     source_key="analytics",
    ...     physical_schema="marts",
    ...     group="publishing",
    ...     asset_name="publish_analytics_marts",
    ...     description="Published analytics marts"
    ... )

"""

from __future__ import annotations

import fnmatch
import json
from pathlib import Path
from typing import Any, Iterable

import click
import yaml

from phlo.cli.authorization_wrappers import enforce_surface_mutation_authorization
from phlo.cli.infrastructure.utils import get_project_name
from phlo.logging import get_logger
from phlo_dbt.authorization import get_dbt_adapter
from phlo_dbt.settings import get_settings

logger = get_logger(__name__)


def _normalize_select_patterns(select: Iterable[str]) -> list[str]:
    """Normalize raw ``--select`` values into a flattened, trimmed list of glob patterns."""
    patterns: list[str] = []
    for raw in select:
        for part in raw.split(","):
            part = part.strip()
            if part:
                patterns.append(part)
    return patterns


def _select_models(model_names: list[str], patterns: list[str]) -> list[str]:
    """Filter model names using glob patterns, preserving input order."""
    if not patterns:
        return model_names

    selected: list[str] = []
    for name in model_names:
        if any(fnmatch.fnmatchcase(name, pattern) for pattern in patterns):
            selected.append(name)
    return selected


def _load_yaml(path: Path) -> dict[str, Any]:
    """Load a YAML mapping from disk; a missing file yields an empty mapping.

    Raises: ValueError when the root YAML value is not a mapping.
    """
    if not path.exists():
        return {}
    with open(path) as f:
        data = yaml.safe_load(f) or {}
    if not isinstance(data, dict):
        raise ValueError(f"Expected YAML mapping at root in {path}")
    return data


def _dump_yaml(data: dict[str, Any]) -> str:
    """Serialize a configuration mapping to YAML text."""
    return yaml.safe_dump(data, sort_keys=False)


def _load_manifest_models(manifest_path: Path) -> dict[str, dict[str, Any]]:
    """Load dbt models from ``manifest.json``, keyed by model name.

    Raises: click.ClickException when file read or JSON parsing fails.
    """
    try:
        manifest = json.loads(manifest_path.read_text())
    except OSError as e:
        logger.exception(
            "dbt_publishing_manifest_read_failed",
            manifest_path=str(manifest_path),
            error=str(e),
        )
        raise click.ClickException(f"Failed to read manifest: {manifest_path}") from e
    except json.JSONDecodeError as e:
        logger.exception(
            "dbt_publishing_manifest_invalid_json",
            manifest_path=str(manifest_path),
            error=str(e),
        )
        raise click.ClickException(f"Invalid JSON in manifest: {manifest_path}") from e

    models: dict[str, dict[str, Any]] = {}
    for unique_id, node in (manifest.get("nodes") or {}).items():
        if not isinstance(unique_id, str) or not unique_id.startswith("model."):
            continue
        if not isinstance(node, dict):
            continue
        name = node.get("name")
        if isinstance(name, str) and name:
            models[name] = node
    return models


def scaffold_publishing_config(
    *,
    existing_config: dict[str, Any],
    model_names: list[str],
    source_key: str,
    physical_schema: str,
    group: str,
    asset_name: str,
    description: str,
    logical_refs: bool = False,
) -> dict[str, Any]:
    """Merge scaffolded publishing config for model_names into existing_config and
    return the updated mapping. source_key, physical_schema, group, asset_name,
    and description shape the generated entry; logical_refs stores dbt-style
    logical references instead of physical schema.table strings.

    Raises: ValueError when the existing config shape is invalid.
    """
    config: dict[str, Any] = dict(existing_config)
    publishing = config.get("publishing", {})
    if publishing is None:
        publishing = {}
    if not isinstance(publishing, dict):
        raise ValueError("Expected `publishing` to be a mapping in publishing.yaml")

    existing_entry = publishing.get(source_key, {}) or {}
    if not isinstance(existing_entry, dict):
        raise ValueError(f"Expected publishing.{source_key} to be a mapping in publishing.yaml")

    entry: dict[str, Any] = dict(existing_entry)
    entry.setdefault("name", asset_name)
    entry.setdefault("group", group)
    entry.setdefault("description", description)

    tables_existing = entry.get("tables", {}) or {}
    if not isinstance(tables_existing, dict):
        raise ValueError(f"Expected publishing.{source_key}.tables to be a mapping")

    tables: dict[str, str] = {str(k): str(v) for k, v in tables_existing.items()}
    for model_name in model_names:
        tables.setdefault(
            model_name,
            _publishing_table_reference(
                model_name, physical_schema=physical_schema, logical_refs=logical_refs
            ),
        )
    entry["tables"] = tables

    deps_existing = entry.get("dependencies", []) or []
    if not isinstance(deps_existing, list):
        raise ValueError(f"Expected publishing.{source_key}.dependencies to be a list")
    dependencies = [str(dep) for dep in deps_existing]
    for model_name in model_names:
        if model_name not in dependencies:
            dependencies.append(model_name)
    entry["dependencies"] = dependencies

    publishing[source_key] = entry
    config["publishing"] = publishing
    return config


def _publishing_table_reference(
    model_name: str,
    *,
    physical_schema: str,
    logical_refs: bool,
) -> str:
    """Return the generated source reference used in publishing table mappings."""
    if logical_refs:
        return f"ref:{model_name}"
    return f"{physical_schema}.{model_name}"


@click.group()
def publishing():
    """Manage publishing configuration."""


@publishing.command("scaffold")
@click.option(
    "--manifest",
    type=click.Path(dir_okay=False, path_type=Path),
    help="Path to dbt manifest.json (default: from settings)",
)
@click.option(
    "--output",
    type=click.Path(dir_okay=False, path_type=Path),
    default=Path("publishing.yaml"),
    show_default=True,
    help="Path to write publishing.yaml",
)
@click.option(
    "--select",
    "select_patterns",
    multiple=True,
    default=("mrt_*",),
    show_default=True,
    help="Model name glob(s) to include (comma-separated allowed)",
)
@click.option(
    "--source",
    "source_key",
    default=None,
    help="publishing.<source> key to write under (default: project name)",
)
@click.option(
    "--physical-schema",
    "physical_schema",
    default="marts",
    show_default=True,
    help="Physical source schema to reference when writing physical table mappings",
)
@click.option(
    "--iceberg-schema",
    "physical_schema",
    hidden=True,
)
@click.option(
    "--logical-refs/--physical-tables",
    default=True,
    show_default=True,
    help="Write dbt logical refs in table mappings instead of physical schema.table names.",
)
@click.option(
    "--group",
    default="publishing",
    show_default=True,
    help="Dagster group_name for the publishing asset",
)
@click.option(
    "--asset-name",
    default=None,
    help="Dagster asset name to generate (default: publish_<source>_marts)",
)
@click.option(
    "--dry-run",
    is_flag=True,
    help="Print YAML to stdout instead of writing output file",
)
def scaffold_cmd(
    manifest: Path | None,
    output: Path,
    select_patterns: tuple[str, ...],
    source_key: str | None,
    physical_schema: str,
    logical_refs: bool,
    group: str,
    asset_name: str | None,
    dry_run: bool,
):
    """Scaffold `publishing.yaml` from a dbt manifest.

    Idempotent: re-running preserves existing config and only adds missing tables/dependencies.
    """
    if not dry_run:
        enforce_surface_mutation_authorization("dbt.publishing.scaffold", get_dbt_adapter)
    manifest_path = manifest or Path(get_settings().dbt_manifest_path)
    logger.info(
        "dbt_publishing_scaffold_started",
        manifest_path=str(manifest_path),
        output_path=str(output),
        dry_run=dry_run,
        select_patterns=list(select_patterns),
    )
    if not manifest_path.is_absolute():
        manifest_path = (Path.cwd() / manifest_path).resolve()
    if not manifest_path.exists():
        logger.warning(
            "dbt_publishing_manifest_missing",
            manifest_path=str(manifest_path),
        )
        raise click.ClickException(f"dbt manifest not found at {manifest_path}")

    models = _load_manifest_models(manifest_path)
    all_model_names = sorted(models.keys())

    patterns = _normalize_select_patterns(select_patterns)
    selected_model_names = _select_models(all_model_names, patterns)
    if not selected_model_names:
        logger.warning(
            "dbt_publishing_no_models_selected",
            manifest_path=str(manifest_path),
            patterns=patterns,
            available_model_count=len(all_model_names),
        )
        raise click.ClickException(f"No models matched selection: {', '.join(patterns)}")

    resolved_source_key = source_key or get_project_name()
    resolved_asset_name = asset_name or f"publish_{resolved_source_key}_marts"
    resolved_description = (
        f"Publish {len(selected_model_names)} dbt marts to Postgres via Trino (scaffolded)."
    )

    existing_config = _load_yaml(output)
    updated_config = scaffold_publishing_config(
        existing_config=existing_config,
        model_names=selected_model_names,
        source_key=resolved_source_key,
        physical_schema=physical_schema,
        logical_refs=logical_refs,
        group=group,
        asset_name=resolved_asset_name,
        description=resolved_description,
    )

    rendered = _dump_yaml(updated_config)
    if dry_run:
        logger.info(
            "dbt_publishing_scaffold_rendered",
            source_key=resolved_source_key,
            selected_model_count=len(selected_model_names),
            output_mode="stdout",
        )
        click.echo(rendered)
        return

    output.write_text(rendered)
    logger.info(
        "dbt_publishing_scaffold_written",
        source_key=resolved_source_key,
        selected_model_count=len(selected_model_names),
        output_path=str(output),
    )
    click.echo(f"Wrote {output}")
