"""Data migration CLI commands.

Subcommands validate and run migration specs (with dry-run override and
recorded execution history), list specs, report status, and run the dated
codemod that rewrites flow decorators.

Wired into the phlo CLI command tree by src/phlo/cli/main.py; runs migrations
through phlo.migrations.
"""

from __future__ import annotations

import json
import sys
from dataclasses import asdict
from difflib import unified_diff
from pathlib import Path

import click
from rich.console import Console
from rich.table import Table

from phlo.cli.authorization_wrappers import require_mutation_authorization
from phlo.codemods.decorators_2026_05 import migrate_decorators_2026_05_source
from phlo.migrations import (
    MigrationExecutionError,
    MigrationExecutor,
    MigrationSpecError,
    load_migration_spec,
    read_migration_history,
)

console = Console()

_CODEMOD_SKIP_DIRS = {
    ".git",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
    "__pycache__",
}


@click.group("migrate")
def migrate_group() -> None:
    """Data migration commands."""


@migrate_group.command("decorators-2026-05")
@click.argument("path", type=click.Path(path_type=Path, exists=True))
@click.option("--check", is_flag=True, help="Fail if decorators 2026-05 migrations are needed.")
@click.option("--write", is_flag=True, help="Rewrite files in place.")
@click.option("--diff", "show_diff", is_flag=True, help="Print unified diffs for pending changes.")
@require_mutation_authorization(
    "migrate.decorators_2026_05",
    when=lambda params: bool(params.get("write")),
)
def decorators_2026_05(path: Path, check: bool, write: bool, show_diff: bool) -> None:
    """Migrate May 2026 decorator APIs.

    Without --write this only reports pending changes. --check exits with
    status 1 when any file still needs migration, so it can gate CI.
    """
    if check and write:
        raise click.UsageError("Use either --check or --write, not both.")

    changed: list[tuple[Path, str, str]] = []
    for file_path in _iter_python_files(path):
        source = file_path.read_text(encoding="utf-8")
        try:
            migrated = migrate_decorators_2026_05_source(source)
        except RuntimeError as exc:
            raise click.ClickException(str(exc)) from exc
        if not migrated.changed:
            continue
        changed.append((file_path, source, migrated.code))
        if write:
            file_path.write_text(migrated.code, encoding="utf-8")

    if show_diff:
        for file_path, before, after in changed:
            click.echo(
                "".join(
                    unified_diff(
                        before.splitlines(keepends=True),
                        after.splitlines(keepends=True),
                        fromfile=f"{file_path} (before)",
                        tofile=f"{file_path} (after)",
                    )
                ),
                nl=False,
            )

    if write:
        console.print(
            f"[green]Updated {len(changed)} file{'s' if len(changed) != 1 else ''}.[/green]"
        )
        return

    if changed:
        console.print("[yellow]Decorators 2026-05 migration needed:[/yellow]")
        for file_path, _, _ in changed:
            click.echo(str(file_path))
        if check:
            sys.exit(1)
        return

    console.print("[green]No decorators 2026-05 migrations needed.[/green]")


def _iter_python_files(path: Path) -> list[Path]:
    if path.is_file():
        return [path] if path.suffix == ".py" else []

    files: list[Path] = []
    for candidate in sorted(path.rglob("*.py")):
        if any(part in _CODEMOD_SKIP_DIRS for part in candidate.parts):
            continue
        files.append(candidate)
    return files


@migrate_group.command("validate")
@click.argument("spec_file", type=click.Path(path_type=Path, dir_okay=False))
def validate(spec_file: Path) -> None:
    """Validate a migration spec without executing."""
    try:
        spec = load_migration_spec(spec_file)
    except MigrationSpecError as exc:
        console.print(f"[red]{exc}[/red]")
        sys.exit(1)

    executor = MigrationExecutor()
    errors = executor.validate(spec, dry_run_override=True)
    if errors:
        console.print("[red]Validation failed:[/red]")
        for error in errors:
            console.print(f"- {error}")
        sys.exit(1)

    console.print(f"[green]Migration spec is valid:[/green] {spec_file}")


@migrate_group.command("run")
@click.argument("spec_file", type=click.Path(path_type=Path, dir_okay=False))
@click.option("--dry-run", is_flag=True, help="Validate and read without writing")
@click.option("--format", "fmt", type=click.Choice(["table", "json"]), default="table")
@require_mutation_authorization("migrate.run", when=lambda params: not params.get("dry_run"))
def run(spec_file: Path, dry_run: bool, fmt: str) -> None:
    """Execute a migration spec."""
    try:
        spec = load_migration_spec(spec_file)
        result = MigrationExecutor().execute(
            spec,
            dry_run_override=True if dry_run else None,
        )
    except (MigrationSpecError, MigrationExecutionError) as exc:
        console.print(f"[red]{exc}[/red]")
        sys.exit(1)

    if fmt == "json":
        click.echo(json.dumps(asdict(result), indent=2, default=str))
        return

    console.print(f"[green]Migration {result.status}:[/green] {result.name}")
    console.print(f"Rows read: {result.rows_read}")
    console.print(f"Rows written: {result.rows_written}")
    console.print(f"Chunks processed: {result.chunks_processed}")
    console.print(f"Duration: {result.duration_seconds:.2f}s")


@migrate_group.command("list")
@click.option(
    "--directory",
    "directory",
    type=click.Path(path_type=Path, file_okay=False),
    default=None,
    help="Directory to scan (defaults: migrations/, workflows/migrations/)",
)
def list_specs(directory: Path | None) -> None:
    """List available migration spec files."""
    candidates = [directory] if directory else [Path("migrations"), Path("workflows/migrations")]
    files: list[Path] = []

    for root in candidates:
        if root is None or not root.exists():
            continue
        files.extend(sorted(root.glob("*.yaml")))
        files.extend(sorted(root.glob("*.yml")))

    deduped = sorted(set(files))
    if not deduped:
        console.print("[yellow]No migration specs found.[/yellow]")
        return

    for path in deduped:
        click.echo(str(path))


@migrate_group.command("status")
@click.option("--limit", default=10, help="Max history entries to show")
@click.option("--format", "fmt", type=click.Choice(["table", "json"]), default="table")
def status(limit: int, fmt: str) -> None:
    """Show recent migration history."""
    entries = read_migration_history(limit=limit)
    if fmt == "json":
        click.echo(json.dumps(entries, indent=2, default=str))
        return

    if not entries:
        console.print("[yellow]No migration history found.[/yellow]")
        return

    table = Table(title="Recent Data Migrations")
    table.add_column("Name", style="cyan")
    table.add_column("Status", style="green")
    table.add_column("Rows Read", justify="right")
    table.add_column("Rows Written", justify="right")
    table.add_column("Chunks", justify="right")
    table.add_column("Timestamp", style="dim")

    for entry in entries:
        raw_metadata = entry.get("metadata")
        metadata: dict[str, object]
        metadata = raw_metadata if isinstance(raw_metadata, dict) else {}
        table.add_row(
            str(entry.get("name", "")),
            str(entry.get("status", "")),
            str(entry.get("rows_read", 0)),
            str(entry.get("rows_written", 0)),
            str(entry.get("chunks_processed", 0)),
            str(metadata.get("timestamp", "")),
        )

    console.print(table)
