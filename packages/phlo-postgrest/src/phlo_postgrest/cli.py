"""PostgREST CLI commands for Phlo.

This module defines the Click-based CLI commands for managing PostgREST,
including view generation from dbt models and authentication infrastructure
setup.

Commands:
    postgrest: Main command group for PostgREST operations.
    generate-views: Generate API views from dbt models.
    setup-auth: Configure authentication infrastructure.

Example:
    $ phlo postgrest generate-views --apply --schema api
    $ phlo postgrest setup-auth --force

"""

from typing import Optional

import click

from phlo.cli.output import user_error
from phlo.logging import get_logger
from phlo_postgrest.hooks import reload_schema
from phlo_postgrest.setup import setup_postgrest
from phlo_postgrest.views import generate_views

logger = get_logger(__name__)


@click.group()
def postgrest():
    """PostgREST API management commands.

    This command group provides operations for managing PostgREST
    configuration, including view generation and authentication setup.

    Subcommands:
        generate-views: Create API views from dbt models.
        setup-auth: Configure JWT authentication infrastructure.

    Example:
        $ phlo postgrest --help
        $ phlo postgrest generate-views --output views.sql

    """
    pass


@postgrest.command(name="generate-views")
@click.option(
    "--output",
    type=click.Path(),
    help="Output file path (default: stdout)",
)
@click.option(
    "--apply",
    is_flag=True,
    help="Apply views directly to database",
)
@click.option(
    "--diff",
    is_flag=True,
    help="Show diff of changes only",
)
@click.option(
    "--models",
    type=str,
    help="Filter models by pattern (e.g., mrt_*)",
)
@click.option(
    "--schema",
    default="api",
    help="API schema name (default: api)",
)
def generate_postgrest_views(
    output: Optional[str],
    apply: bool,
    diff: bool,
    models: Optional[str],
    schema: str,
):
    """Generate PostgREST API views from dbt models.

    Parses dbt manifest.json to discover models and generates CREATE VIEW
    statements that expose them through PostgREST's REST API. Can output
    to file (--output), stdout (default), apply directly to the database
    (--apply), or show a diff of changes without applying (--diff). Models
    can be filtered by glob pattern (e.g., 'mrt_*', 'stg_*'); views are
    written to the target schema (default: 'api').

    Raises: click.ClickException when view generation or database application fails.

    Example:
        $ phlo postgrest generate-views
        $ phlo postgrest generate-views --apply --models mrt_*
        $ phlo postgrest generate-views --diff --output views.sql
    """
    logger.info(
        "postgrest_generate_views_started",
        output=output,
        apply=apply,
        diff=diff,
        model_filter=models,
        schema=schema,
    )
    try:
        result = generate_views(
            output=output,
            apply=apply,
            diff=diff,
            models=models,
            api_schema=schema,
            verbose=True,
        )

        if not apply and not output:
            click.echo(result)
        logger.info(
            "postgrest_generate_views_completed", applied=apply, output_written=bool(output)
        )

    except Exception as e:
        logger.exception("postgrest_generate_views_failed", error=str(e))
        raise user_error(
            "could not generate PostgREST views",
            details={"Schema": schema},
            run="phlo postgrest generate-views --help",
        ) from e


@postgrest.command(name="setup-auth")
@click.option("--host", help="PostgreSQL host")
@click.option("--port", type=int, help="PostgreSQL port")
@click.option("--database", help="PostgreSQL database name")
@click.option("--user", help="PostgreSQL user")
@click.option("--password", help="PostgreSQL password")
@click.option("--force", is_flag=True, help="Force re-setup even if already exists")
@click.option("-q", "--quiet", is_flag=True, help="Suppress output")
def setup_postgrest_cmd(host, port, database, user, password, force, quiet):
    """Set up PostgREST authentication infrastructure.

    Configures the PostgreSQL database with JWT authentication functions,
    database roles (anon, authenticated, analyst, admin), user management
    tables, and Row-Level Security policies required by PostgREST.

    This command is idempotent - safe to run multiple times. It will skip
    setup if infrastructure already exists unless --force is specified.
    Connection parameters come from the options above or their POSTGRES_HOST,
    POSTGRES_PORT, POSTGRES_DB, POSTGRES_USER, and POSTGRES_PASSWORD
    environment variables; pass --quiet to suppress progress output.

    Raises: click.ClickException when setup fails due to connection or permission errors.

    Example:
        $ phlo postgrest setup-auth
        $ phlo postgrest setup-auth --force --quiet
        $ phlo postgrest setup-auth --host db.example.com --port 5433
    """
    logger.info(
        "postgrest_setup_started",
        host=host,
        port=port,
        database=database,
        user=user,
        force=force,
        quiet=quiet,
    )
    try:
        setup_postgrest(
            host=host,
            port=port,
            database=database,
            user=user,
            password=password,
            force=force,
            verbose=not quiet,
        )
        logger.info("postgrest_setup_completed", force=force)
    except Exception as e:
        logger.exception("postgrest_setup_failed", error=str(e))
        raise user_error(
            "could not set up PostgREST authentication",
            run="phlo services status",
        ) from e


@postgrest.command(name="reload-schema")
def reload_postgrest_schema_cmd():
    """Reload PostgREST's schema cache after migrations or table creation."""
    logger.info("postgrest_reload_schema_started")
    try:
        reload_schema()
    except Exception as e:
        logger.exception("postgrest_reload_schema_failed", error=str(e))
        raise user_error(
            "could not reload PostgREST schema cache",
            run="phlo services status",
        ) from e
    click.echo("PostgREST schema cache reload requested.")
    logger.info("postgrest_reload_schema_completed")
