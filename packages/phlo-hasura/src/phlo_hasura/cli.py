"""Hasura CLI commands for Phlo.

This module provides Click CLI commands for managing Hasura GraphQL metadata,
including table tracking, relationship setup, permission management, and
metadata export/import operations.

Commands:
    track: Auto-discover and track tables in Hasura.
    relationships: Auto-create relationships from foreign keys.
    permissions: Set up default permissions for tracked tables.
    auto_setup: Complete auto-configuration (tables, relationships, permissions).
    export: Export current Hasura metadata to file.
    apply: Apply Hasura metadata from file.
    status: Show Hasura tracking status.
    sync-permissions: Sync permissions from config file.

Example:
    $ phlo hasura track --schema api --verbose
    $ phlo hasura auto-setup --schema marts
    $ phlo hasura export --output metadata.json

"""

import click
from phlo.logging import get_logger

from phlo.cli.output import user_error
from phlo_hasura.client import HasuraClient
from phlo_hasura.permissions import HasuraPermissionManager
from phlo_hasura.sync import HasuraMetadataSync
from phlo_hasura.track import HasuraTableTracker, auto_track

logger = get_logger(__name__)


def _log_error_and_raise(exception: Exception, log_context: dict, error_msg: str) -> None:
    """Log the exception with context, then raise a ClickException carrying
    error_msg for display to the user.
    """
    logger.exception("hasura_command_failed", error=str(exception), **log_context)
    raise user_error(
        error_msg,
        details=["Check that Hasura and Postgres services are running."],
        run="phlo services status",
    ) from exception


@click.group()
def hasura() -> None:
    """Hasura GraphQL metadata management CLI.

    Provides commands for managing Hasura metadata including table tracking,
    relationship configuration, permission setup, and metadata export/import.

    Example:
        $ phlo hasura --help
        $ phlo hasura track --schema api
        $ phlo hasura export --output metadata.json

    """
    pass


@hasura.command()
@click.option(
    "--schema",
    default="api",
    help="Schema to track tables from (default: api)",
)
@click.option(
    "--exclude",
    multiple=True,
    help="Tables to exclude from tracking",
)
@click.option(
    "-v",
    "--verbose",
    is_flag=True,
    help="Verbose output",
)
def track(schema: str, exclude: tuple, verbose: bool) -> None:
    """Auto-discover and track tables in Hasura, optionally excluding specific
    tables, and print a summary of tracked tables.

    Example:
        $ phlo hasura track --schema api
        $ phlo hasura track --schema marts --exclude staging_table --verbose

    """
    try:
        exclude_list = list(exclude) if exclude else None

        tracker = HasuraTableTracker()
        results = tracker.track_tables(
            schema,
            exclude=exclude_list,
            verbose=verbose,
        )

        tracked = sum(1 for v in results.values() if v)
        total = len(results)

        if verbose:
            click.echo()
        click.echo(f"Tracked {tracked}/{total} tables")

    except Exception as e:
        _log_error_and_raise(
            e,
            {"schema": schema, "exclude_count": len(exclude), "verbose": verbose},
            "could not track Hasura tables",
        )


@hasura.command()
@click.option(
    "--schema",
    default="api",
    help="Schema to set up relationships for (default: api)",
)
@click.option(
    "-v",
    "--verbose",
    is_flag=True,
    help="Verbose output",
)
def relationships(schema: str, verbose: bool) -> None:
    """Analyze foreign key constraints in the schema and create object
    relationships (many-to-one) in Hasura metadata.

    Example:
        $ phlo hasura relationships --schema api
        $ phlo hasura relationships --schema marts --verbose

    """
    try:
        tracker = HasuraTableTracker()
        results = tracker.setup_relationships(schema, verbose=verbose)

        successful = sum(1 for v in results.values() if v)
        total = len(results)

        if verbose:
            click.echo()
        click.echo(f"Created {successful}/{total} relationships")

    except Exception as e:
        _log_error_and_raise(
            e,
            {"schema": schema, "verbose": verbose},
            "could not create Hasura relationships",
        )


