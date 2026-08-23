"""
Schema management CLI commands.

Provides commands to:
- List and inspect Pandera schemas
- Show schema details and constraints
- Diff schema versions
- Validate schema syntax
"""

from __future__ import annotations

import ast
import inspect
import json
import subprocess
import sys
from pathlib import Path
from typing import Optional

import click
from rich.console import Console
from rich.syntax import Syntax
from rich.table import Table

from phlo.cli.output import user_error
from phlo.logging import get_logger
from phlo_pandera.cli_schema_codegen import generate as codegen_generate
from phlo_pandera.cli_schema_utils import classify_schema_change, discover_pandera_schemas

console = Console()
logger = get_logger(__name__)


@click.group()
def schema():
    """Manage Pandera schemas and schema validation."""
    pass


@schema.command()
@click.option(
    "--domain",
    help="Filter by domain",
    default=None,
)
@click.option(
    "--format",
    type=click.Choice(["table", "json"]),
    default="table",
    help="Output format",
)
def list(domain: Optional[str], format: str):
    """List all available Pandera schemas with name, field count, and file path.

    Optionally filters by domain; prints results as a table or JSON.

    Example:
        ```bash
        phlo schema list                 # List all schemas
        phlo schema list --domain sales
        phlo schema list --format json
        ```

    """
    try:
        schemas = discover_pandera_schemas()

        if not schemas:
            if format == "json":
                click.echo("{}")
                return
            console.print("[yellow]No schemas found[/yellow]")
            return

        # Filter by domain if specified
        if domain:
            schemas = {
                name: schema for name, schema in schemas.items() if domain.lower() in name.lower()
            }

        if not schemas:
            if format == "json":
                click.echo("{}")
                return
            console.print(f"[yellow]No schemas found for domain: {domain}[/yellow]")
            return

        if format == "json":
            output = {
                name: {
                    "fields": len(schema.__annotations__),
                    "location": str(Path(schema.__module__.replace(".", "/")).with_suffix(".py")),
                }
                for name, schema in schemas.items()
            }
            click.echo(json.dumps(output, indent=2))
        else:
            table = Table(title="Available Schemas")
            table.add_column("Name", style="cyan")
            table.add_column("Fields", justify="right")
            table.add_column("Module", style="magenta")

            for name in sorted(schemas.keys()):
                schema_cls = schemas[name]
                field_count = len(schema_cls.__annotations__)
                module = schema_cls.__module__
                table.add_row(name, str(field_count), module)

            console.print(table)

    except Exception as e:
        logger.exception(
            "schema_list_failed",
            domain=domain,
            output_format=format,
            error=str(e),
        )
        raise user_error("could not list schemas", run="phlo schema list --help") from e


@schema.command()
@click.argument("schema_name")
@click.option(
    "--iceberg",
    is_flag=True,
    help="Show Iceberg schema equivalent",
)
def show(schema_name: str, iceberg: bool):
    """Show a schema's fields, types, constraints, and descriptions.

    With --iceberg, prints the equivalent Iceberg schema instead.

    Example:
        ```bash
        phlo schema show OrderSchema
        phlo schema show OrderSchema --iceberg
        ```

    """
    try:
        schemas = discover_pandera_schemas()

        if schema_name not in schemas:
            console.print(
                f"[red]Schema not found: {schema_name}[/red]\n"
                f"Available schemas: {', '.join(sorted(schemas.keys()))}"
            )
            sys.exit(1)

        schema_cls = schemas[schema_name]

        # Show basic info
        console.print(f"\n[bold blue]{schema_name}[/bold blue]")
        console.print(f"Module: {schema_cls.__module__}")
        console.print(f"Fields: {len(schema_cls.__annotations__)}\n")

        # Show fields
        table = Table(title="Fields")
        table.add_column("Name", style="cyan")
        table.add_column("Type", style="green")
        table.add_column("Required", justify="center")
        table.add_column("Description", style="dim")

        for field_name, field_type in schema_cls.__annotations__.items():
            description = ""
            required = "✓"

            if hasattr(schema_cls, "__annotations__"):
                # Check if Optional
                type_str = str(field_type)
                if "Optional" in type_str or "None" in type_str:
                    required = ""

            table.add_row(field_name, str(field_type), required, description)

        console.print(table)

        if iceberg:
            console.print("\n[bold]Iceberg Schema Equivalent:[/bold]")
            console.print("[dim]# Convert with: phlo schema show --iceberg[/dim]\n")

            # Show example conversion
            iceberg_equiv = _pandera_to_iceberg_example(schema_cls)
            syntax = Syntax(iceberg_equiv, "yaml", theme="monokai", line_numbers=True)
            console.print(syntax)

    except Exception as e:
        logger.exception(
            "schema_show_failed",
            schema_name=schema_name,
            iceberg=iceberg,
            error=str(e),
        )
        raise user_error(
            "could not show schema",
            details={"Schema": schema_name},
            run="phlo schema list",
        ) from e


