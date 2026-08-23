"""Workflow management commands.

This module provides CLI commands for creating and managing DLT-based
workflows. It implements the ``phlo workflow`` command group with
subcommands for scaffolding new ingestion pipelines.

Command Groups:
    - ``workflow``: Main workflow management group
    - ``workflow create``: Create new ingestion workflow scaffold

Command Options:
    --type: Workflow type (currently only "ingestion")
    --domain: Domain name (e.g., weather, stripe)
    --table: Table name for the ingestion
    --unique-key: Field name for deduplication
    --cron: Cron schedule expression
    --api-base-url: REST API base URL (optional)
    --field: Additional schema fields (repeatable)

Generated Files:
    For each workflow, creates three files:
    1. ``workflows/schemas/{domain}.py``: Pandera schema definition
    2. ``workflows/ingestion/{domain}/{table}.py``: Ingestion asset
    3. ``tests/test_{domain}_{table}.py``: Unit tests

See Also:
    - :mod:`phlo_dlt.scaffold`: Scaffolding implementation
    - :mod:`phlo_dlt.cli_plugin`: Plugin that exposes these commands
    - Click documentation: https://click.palletsprojects.com/

Example:
    ```bash
    # Create a new ingestion workflow
    phlo workflow create --domain weather --table observations --unique-key id

    # With additional fields
    phlo workflow create \
        --domain weather \
        --table observations \
        --unique-key station_id \
        --field temperature:float \
        --field humidity:float \
        --field recorded_at:datetime
    ```

"""

from __future__ import annotations

import click

from phlo.cli.output import user_error
from phlo.logging import get_logger

logger = get_logger(__name__)


@click.group(name="workflow")
def workflow_group() -> None:
    """Manage workflows.

    Command group for workflow operations including creation,
    listing, and management of ingestion and transformation workflows.

    Subcommands:
        create: Create a new workflow scaffold

    Example:
        ```bash
        phlo workflow --help
        phlo workflow create --help
        ```

    """


def _print_ingestion_next_steps(files: list[str], *, table: str) -> None:
    """Print the next commands for a generated ingestion workflow."""
    schema_file = files[0]
    workflow_file = files[1]
    test_file = files[2] if len(files) > 2 else None

    click.echo("\nNext steps:")
    click.echo(f"  1. Review schema: {schema_file}")
    click.echo(f"  2. Review workflow: {workflow_file}")
    if test_file:
        click.echo(f"  3. Run generated tests: uv run pytest {test_file} -q")
        click.echo("  4. Restart Dagster: phlo services restart --service dagster")
        click.echo(f"  5. Materialize: phlo materialize dlt_{table} --partition YYYY-MM-DD")
        click.echo("  6. Inspect status: phlo status")
    else:
        click.echo("  3. Restart Dagster: phlo services restart --service dagster")
        click.echo(f"  4. Materialize: phlo materialize dlt_{table} --partition YYYY-MM-DD")
        click.echo("  5. Inspect status: phlo status")


@workflow_group.command("create")
@click.option(
    "--type",
    "workflow_type",
    type=click.Choice(["ingestion"]),
    default="ingestion",
    show_default=True,
    help="Type of workflow to create (ingestion only)",
)
@click.option("--domain", prompt="Domain name", help="Domain name (e.g., weather, stripe, github)")
@click.option("--table", prompt="Table name", help="Table name for ingestion")
@click.option(
    "--unique-key",
    prompt="Unique key field",
    help="Field name for deduplication (e.g., id, _id)",
)
@click.option(
    "--cron",
    default="0 */1 * * *",
    show_default=True,
    help="Cron schedule expression",
)
@click.option(
    "--api-base-url",
    default="",
    help="REST API base URL",
)
@click.option(
    "--field",
    "fields",
    multiple=True,
    help="Additional schema field (name:type, name:type?, name:type!)",
)
def create_workflow_cmd(
    workflow_type: str,
    domain: str,
    table: str,
    unique_key: str,
    cron: str,
    api_base_url: str,
    fields: tuple[str, ...],
) -> None:
    """Create a workflow scaffold.

    Generates the initial file structure for a new DLT ingestion workflow:
    - Pandera schema in workflows/schemas/{domain}.py
    - Ingestion asset in workflows/ingestion/{domain}/{table}.py
    - Unit tests in tests/test_{domain}_{table}.py

    Example:
        ```bash
        # Interactive mode (prompts for all values)
        phlo workflow create

        # Non-interactive with all options
        phlo workflow create \
            --type ingestion \
            --domain weather \
            --table observations \
            --unique-key station_id \
            --cron "0 */6 * * *" \
            --api-base-url "https://api.weather.com/v1" \
            --field temperature:float \
            --field humidity:float

        # Nullable and required fields
        phlo workflow create \
            --domain users \
            --table profiles \
            --unique-key user_id \
            --field middle_name:str? \
            --field email:str!
        ```
    """
    from phlo_dlt.scaffold import create_ingestion_workflow

    logger.info(
        "dlt_workflow_create_started",
        workflow_type=workflow_type,
        domain=domain,
        table=table,
        field_count=len(fields),
    )
    click.echo(f"\nCreating {workflow_type} workflow for {domain}.{table}...\n")

    try:
        if workflow_type == "ingestion":
            files = create_ingestion_workflow(
                domain=domain,
                table_name=table,
                unique_key=unique_key,
                cron=cron,
                api_base_url=api_base_url or None,
                fields=list(fields),
            )

            click.echo("Created files:\n")
            for file_path in files:
                click.echo(f"  - {file_path}")

            _print_ingestion_next_steps(files, table=table)
            logger.info(
                "dlt_workflow_create_succeeded",
                workflow_type=workflow_type,
                domain=domain,
                table=table,
                file_count=len(files),
            )
    except Exception as exc:
        logger.exception(
            "dlt_workflow_create_failed",
            workflow_type=workflow_type,
            domain=domain,
            table=table,
            error=str(exc),
        )
        raise user_error(
            "could not create workflow",
            details={
                "Workflow": workflow_type,
                "Dataset": f"{domain}.{table}",
            },
            run="phlo workflow create --help",
        ) from exc
