"""Schema migration CLI commands.

Provides commands to diff, plan, apply, and inspect schema migrations
between quality provider schemas and storage tables.

Imported by phlo.cli.main and by phlo_dagster's schema-contract framework.
Builds migrations on phlo.schema_migration planning and phlo.capabilities.discovery.
"""

from __future__ import annotations

import json
import sys
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import click
from rich.console import Console
from rich.table import Table

from phlo.capabilities import (
    FieldSpec,
    NormalizedSchema,
    SchemaMigrationPlan,
    configured_capability_name,
    get_capability_registry,
    list_capabilities,
    resolve_capability,
)
from phlo.capabilities.discovery import discover_capabilities
from phlo.cli.authorization_wrappers import require_mutation_authorization
from phlo.cli.commands import schema_migrate_contracts
from phlo.cli.contract import PhloCommand, PhloGroup
from phlo.cli.output import confirm_action, json_envelope, user_error
from phlo.logging import get_logger
from phlo.schema_migration.instructions import (
    MigrationInstructionError,
    resolve_migration_instructions,
)
from phlo.schema_migration.planning import (
    SchemaMigrationPlanningError,
)

console = Console()
logger = get_logger(__name__)


def _resolve_migrator() -> Any:
    """Resolve the registered SchemaMigrator from the capability registry."""
    from phlo.capabilities.discovery import discover_capabilities

    discover_capabilities()

    registry = get_capability_registry()
    migrators = registry.list("schema_migrator")
    if not migrators:
        raise click.ClickException(
            "No schema migrator registered. Install a storage provider (e.g. phlo-iceberg) that implements SchemaMigrator."
        )

    migrators_by_name = {migrator.name: migrator for migrator in migrators}

    configured_migrator = configured_capability_name("schema_migrator")
    if configured_migrator:
        selected = migrators_by_name.get(configured_migrator)
        if selected is None:
            available = list_capabilities("schema_migrator")
            raise click.ClickException(
                f"Configured schema migrator '{configured_migrator}' is not registered. Available schema migrators: {available}"
            )
        return selected.provider

    default_table_store = configured_capability_name("table_store")
    if default_table_store:
        selected = migrators_by_name.get(default_table_store)
        if selected is not None:
            return selected.provider

    if len(migrators) == 1:
        return migrators[0].provider

    available = list_capabilities("schema_migrator")
    table_store_options = list_capabilities("table_store")
    details = [
        "Multiple schema migrators are registered and none was selected.",
        "Configure `schema_migrator` directly or set a matching default `table_store`.",
    ]
    if default_table_store:
        details.append(
            f"Configured table_store '{default_table_store}' does not match any registered schema migrator."
        )
    details.append(f"Available schema migrators: {available}")
    if table_store_options:
        details.append(f"Available table stores: {table_store_options}")
    raise click.ClickException("\n".join(details))


def _resolve_extractor() -> Any:
    """Resolve the installed schema discovery capability."""
    discover_capabilities()
    resolution = resolve_capability("schema_discovery")
    return resolution.provider if resolution is not None else None


def _discover_schema_for_table(table_name: str) -> Any:
    """Discover the native schema associated with a table through a capability."""
    provider = _resolve_extractor()
    if provider is None:
        return None
    schemas = provider.discover_schemas()

    short_name = table_name.split(".")[-1] if "." in table_name else table_name

    for name, schema_cls in schemas.items():
        cls_lower = name.lower().replace("_", "")
        table_lower = short_name.lower().replace("_", "")
        if cls_lower == table_lower or table_lower in cls_lower:
            return schema_cls

    return None


def _resolve_desired_schema(table_name: str, schema_class: str | None) -> tuple[Any, Any, Any]:
    """Resolve migrator, extracted desired schema, and native schema class."""
    migrator = _resolve_migrator()
    extractor = _resolve_extractor()

    if extractor is None:
        raise click.ClickException(
            "schema_discovery capability is unavailable. Install a provider that supplies schema discovery."
        )

    native_schema = _find_native_schema(table_name, schema_class)
    if native_schema is None:
        raise click.ClickException(f"No quality schema found for table: {table_name}")

    desired = extractor.extract(native_schema)
    return migrator, desired, native_schema


