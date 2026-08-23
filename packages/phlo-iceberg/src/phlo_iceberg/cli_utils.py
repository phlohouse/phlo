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
    This module is separate from the main catalog module to avoid
    circular dependencies between CLI utilities and core functionality.

"""

from __future__ import annotations

from functools import lru_cache


@lru_cache(maxsize=None)
def get_iceberg_catalog(ref: str = "main"):
    """Get a cached Iceberg catalog instance for CLI operations.

    Uses LRU cache to provide consistent catalog connections across
    multiple CLI commands. The cache has no size limit (maxsize=None)
    since CLI processes are typically short-lived.

    Example:
        Use in CLI commands::

            from phlo_iceberg.cli_utils import get_iceberg_catalog

            # List tables
            catalog = get_iceberg_catalog(ref="main")
            tables = catalog.list_tables("raw")

            # Later in same CLI session - reuses cached connection
            catalog2 = get_iceberg_catalog(ref="main")
            assert catalog is catalog2  # Same instance

    See Also:
        :func:`phlo_iceberg.catalog.get_catalog`: Core catalog function
            that this utility wraps.

    """
    from phlo_iceberg.catalog import get_catalog

    return get_catalog(ref=ref)
