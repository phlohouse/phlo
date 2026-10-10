"""CLI helper utilities for Iceberg.

This module provides cached access to Iceberg catalog instances for use
in CLI commands. The caching ensures consistent catalog connections
across multiple CLI operations within the same process.

The primary use case is for Phlo CLI commands that need to interact
with Iceberg tables (listing, inspecting, creating, etc.).

Example:
    CLI command using cached catalog::

        from phlo_iceberg.cli_utils import get_iceberg_catalog

        @click.command()
        @click.argument("table_name")
        def inspect_table(table_name):
            # Reuses cached connection
            catalog = get_iceberg_catalog(ref="main")
            table = catalog.load_table(table_name)
            print(f"Table: {table.name}")
            print(f"Schema: {table.schema()}")

Note:
    The catalog cache is owned by :func:`phlo_iceberg.catalog.get_catalog`;
    this module re-exposes it under a CLI-oriented name, so
    :func:`phlo_iceberg.catalog.reset_catalog_cache` clears it.

"""

from __future__ import annotations

from phlo_iceberg.catalog import get_catalog


def get_iceberg_catalog(ref: str = "main"):
    """Get the cached Iceberg catalog instance for CLI operations.

    Delegates to :func:`phlo_iceberg.catalog.get_catalog`, which owns the
    connection cache, so repeated calls in one CLI process reuse the same
    connection.

    Example:
        Use in CLI commands::

            from phlo_iceberg.cli_utils import get_iceberg_catalog

            catalog = get_iceberg_catalog(ref="main")
            tables = catalog.list_tables("raw")

            # Later in same CLI session - reuses cached connection
            catalog2 = get_iceberg_catalog(ref="main")
            assert catalog is catalog2  # Same instance

    """
    return get_catalog(ref=ref)