def _build_migration_plan(
    *,
    table_name: str,
    schema_class: str | None,
    migration_file: Path | None,
    rename: tuple[str, ...],
) -> tuple[Any, SchemaMigrationPlan]:
    """Resolve the selected migrator and build a plan from CLI inputs."""
    try:
        instructions = resolve_migration_instructions(
            table_name=table_name,
            migration_file=migration_file,
            rename_flags=rename,
        )
    except MigrationInstructionError as exc:
        raise click.ClickException(str(exc)) from exc

    migrator, desired, _ = _resolve_desired_schema(table_name, schema_class)
    try:
        plan = migrator.diff_schema(
            table_name=table_name,
            desired=desired,
            instructions=instructions,
        )
    except SchemaMigrationPlanningError as exc:
        raise click.ClickException(str(exc)) from exc
    return migrator, plan


def _ensure_plan_supported(migrator: Any, plan: SchemaMigrationPlan) -> None:
    """Refuse to apply unsupported explicit change types."""
    if not hasattr(migrator, "supported_changes"):
        return
    supported = migrator.supported_changes()
    unsupported = sorted(
        {change.change_type for change in plan.changes if change.change_type not in supported}
    )
    if not unsupported:
        return
    rendered = ", ".join(unsupported)
    raise click.ClickException(
        "Schema migration plan contains unsupported change type(s) for the selected "
        f"schema migrator: {rendered}. Use a provider that supports them, or change the "
        "migration YAML/CLI flags."
    )


def _select_default_table_store_name() -> str | None:
    """Return the selected default table-store name, if available."""
    resolution = resolve_capability("table_store")
    return resolution.name if resolution is not None else None


def _select_default_migrator_name() -> str | None:
    """Return the selected default schema-migrator name, if available."""
    resolution = resolve_capability("schema_migrator")
    return resolution.name if resolution is not None else None


def _collect_quality_checks(table_name: str) -> list[dict[str, Any]]:
    """Collect registered check metadata associated with a table asset."""
    short_name = table_name.split(".")[-1]
    target_key = f"dlt_{short_name}"
    checks: list[dict[str, Any]] = []
    for check in get_capability_registry().list("check"):
        if check.asset_key != target_key:
            continue
        tags = dict(check.tags)
        consumers = tags.get("contract_consumers", "")
        contract_consumers = [c for c in consumers.split(",") if c]
        contract_sla: dict[str, Any] | None = None
        raw_sla = tags.get("contract_sla")
        if raw_sla:
            try:
                parsed = json.loads(raw_sla)
                if isinstance(parsed, dict):
                    contract_sla = parsed
            except json.JSONDecodeError:
                contract_sla = None
        checks.append(
            {
                "name": check.name,
                "severity": check.severity,
                "blocking": check.blocking,
                "description": check.description,
                "tags": tags,
                "owner": tags.get("contract_owner"),
                "consumers": contract_consumers,
                "sla": contract_sla,
            }
        )
    return checks


def _collect_contract_metadata(table_name: str) -> dict[str, Any]:
    """Collect owner/consumer/SLA metadata for a table contract."""
    short_name = table_name.split(".")[-1]
    target_key = f"dlt_{short_name}"
    owner: str | None = None
    consumers: list[dict[str, Any]] = []
    sla: dict[str, Any] | None = None

    for asset in get_capability_registry().list("asset"):
        if asset.key != target_key:
            continue
        if asset.metadata.get("table_name") not in {short_name, table_name}:
            continue

        asset_owner = asset.metadata.get("owner")
        if isinstance(asset_owner, str) and asset_owner:
            owner = asset_owner

        asset_consumers = asset.metadata.get("consumers")
        if isinstance(asset_consumers, list):
            consumers = [c for c in asset_consumers if isinstance(c, dict)]

        asset_sla = asset.metadata.get("sla")
        if isinstance(asset_sla, dict):
            sla = asset_sla
        break

    return {"owner": owner, "consumers": consumers, "sla": sla}


