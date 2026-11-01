"""Run Dagster's daemon with connection reuse scoped to its owning process.

Run workers and code servers reconstruct their own instances from InstanceRef;
they never receive this daemon's connection pools. Polling and transaction
semantics remain owned by Dagster.
"""

from __future__ import annotations

import click
import psycopg2
import psycopg2.extensions
from dagster._cli.utils import assert_no_remaining_opts, get_instance_for_cli
from dagster._daemon.cli import _daemon_run_command
from dagster._utils.interrupts import capture_interrupts, setup_interrupt_handlers
from dagster_shared.cli import WorkspaceOpts, workspace_options
from sqlalchemy import event
from sqlalchemy.exc import InvalidatePoolError
from sqlalchemy.pool import QueuePool


def _ping_postgres_connection(connection: object, _record: object, _proxy: object) -> None:
    """Recycle disconnected sessions before a scheduled run can consume one."""
    if not isinstance(connection, psycopg2.extensions.connection):
        return
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
    except psycopg2.Error as exc:
        raise InvalidatePoolError from exc


@click.command()
@workspace_options
def main(**other_opts: object) -> None:
    """Run the configured daemons with the standard Dagster workspace options."""
    workspace_opts = WorkspaceOpts.extract_from_cli_options(other_opts)
    assert_no_remaining_opts(other_opts)
    setup_interrupt_handlers()
    # Dagster's optimisation API does not expose pool_pre_ping. SQLAlchemy's
    # checkout hook retries dead sessions before user work, in this process only.
    event.listen(QueuePool, "checkout", _ping_postgres_connection)
    try:
        with capture_interrupts(), get_instance_for_cli() as instance:
            # Reuse Dagster's storage API, not a global SQLAlchemy pool override.
            # Zero leaves statements unlimited, as in the unpooled daemon.
            instance.optimize_for_webserver(statement_timeout=0, pool_recycle=3600, max_overflow=10)
            _daemon_run_command(instance, "info", "warning", "colored", workspace_opts)
    except KeyboardInterrupt:
        return


if __name__ == "__main__":
    main()