@hasura.command()
@click.option(
    "--schema",
    default="api",
    help="Schema to set up permissions for (default: api)",
)
@click.option(
    "-v",
    "--verbose",
    is_flag=True,
    help="Verbose output",
)
def permissions(schema: str, verbose: bool) -> None:
    """Create default SELECT permissions for standard roles (anon, analyst,
    admin) on all tracked tables in the schema.

    Example:
        $ phlo hasura permissions --schema api
        $ phlo hasura permissions --schema marts --verbose

    """
    try:
        tracker = HasuraTableTracker()
        results = tracker.setup_default_permissions(schema, verbose=verbose)

        successful = sum(1 for v in results.values() if v)
        total = len(results)

        if verbose:
            click.echo()
        click.echo(f"Created {successful}/{total} permissions")

    except Exception as e:
        _log_error_and_raise(
            e,
            {"schema": schema, "verbose": verbose},
            "could not create Hasura permissions",
        )


@hasura.command()
@click.option(
    "--schema",
    default="api",
    help="Schema to auto-track (default: api)",
)
@click.option(
    "-v",
    "--verbose",
    is_flag=True,
    help="Verbose output",
)
def auto_setup(schema: str, verbose: bool) -> None:
    """Run track, relationships, and permissions in sequence — one-command
    setup for a new schema.

    Example:
        $ phlo hasura auto-setup --schema api
        $ phlo hasura auto-setup --schema marts --verbose

    """
    try:
        auto_track(schema, verbose=verbose)
    except Exception as e:
        _log_error_and_raise(
            e,
            {"schema": schema, "verbose": verbose},
            "could not auto-configure Hasura",
        )


@hasura.command()
@click.option(
    "--output",
    type=click.Path(),
    required=True,
    help="Output file path for metadata",
)
def export(output: str) -> None:
    """Export the complete Hasura metadata (tracked tables, relationships,
    permissions) to a JSON file.

    Example:
        $ phlo hasura export --output hasura_metadata.json

    """
    try:
        syncer = HasuraMetadataSync()
        syncer.export_metadata(output)
        click.echo(f"Metadata exported to {output}")
    except Exception as e:
        _log_error_and_raise(e, {"output_path": output}, "could not export Hasura metadata")


@hasura.command(name="apply")
@click.option(
    "--input",
    type=click.Path(exists=True),
    required=True,
    help="Input metadata file",
)
def apply_meta(input: str) -> None:
    """Apply Hasura metadata from a previously exported JSON file, replacing
    the current metadata.

    Example:
        $ phlo hasura apply --input hasura_metadata.json

    """
    try:
        syncer = HasuraMetadataSync()
        syncer.import_metadata(input)
        click.echo(f"Metadata applied from {input}")
    except Exception as e:
        _log_error_and_raise(e, {"input_path": input}, "could not apply Hasura metadata")


@hasura.command()
def status() -> None:
    """Show a summary of all tracked tables organized by schema, reflecting
    the current Hasura metadata.

    Example:
        $ phlo hasura status

    """
    try:
        client = HasuraClient()
        tracked = client.get_tracked_tables()

        click.echo("Tracked tables by schema:")
        click.echo()

        for schema in sorted(tracked.keys()):
            tables = tracked[schema]
            click.echo(f"  {schema}: {len(tables)} tables")
            for table in sorted(tables):
                click.echo(f"    - {table}")

    except Exception as e:
        _log_error_and_raise(e, {}, "could not read Hasura status")


@hasura.command(name="sync-permissions")
@click.option(
    "--config",
    type=click.Path(exists=True),
    required=True,
    help="Permission config file (JSON/YAML)",
)
def sync_permissions(config: str) -> None:
    """Apply permission configurations from a YAML or JSON config file that
    defines tables, roles, and their SELECT/INSERT/UPDATE/DELETE grants.

    Example:
        $ phlo hasura sync-permissions --config permissions.yaml

    """
    try:
        manager = HasuraPermissionManager()
        config_dict = manager.load_config(config)
        manager.sync_permissions(config_dict, verbose=True)
        click.echo("Permissions synced")
    except Exception as e:
        _log_error_and_raise(
            e,
            {"config_path": config},
            "could not sync Hasura permissions",
        )