def _collect_transform_refs(table_name: str) -> list[str]:
    """Collect transform asset refs that likely depend on the table."""
    short_name = table_name.split(".")[-1]
    target_key = f"dlt_{short_name}"
    refs: list[str] = []
    for asset in get_capability_registry().list("asset"):
        kinds = asset.kinds or set()
        deps = asset.deps or []
        if "dbt" not in kinds:
            continue
        if target_key in deps or short_name in deps:
            refs.append(asset.key)
    return sorted(set(refs))


def export_contract_for_table(
    *,
    table_name: str,
    schema_class: str | None = None,
    output_path: Path | None = None,
    force: bool = False,
) -> Path:
    """Export a schema contract for a table and return output path."""
    migrator, desired, native_schema = _resolve_desired_schema(table_name, schema_class)
    migration_plan = migrator.diff_schema(table_name=table_name, desired=desired)
    now = datetime.now(UTC).isoformat()

    contract = {
        "contract_version": schema_migrate_contracts.CONTRACT_VERSION,
        "generated_at": now,
        "table_name": table_name,
        "schema_class": getattr(native_schema, "__name__", None),
        "table_store": _select_default_table_store_name(),
        "schema_migrator": _select_default_migrator_name(),
        "normalized_schema": asdict(desired),
        "migration_plan": asdict(migration_plan),
        "contract_metadata": _collect_contract_metadata(table_name),
        "quality_checks": _collect_quality_checks(table_name),
        "transform_refs": _collect_transform_refs(table_name),
    }
    destination = output_path or schema_migrate_contracts.default_contract_path(table_name)
    schema_migrate_contracts.write_contract(destination, contract, force=force)
    return destination


def _is_missing_table_error(exc: BaseException) -> bool:
    """Return whether a schema refresh failed because the table is not available yet."""
    try:
        from pyiceberg.exceptions import NoSuchIdentifierError, NoSuchTableError
    except Exception:
        # pyiceberg is an optional dependency; when its exceptions are
        # unavailable, classify by exception name and message text instead.
        missing_error_types: tuple[type[BaseException], ...] = ()
    else:
        missing_error_types = (NoSuchIdentifierError, NoSuchTableError)

    if missing_error_types and isinstance(exc, missing_error_types):
        return True

    exc_name = type(exc).__name__
    message = str(exc).lower()
    return (
        exc_name in {"NoSuchIdentifierError", "NoSuchTableError"}
        or "table does not exist" in message
        or "missing namespace or invalid identifier" in message
    )


def refresh_contracts_for_selection(
    *,
    selection: str | None = None,
    force: bool = True,
) -> int:
    """Refresh contracts for a materialization selection. Returns write count."""
    registry = get_capability_registry()
    candidate_tables: set[str] = set()
    selection_value = (selection or "").strip()
    found_matching_dlt_asset = False

    if selection_value.startswith("dlt_"):
        candidate_tables.add(selection_value.removeprefix("dlt_"))

    for asset in registry.list("asset"):
        kinds = asset.kinds or set()
        if "dlt" not in kinds:
            continue
        table_name = asset.metadata.get("table_name")
        if not isinstance(table_name, str) or not table_name:
            continue
        if selection_value and selection_value.startswith("dlt_") and asset.key != selection_value:
            continue
        found_matching_dlt_asset = True
        candidate_tables.add(table_name)

    if found_matching_dlt_asset and selection_value.startswith("dlt_"):
        candidate_tables.discard(selection_value.removeprefix("dlt_"))

    refreshed_count = 0
    for table in sorted(candidate_tables):
        # Each table resolves against two candidate names: namespace-resolved
        # first, then the bare name. The first candidate that exports cleanly
        # wins; a missing table skips the candidate instead of failing.
        table_candidates = [table]
        if "." not in table:
            namespace_resolver = _resolve_namespace_resolver()
            if namespace_resolver is None:
                raise click.ClickException("namespace_resolver capability is unavailable.")
            table_candidates.insert(0, namespace_resolver.resolve_namespace(table))

        for candidate in table_candidates:
            try:
                export_contract_for_table(table_name=candidate, force=force)
            except SystemExit:
                raise
            except Exception as exc:
                if _is_missing_table_error(exc):
                    logger.debug(
                        "schema_contract_refresh_skipped_table_missing",
                        table_name=candidate,
                        selection=selection_value or None,
                    )
                    continue
                logger.warning(
                    "schema_contract_refresh_failed",
                    table_name=candidate,
                    selection=selection_value or None,
                    exc_info=True,
                )
                continue
            refreshed_count += 1
            break
    return refreshed_count