@schema.command()
@click.argument("schema_name")
@click.option(
    "--old",
    default="HEAD~1",
    help="Old version (git ref or file path)",
)
@click.option(
    "--format",
    type=click.Choice(["text", "json"]),
    default="text",
)
def diff(schema_name: str, old: str, format: str):
    """Compare a schema version against an older one.

    Detects added/removed/modified fields, classifies changes as safe or
    breaking, and prints the diff as text or JSON. The old version may be a
    git ref (default HEAD~1) or a file path.

    Example:
        ```bash
        phlo schema diff OrderSchema --old HEAD~1
        phlo schema diff OrderSchema --old main
        phlo schema diff OrderSchema --old workflows/schemas/orders_previous.py
        ```

    """
    try:
        schemas = discover_pandera_schemas()

        if schema_name not in schemas:
            console.print(f"[red]Schema not found: {schema_name}[/red]")
            sys.exit(1)

        schema_cls = schemas[schema_name]
        new_schema = _load_current_schema(schema_cls, schema_name)
        old_schema = _load_old_schema(schema_cls, schema_name, old)
        classification, details = classify_schema_change(old_schema, new_schema)

        if format == "json":
            output = {
                "classification": classification,
                "details": details,
                "old_schema": old_schema,
                "new_schema": new_schema,
            }
            click.echo(json.dumps(output, indent=2))
        else:
            console.print(f"\n[bold blue]Schema Diff: {schema_name}[/bold blue]")
            console.print(f"Old schema fields: {len(old_schema)}")
            console.print(f"New schema fields: {len(new_schema)}")

            table = Table(title=f"Classification: {classification}")
            table.add_column("Change Type")
            table.add_column("Details")

            for detail in details:
                table.add_row("Field Change", detail)

            console.print(table)

    except Exception as e:
        logger.exception(
            "schema_diff_failed",
            schema_name=schema_name,
            old_ref=old,
            output_format=format,
            error=str(e),
        )
        raise user_error(
            "could not diff schema",
            details={
                "Schema": schema_name,
                "Old": old,
            },
            run="phlo schema diff --help",
        ) from e


def _load_current_schema(schema_cls: type, schema_name: str) -> dict[str, str]:
    """Load the current schema annotations from source when possible."""
    source_path = getattr(schema_cls, "__phlo_schema_source_path__", None)
    if source_path is None:
        source_path = inspect.getsourcefile(schema_cls)
    if source_path is not None:
        current_path = Path(source_path)
        if current_path.exists():
            return _extract_schema_annotations(
                current_path.read_text(),
                schema_name,
                str(current_path),
            )

    return {name: str(type_) for name, type_ in schema_cls.__annotations__.items()}


@schema.command()
@click.argument("schema_path")
def validate(schema_path: str):
    """Validate a schema file's syntax and common integration issues.

    Prints validation results; exits with code 1 on failure.

    Example:
        ```bash
        phlo schema validate workflows/schemas/orders.py
        phlo schema validate workflows/schemas/custom.py
        ```

    """
    try:
        path = Path(schema_path)

        if not path.exists():
            console.print(f"[red]File not found: {schema_path}[/red]")
            sys.exit(1)

        # Read and validate schema file
        with open(path) as f:
            content = f.read()

        # Check for basic requirements
        checks = {
            "Has imports": "import" in content.lower(),
            "Has class definition": "class " in content,
            "Has docstring": '"""' in content or "'''" in content,
            "Valid Python": True,
        }

        # Try to compile
        try:
            compile(content, path, "exec")
        except SyntaxError as e:
            logger.warning(
                "schema_validate_syntax_error",
                schema_path=str(path),
                line=e.lineno,
                offset=e.offset,
                error=str(e),
            )
            checks["Valid Python"] = False
            console.print("[red]Syntax error[/red]")

        # Show results
        table = Table(title=f"Schema Validation: {schema_path}")
        table.add_column("Check", style="cyan")
        table.add_column("Status", justify="center")

        for check_name, passed in checks.items():
            status = "[green]✓[/green]" if passed else "[red]✗[/red]"
            table.add_row(check_name, status)

        console.print(table)

        # Summary
        passed_count = sum(1 for v in checks.values() if v)
        total_count = len(checks)

        if passed_count == total_count:
            console.print(f"\n[green]All checks passed ({passed_count}/{total_count})[/green]")
        else:
            console.print(f"\n[yellow]Some checks failed ({passed_count}/{total_count})[/yellow]")
            sys.exit(1)

    except Exception as e:
        logger.exception(
            "schema_validate_failed",
            schema_path=schema_path,
            error=str(e),
        )
        raise user_error(
            "could not validate schema",
            details={"File": schema_path},
            run="phlo schema validate --help",
        ) from e


