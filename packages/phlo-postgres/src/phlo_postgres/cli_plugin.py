"""Postgres CLI plugin registration.

Exposes the postgres command group as a cli-command plugin so the
phlo-postgres package contributes its commands through plugin discovery
rather than a core-CLI import.
Loaded through the phlo plugin entry-point mechanism at startup rather than
imported directly; exposes the command group from phlo_postgres.cli.
"""

from __future__ import annotations


from phlo.plugins.base import cli_command_plugin_class

from phlo_postgres.cli import postgres_group


PostgresCliPlugin = cli_command_plugin_class(
    "PostgresCliPlugin",
    name="postgres",
    version="0.1.0",
    description="CLI commands for PostgreSQL service access",
    commands=[postgres_group],
)