@click.group("schema-migrate", cls=PhloGroup)
def schema_migrate_group() -> None:
    """Schema migration between quality schemas and storage tables."""


@schema_migrate_group.command()
@click.argument("table_name")
@click.option(
    "--schema-class", default=None, help="Pandera schema class name (auto-detected if omitted)"
)
@click.option(
    "--migration-file",
    type=click.Path(path_type=Path, dir_okay=False),
    default=None,
    help="Migration instruction YAML (default: .phlo/migrations/<table>.yaml)",
)
@click.option(
    "--rename",
    multiple=True,
    help="Explicit rename instruction in old_name=new_name form. May be repeated.",
)
@click.option("--format", "fmt", type=click.Choice(["table", "json"]), default="table")
def diff(
    table_name: str,
    schema_class: str | None,
    migration_file: Path | None,
    rename: tuple[str, ...],
    fmt: str,
) -> None:
    """Show pending schema changes between quality schema and storage table.

    Examples:
        phlo schema-migrate diff warehouse.customers
        phlo schema-migrate diff warehouse.customers --schema-class CustomerSchema
        phlo schema-migrate diff warehouse.customers --format json
    """
    _, migration_plan = _build_migration_plan(
        table_name=table_name,
        schema_class=schema_class,
        migration_file=migration_file,
        rename=rename,
    )

    if fmt == "json":
        click.echo(json.dumps(asdict(migration_plan), indent=2))
        return

    if not migration_plan.changes:
        console.print(f"[green]No schema changes detected for {table_name}[/green]")
        return

    _render_plan(migration_plan)


@schema_migrate_group.command()
@click.argument("table_name")
@click.option(
    "--schema-class", default=None, help="Pandera schema class name (auto-detected if omitted)"
)
@click.option(
    "--migration-file",
    type=click.Path(path_type=Path, dir_okay=False),
    default=None,
    help="Migration instruction YAML (default: .phlo/migrations/<table>.yaml)",
)
@click.option(
    "--rename",
    multiple=True,
    help="Explicit rename instruction in old_name=new_name form. May be repeated.",
)
@click.option("--format", "fmt", type=click.Choice(["table", "json"]), default="table")
def plan(
    table_name: str,
    schema_class: str | None,
    migration_file: Path | None,
    rename: tuple[str, ...],
    fmt: str,
) -> None:
    """Generate a migration plan for a table.

    Examples:
        phlo schema-migrate plan warehouse.customers
    """
    _, migration_plan = _build_migration_plan(
        table_name=table_name,
        schema_class=schema_class,
        migration_file=migration_file,
        rename=rename,
    )

    if fmt == "json":
        click.echo(json.dumps(asdict(migration_plan), indent=2))
        return

    if not migration_plan.changes:
        console.print(f"[green]No migration needed for {table_name}[/green]")
        return

    _render_plan(migration_plan)

    if migration_plan.recommendations:
        console.print("\n[bold]Recommendations:[/bold]")
        for rec in migration_plan.recommendations:
            console.print(f"  • {rec}")


