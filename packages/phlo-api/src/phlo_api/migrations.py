"""Explicit, transactional environment-constraint upgrade for installed consumers.

No application startup calls this module. Back up the database, stop API writers,
then run ``python -m phlo_api.migrations --apply --backup-confirmed`` using the
installation's PHLO_RUN_EVIDENCE_DB_URL. ``--check`` is read-only.
"""

import json
from pathlib import Path
from typing import Any

import click
import psycopg2

from phlo.cli.authorization_wrappers import require_mutation_authorization
from phlo.config.process import get_process_settings

_TABLES = (
    "incident",
    "incident_event",
    "incident_signal",
    "incident_command",
    "incident_subscription",
    "incident_follow_up",
    "incident_schema_decision",
    "asset_incident_policy",
    "asset_query_usage",
    "query_execution",
    "incident_effect",
)
_PATTERN = "^[a-z][a-z0-9_-]{0,62}$"


def configurable_environments_ready(connection: Any) -> bool:
    """Check every provider-owned constraint, not only table presence or liveness."""
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT r.relname, pg_get_constraintdef(c.oid), c.convalidated "
            "FROM pg_constraint c JOIN pg_class r ON r.oid = c.conrelid "
            "JOIN pg_namespace n ON n.oid = r.relnamespace "
            "WHERE n.nspname = 'phlo' AND c.contype = 'c' "
            "AND c.conname = r.relname || '_env_check' AND r.relname = ANY(%s)",
            (list(_TABLES),),
        )
        checks = {
            name: (definition, validated) for name, definition, validated in cursor.fetchall()
        }
    return set(checks) == set(_TABLES) and all(
        validated and _PATTERN in definition and " ~ " in definition
        for definition, validated in checks.values()
    )


def apply_configurable_environments(connection: Any) -> None:
    """Apply 004 inside the caller's transaction; never commits partial upgrades."""
    with connection.cursor() as cursor:
        cursor.execute("SET LOCAL lock_timeout = '5s'")
        cursor.execute("SET LOCAL statement_timeout = '60s'")
        cursor.execute("SELECT pg_advisory_xact_lock(1126004)")
        cursor.execute(
            (Path(__file__).parent / "sql" / "004_configurable_environments.sql").read_text()
        )
    if not configurable_environments_ready(connection):
        raise RuntimeError("Environment constraint validation failed")


@click.command()
@click.option("--check", "check_only", is_flag=True, help="Read-only schema compatibility check.")
@click.option("--apply", "apply", is_flag=True, help="Apply migration 004 transactionally.")
@click.option("--json", "json_output", is_flag=True, help="Emit machine-readable migration state.")
@click.option(
    "--backup-confirmed", is_flag=True, help="Confirm a verified backup and stopped writers."
)
@require_mutation_authorization("services.migrate", when=lambda values: bool(values.get("apply")))
def main(check_only: bool, apply: bool, backup_confirmed: bool, json_output: bool) -> None:
    """Check or explicitly upgrade environment constraints. Never prints the DSN."""
    if check_only == apply or (apply and not backup_confirmed):
        raise click.UsageError(
            "Choose --check, or --apply --backup-confirmed after backup and writer shutdown."
        )
    dsn = get_process_settings().phlo_run_evidence_db_url
    if not dsn:
        raise click.ClickException("PHLO_RUN_EVIDENCE_DB_URL is required.")
    try:
        with psycopg2.connect(dsn) as connection:
            if check_only:
                connection.set_session(readonly=True)
            if apply:
                apply_configurable_environments(connection)
            ready = configurable_environments_ready(connection)
    except (psycopg2.Error, RuntimeError) as exc:
        raise click.ClickException(
            "Environment migration failed; transaction rolled back. Inspect database logs."
        ) from exc
    if json_output:
        click.echo(
            json.dumps(
                {
                    "migration": "004_configurable_environments",
                    "state": "compatible" if ready else "required",
                }
            )
        )
    elif ready:
        click.echo("Environment constraints: compatible (004).")
    else:
        click.echo("Migration 004 is required; back up, stop writers, then apply.", err=True)
    if not ready:
        raise click.exceptions.Exit(1)


if __name__ == "__main__":
    main()
