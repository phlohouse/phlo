"""Workflow management commands.

create scaffolds a workflow through the lazily resolved authoring provider and
prints next steps; check validates workflow and schema files, optionally
emitting JSON.
Imported by src/phlo/cli/main.py and phlo_api.api.authoring (the workflow authoring API).
Resolves workflow validators through phlo.capabilities.discovery.
"""

from __future__ import annotations

import io
from contextlib import redirect_stdout
from pathlib import Path

import click

from phlo.capabilities import WorkflowValidator, resolve_capability
from phlo.capabilities.discovery import discover_capabilities
from phlo.cli.output import json_envelope, user_error
from phlo.logging import get_logger
from phlo.workflow_authoring import WorkflowCreateResult, create_workflow_with_provider

logger = get_logger(__name__)


@click.group(name="workflow")
def workflow_group() -> None:
    """Manage workflows."""


def _print_next_steps(steps: list[str]) -> None:
    """Print the next commands for a generated ingestion workflow."""
    click.echo("\nNext steps:")
    for index, step in enumerate(steps, start=1):
        click.echo(f"  {index}. {step}")


def _infer_schema_path(workflow_path: Path) -> Path | None:
    """Infer a domain schema path from a workflow path."""
    parts = workflow_path.parts
    if "workflows" not in parts or "ingestion" not in parts:
        return None
    workflows_index = parts.index("workflows")
    ingestion_index = parts.index("ingestion")
    if ingestion_index + 1 >= len(parts):
        return None
    domain = parts[ingestion_index + 1]
    root = Path(*parts[:workflows_index]) if workflows_index > 0 else Path()
    return root / "workflows" / "schemas" / f"{domain}.py"


def _asset_key_from_workflow_path(workflow_path: Path) -> str:
    """Infer a provider-neutral asset key hint from a workflow file path."""
    return workflow_path.stem


def _validate_workflow_file(path: str) -> None:
    """Validate a workflow file through the resolved validation capability."""
    validator = _resolve_workflow_validator()
    validator.validate_workflow_file(Path(path))


def _resolve_workflow_validator() -> WorkflowValidator:
    """Resolve the installed workflow validation capability."""
    discover_capabilities()
    resolution = resolve_capability("workflow_validation")
    if resolution is None:
        raise user_error(
            "workflow_validation capability is unavailable",
            details=["Install a provider that supplies workflow validation."],
            run='uv pip install "phlo-pandera"',
        )
    return resolution.provider


def _validate_schema_file(path: str) -> None:
    """Validate a schema file through the resolved validation capability."""
    validator = _resolve_workflow_validator()
    try:
        validator.validate_schema_file(Path(path))
    except click.ClickException:
        raise
    except Exception as exc:
        raise click.ClickException(f"Schema validation failed for {path}: {exc}") from exc


def _create_workflow(
    *,
    workflow_type: str = "ingestion",
    domain: str,
    table: str,
    unique_key: str,
    cron: str,
    api_base_url: str | None = None,
    fields: list[str] | None = None,
    provider: str | None = None,
    source_kind: str | None = None,
) -> WorkflowCreateResult:
    """Create a workflow scaffold through the active workflow authoring provider."""
    return create_workflow_with_provider(
        project_root=Path.cwd(),
        workflow_type=workflow_type,
        domain=domain,
        table=table,
        unique_key=unique_key,
        cron=cron,
        api_base_url=api_base_url or None,
        fields=fields or [],
        provider=provider,
        source_kind=source_kind,
    )


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
@click.option("--provider", help="Workflow authoring provider capability to use.")
@click.option(
    "--source-kind",
    type=click.Choice(["rest-api", "partitioned-sql"]),
    default="rest-api",
    show_default=True,
    help="Source template style for providers that support multiple ingestion patterns.",
)
@click.option("--json", "output_json", is_flag=True, help="Emit machine-readable JSON.")
def create_workflow_cmd(
    workflow_type: str,
    domain: str,
    table: str,
    unique_key: str,
    cron: str,
    api_base_url: str,
    fields: tuple[str, ...],
    provider: str | None,
    source_kind: str,
    output_json: bool,
) -> None:
    """Create a workflow scaffold."""
    logger.info(
        "workflow_create_started",
        workflow_type=workflow_type,
        domain=domain,
        table=table,
        field_count=len(fields),
    )
    if not output_json:
        click.echo(f"\nCreating {workflow_type} workflow for {domain}.{table}...\n")

    try:
        if workflow_type == "ingestion":
            result = _create_workflow(
                workflow_type=workflow_type,
                domain=domain,
                table=table,
                unique_key=unique_key,
                cron=cron,
                api_base_url=api_base_url or None,
                fields=list(fields),
                provider=provider,
                source_kind=source_kind,
            )

            if output_json:
                click.echo(json_envelope(data=result.__dict__))
            else:
                click.echo("Created files:\n")
                for file_path in result.files:
                    click.echo(f"  - {file_path}")

                _print_next_steps(result.next_steps)
            logger.info(
                "workflow_create_succeeded",
                workflow_type=workflow_type,
                domain=domain,
                table=table,
                file_count=len(result.files),
            )
    except Exception as exc:
        logger.exception(
            "workflow_create_failed",
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


@workflow_group.command("check")
@click.argument("workflow_file", type=click.Path(dir_okay=False))
@click.option("--json", "output_json", is_flag=True, help="Emit machine-readable JSON.")
def check_workflow_cmd(workflow_file: str, output_json: bool) -> None:
    """Validate a workflow and its inferred schema before materialization."""
    workflow_path = Path(workflow_file)
    if not workflow_path.exists():
        raise user_error(
            "workflow file not found",
            missing=str(workflow_path),
            run="phlo workflow create",
        )

    schema_path = _infer_schema_path(workflow_path)

    if output_json:
        with redirect_stdout(io.StringIO()):
            _validate_workflow_file(str(workflow_path))
    else:
        _validate_workflow_file(str(workflow_path))

    if not output_json:
        click.echo(f"Workflow valid: {workflow_path}")

    schema_validated = False
    if schema_path and schema_path.exists():
        try:
            if output_json:
                with redirect_stdout(io.StringIO()):
                    _validate_schema_file(str(schema_path))
            else:
                _validate_schema_file(str(schema_path))
        except click.ClickException:
            raise
        except Exception as exc:
            raise click.ClickException(
                f"Schema validation failed for {schema_path}: {exc}"
            ) from exc
        schema_validated = True
        if not output_json:
            click.echo(f"Schema valid: {schema_path}")
    elif schema_path:
        raise click.ClickException(f"Inferred schema file not found: {schema_path}")

    asset_key = _asset_key_from_workflow_path(workflow_path)
    payload = {
        "valid": True,
        "workflow_path": str(workflow_path),
        "schema_path": str(schema_path) if schema_path else None,
        "schema_validated": schema_validated,
        "asset_key": asset_key,
        "next_command": f"phlo materialize {asset_key}",
    }
    if output_json:
        click.echo(json_envelope(data=payload))
    else:
        click.echo(f"Next: phlo materialize {asset_key}")