@schema_migrate_group.command(cls=PhloCommand)
@click.argument("table_name")
@click.option(
    "--schema-class", default=None, help="Pandera schema class name (auto-detected if omitted)"
)
@click.option(
    "--migration-file",
    type=click.Path(path_type=Path, dir_okay=False),
    default=None,
    help="Migration instruction YAML (default: .phlo/migrations/<table>.yaml)",
)
@click.option(
    "--rename",
    multiple=True,
    help="Explicit rename instruction in old_name=new_name form. May be repeated.",
)
@click.option(
    "--json", "output_json", is_flag=True, help="Emit the migration plan and outcome as JSON."
)
@click.option(
    "--non-interactive", is_flag=True, help="Never prompt; require --yes for breaking changes."
)
@click.option("--yes", is_flag=True, help="Auto-approve breaking changes")
@click.option("--dry-run", is_flag=True, help="Show what would be applied without executing")
@require_mutation_authorization(
    "schema_migrate.apply",
    when=lambda params: not params.get("dry_run"),
)
def apply(
    table_name: str,
    schema_class: str | None,
    migration_file: Path | None,
    rename: tuple[str, ...],
    yes: bool,
    dry_run: bool,
    output_json: bool = False,
    non_interactive: bool = False,
) -> None:
    """Apply schema migration to a storage table.

    Safe changes are applied automatically. Breaking changes require
    confirmation (or --yes to skip the prompt).

    Examples:
        phlo schema-migrate apply warehouse.customers
        phlo schema-migrate apply warehouse.customers --yes
        phlo schema-migrate apply warehouse.customers --dry-run
    """
    migrator, migration_plan = _build_migration_plan(
        table_name=table_name,
        schema_class=schema_class,
        migration_file=migration_file,
        rename=rename,
    )

    payload = {"table_name": table_name, "plan": asdict(migration_plan)}
    if not migration_plan.changes:
        if output_json:
            click.echo(json_envelope(data={**payload, "applied": False}, reason_code="no_changes"))
        else:
            console.print(f"[green]No migration needed for {table_name}[/green]")
        return

    _ensure_plan_supported(migrator, migration_plan)
    if not output_json:
        _render_plan(migration_plan)

    if dry_run:
        if output_json:
            click.echo(
                json_envelope(
                    data={**payload, "applied": False}, status="planned", reason_code="dry_run"
                )
            )
        else:
            console.print("\n[yellow]Dry run — no changes applied.[/yellow]")
        return

    if migration_plan.requires_approval and not confirm_action(
        "Apply breaking changes?", yes=yes, non_interactive=non_interactive or output_json
    ):
        raise user_error("Migration cancelled.", reason_code="cancelled")

    try:
        result = migrator.apply_plan(plan=migration_plan, approved=True)
    except Exception as exc:
        logger.exception("schema_migrate_apply_failed", table_name=table_name)
        raise click.ClickException(f"Migration failed: {exc}") from exc
    if output_json:
        click.echo(
            json_envelope(
                data={**payload, "applied": True, "result": result}, reason_code="migration_applied"
            )
        )
    else:
        console.print("\n[green]Migration applied successfully.[/green]")
        for key, value in result.items():
            console.print(f"  {key}: {value}")


@schema_migrate_group.command()
@click.argument("table_name")
@click.option("--limit", default=10, help="Max history entries to show")
@click.option("--format", "fmt", type=click.Choice(["table", "json"]), default="table")
def history(table_name: str, limit: int, fmt: str) -> None:
    """Show schema version history for a table.

    Examples:
        phlo schema-migrate history warehouse.customers
        phlo schema-migrate history warehouse.customers --limit 5
        phlo schema-migrate history warehouse.customers --format json
    """
    migrator = _resolve_migrator()

    try:
        entries = migrator.get_schema_history(table_name=table_name, limit=limit)
    except Exception as exc:
        console.print(f"[red]Could not load schema history for {table_name}: {exc}[/red]")
        sys.exit(1)

    if fmt == "json":
        click.echo(json.dumps(entries, indent=2, default=str))
        return

    if not entries:
        console.print(f"[yellow]No schema history found for {table_name}[/yellow]")
        return

    table = Table(title=f"Schema History: {table_name}")
    table.add_column("Snapshot", style="cyan")
    table.add_column("Timestamp", style="green")
    table.add_column("Summary", style="dim")

    for entry in entries:
        timestamp = entry.get("timestamp")
        if timestamp is None:
            timestamp = entry.get("timestamp_ms", "")
        table.add_row(
            str(entry.get("snapshot_id", "")),
            str(timestamp),
            str(entry.get("summary", "")),
        )

    console.print(table)