def _load_old_schema(schema_cls: type, schema_name: str, old_ref: str) -> dict[str, str]:
    """Load a previous schema definition from a file path or git ref.

    Returns a mapping of column names to type annotations.
    Raises: ValueError when the schema cannot be loaded from the reference.

    """
    source_path = getattr(schema_cls, "__phlo_schema_source_path__", None)
    if source_path is None:
        source_path = inspect.getsourcefile(schema_cls)
    if source_path is None:
        raise ValueError(f"Could not determine source file for schema '{schema_name}'")

    current_path = Path(source_path).resolve()
    old_path = Path(old_ref)
    if old_path.exists():
        source = old_path.read_text()
        return _extract_schema_annotations(source, schema_name, str(old_path))

    repo_root = _get_repo_root()
    relative_path = current_path.relative_to(repo_root)

    try:
        result = subprocess.run(
            ["git", "show", f"{old_ref}:{relative_path.as_posix()}"],
            check=True,
            capture_output=True,
            text=True,
        )
    except subprocess.CalledProcessError as exc:
        stderr = exc.stderr.strip()
        raise ValueError(
            f"Could not load schema '{schema_name}' from git ref '{old_ref}'"
            + (f": {stderr}" if stderr else "")
        ) from exc

    return _extract_schema_annotations(result.stdout, schema_name, f"{old_ref}:{relative_path}")


def _extract_schema_annotations(source: str, schema_name: str, source_label: str) -> dict[str, str]:
    """Extract annotated class fields from schema source without importing it.

    Returns a mapping of field names to type annotation strings.
    Raises: ValueError when the schema class is missing or has no annotated fields.

    """
    module = ast.parse(source, filename=source_label)
    for node in module.body:
        if isinstance(node, ast.ClassDef) and node.name == schema_name:
            annotations: dict[str, str] = {}
            for statement in node.body:
                if isinstance(statement, ast.AnnAssign) and isinstance(statement.target, ast.Name):
                    annotations[statement.target.id] = ast.unparse(statement.annotation)
            if annotations:
                return annotations
            raise ValueError(f"Schema '{schema_name}' has no annotated fields in {source_label}")
    raise ValueError(f"Schema '{schema_name}' not found in {source_label}")


def _get_repo_root() -> Path:
    """Resolve the git repository root for schema diff lookups."""
    result = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        check=True,
        capture_output=True,
        text=True,
    )
    return Path(result.stdout.strip()).resolve()


def _pandera_to_iceberg_example(schema_cls) -> str:
    """Generate a YAML-formatted example Iceberg schema for a Pandera model."""
    lines = [
        "# Iceberg Schema Equivalent",
        "schema:",
    ]

    for field_name, field_type in schema_cls.__annotations__.items():
        type_str = str(field_type)
        # Simple mapping
        iceberg_type = _map_python_to_iceberg_type(type_str)
        lines.append(f"  {field_name}:")
        lines.append(f"    type: {iceberg_type}")
        lines.append("    required: true")

    return "\n".join(lines)


def _map_python_to_iceberg_type(python_type: str) -> str:
    """Map a Python type annotation string to an Iceberg type name.

    Falls back to "string" for unmapped types.

    """
    type_lower = python_type.lower()

    mapping = {
        "int": "int",
        "float": "double",
        "str": "string",
        "bool": "boolean",
        "datetime": "timestamp",
        "date": "date",
        "decimal": "decimal",
    }

    for py_type, iceberg_type in mapping.items():
        if py_type in type_lower:
            return iceberg_type

    return "string"  # Default


schema.add_command(codegen_generate)