@schema_migrate_group.command("export-contract")
@click.argument("table_name")
@click.option(
    "--schema-class", default=None, help="Pandera schema class name (auto-detected if omitted)"
)
@click.option(
    "--output",
    "output_path",
    type=click.Path(path_type=Path, dir_okay=False),
    default=None,
    help="Contract output path (default: .phlo/contracts/<table>.json)",
)
@click.option("--force", is_flag=True, help="Overwrite existing output file")
@require_mutation_authorization("schema_migrate.export_contract")
def export_contract(
    table_name: str,
    schema_class: str | None,
    output_path: Path | None,
    force: bool,
) -> None:
    """Export a Phlo contract snapshot for a table."""
    try:
        destination = export_contract_for_table(
            table_name=table_name,
            schema_class=schema_class,
            output_path=output_path,
            force=force,
        )
    except FileExistsError as exc:
        console.print(f"[red]{exc}[/red]")
        sys.exit(1)

    console.print(f"[green]Exported contract:[/green] {destination}")


@schema_migrate_group.command("scaffold-yaml")
@click.argument("table_name")
@click.option(
    "--from-contract",
    "contract_path",
    type=click.Path(path_type=Path, dir_okay=False),
    default=None,
    help="Path to contract JSON (default: .phlo/contracts/<table>.json)",
)
@click.option(
    "--output",
    "output_path",
    type=click.Path(path_type=Path, dir_okay=False),
    default=None,
    help="YAML output path (default: .phlo/migrations/<table>.yaml)",
)
@click.option("--force", is_flag=True, help="Overwrite existing output file")
@require_mutation_authorization("schema_migrate.scaffold_yaml")
def scaffold_yaml(
    table_name: str,
    contract_path: Path | None,
    output_path: Path | None,
    force: bool,
) -> None:
    """Generate migration scaffold YAML from a Phlo contract."""
    source_path = contract_path or schema_migrate_contracts.default_contract_path(table_name)
    destination = output_path or schema_migrate_contracts.default_scaffold_yaml_path(table_name)

    try:
        payload = _build_scaffold_payload_from_contract(
            table_name=table_name,
            contract_path=source_path,
        )
        schema_migrate_contracts.write_scaffold_yaml(
            destination,
            payload=payload,
            force=force,
        )
    except (
        FileNotFoundError,
        ValueError,
        json.JSONDecodeError,
        TypeError,
        FileExistsError,
        Exception,
    ) as exc:
        console.print(f"[red]{exc}[/red]")
        sys.exit(1)

    console.print(f"[green]Generated migration scaffold:[/green] {destination}")


@schema_migrate_group.command("scaffold-yaml-recent")
@click.option(
    "--since-hours",
    type=int,
    default=24,
    show_default=True,
    help="Only include contracts modified in the last N hours",
)
@click.option(
    "--limit",
    type=int,
    default=None,
    help="Maximum number of contract additions to scaffold",
)
@click.option("--force", is_flag=True, help="Overwrite existing output files")
@require_mutation_authorization("schema_migrate.scaffold_yaml_recent")
def scaffold_yaml_recent(since_hours: int, limit: int | None, force: bool) -> None:
    """Generate migration scaffold YAML files for recent .phlo/contracts additions."""
    if since_hours < 0:
        console.print("[red]--since-hours must be >= 0[/red]")
        sys.exit(1)
    if limit is not None and limit <= 0:
        console.print("[red]--limit must be > 0[/red]")
        sys.exit(1)

    contract_paths = schema_migrate_contracts.list_recent_contract_paths(
        since_hours=since_hours,
        limit=limit,
    )
    if not contract_paths:
        console.print("[yellow]No recent contracts found in .phlo/contracts[/yellow]")
        return

    generated_count = 0
    errors: list[str] = []
    for contract_path in contract_paths:
        try:
            contract = schema_migrate_contracts.read_contract(contract_path)
            table_name = contract.get("table_name")
            if not isinstance(table_name, str) or not table_name:
                raise ValueError(f"Contract missing table_name: {contract_path}")

            payload = _build_scaffold_payload_from_contract(
                table_name=table_name,
                contract_path=contract_path,
            )
            destination = schema_migrate_contracts.default_scaffold_yaml_path(table_name)
            schema_migrate_contracts.write_scaffold_yaml(destination, payload=payload, force=force)
            generated_count += 1
            console.print(
                f"[green]Generated migration scaffold:[/green] {destination} (from {contract_path})"
            )
        except (
            FileNotFoundError,
            ValueError,
            json.JSONDecodeError,
            TypeError,
            FileExistsError,
            Exception,
        ) as exc:
            message = f"{contract_path}: {exc}"
            errors.append(message)
            console.print(f"[yellow]Skipping contract due to error:[/yellow] {message}")

    console.print(f"[green]Generated {generated_count} migration scaffolds.[/green]")
    if errors:
        console.print(
            f"[red]Encountered {len(errors)} errors while scaffolding recent contracts.[/red]"
        )
        for error in errors:
            console.print(f"- {error}")
        sys.exit(1)


def _build_scaffold_payload_from_contract(
    *, table_name: str, contract_path: Path
) -> dict[str, Any]:
    contract = schema_migrate_contracts.read_contract(contract_path)
    contract_table = contract.get("table_name")
    if contract_table != table_name:
        raise ValueError(
            f"Contract table mismatch: expected '{table_name}', found '{contract_table}'"
        )

    desired_payload = contract.get("normalized_schema")
    if not isinstance(desired_payload, dict):
        raise ValueError("Contract missing normalized_schema payload")

    fields_payload = desired_payload.get("fields", [])
    metadata_payload = desired_payload.get("metadata", {})
    if not isinstance(fields_payload, list) or not isinstance(metadata_payload, dict):
        raise ValueError("Invalid normalized_schema payload in contract")

    desired = NormalizedSchema(
        fields=[FieldSpec(**field_data) for field_data in fields_payload],
        metadata=metadata_payload,
    )
    migrator = _resolve_migrator()
    migration_plan = migrator.diff_schema(table_name=table_name, desired=desired)

    return schema_migrate_contracts.build_scaffold_payload(
        table_name=table_name,
        contract=contract,
        migration_plan=migration_plan,
        generated_at=datetime.now(UTC).isoformat(),
    )


def _find_native_schema(table_name: str, schema_class: str | None) -> Any:
    """Find the native quality schema for a table."""
    provider = _resolve_extractor()
    if provider is None:
        return None
    candidates = provider.discover_schemas()
    if schema_class:
        if schema_class in candidates:
            return candidates[schema_class]

        # Support module-qualified references (e.g. workflows.schemas.demo.RawSchema)
        short_name = schema_class.split(".")[-1]
        if short_name in candidates:
            return candidates[short_name]

        console.print(f"[red]Schema class not found: {schema_class}[/red]")
        return None

    return _discover_schema_for_table(table_name)


def _resolve_namespace_resolver() -> Any:
    """Resolve the installed namespace resolution capability."""
    discover_capabilities()
    resolution = resolve_capability("namespace_resolver")
    return resolution.provider if resolution is not None else None


def _render_plan(plan: Any) -> None:
    """Render a SchemaMigrationPlan as a rich table."""
    classification_colors = {
        "safe": "green",
        "warning": "yellow",
        "breaking": "red",
    }
    color = classification_colors.get(plan.classification, "white")

    console.print(f"\n[bold]Migration Plan: {plan.table_name}[/bold]")
    console.print(f"Classification: [{color}]{plan.classification}[/{color}]")
    console.print(f"Requires approval: {'Yes' if plan.requires_approval else 'No'}\n")

    table = Table()
    table.add_column("Field", style="cyan")
    table.add_column("Change", style="magenta")
    table.add_column("Old", style="dim")
    table.add_column("New", style="dim")
    table.add_column("Classification")

    for change in plan.changes:
        c_color = classification_colors.get(change.classification, "white")
        table.add_row(
            change.field_name,
            change.change_type,
            change.old_value or "",
            change.new_value or "",
            f"[{c_color}]{change.classification}[/{c_color}]",
        )

    console.print(table)
