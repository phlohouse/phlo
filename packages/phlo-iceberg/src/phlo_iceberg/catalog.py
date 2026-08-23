"""Iceberg catalog management using Nessie REST catalog.

This module provides catalog-level operations for the Iceberg REST catalog
backed by Nessie. It includes catalog connection management, table listing,
and namespace operations.

The catalog uses an LRU cache to avoid repeatedly creating connections.
Use :func:`reset_catalog_cache` to clear cached instances when needed.

Example:
    Catalog operations::

        from phlo_iceberg.catalog import get_catalog, list_tables, create_namespace

        # Get catalog connection
        catalog = get_catalog(ref="main")

        # List tables in namespace
        tables = list_tables(namespace="raw", ref="main")
        print(f"Tables in raw namespace: {tables}")

        # Create namespace
        create_namespace("staging", ref="main")

See Also:
    - Nessie Catalog: https://projectnessie.org/
    - Iceberg REST Catalog: https://iceberg.apache.org/docs/latest/configuration/

"""

from __future__ import annotations

from functools import lru_cache

from pyiceberg.catalog import load_catalog

from phlo.logging import get_logger
from phlo_iceberg.settings import get_settings

logger = get_logger(__name__)


def _is_namespace_already_exists_error(exc: BaseException) -> bool:
    """Return whether PyIceberg reported an idempotent namespace create conflict."""
    try:
        from pyiceberg.exceptions import NamespaceAlreadyExistsError
    except Exception:
        already_exists_types: tuple[type[BaseException], ...] = ()
    else:
        already_exists_types = (NamespaceAlreadyExistsError,)

    if already_exists_types and isinstance(exc, already_exists_types):
        return True

    message = str(exc).lower()
    return "namespace already exists" in message or "alreadyexistsexception" in message


@lru_cache(maxsize=16)
def get_catalog(ref: str = "main"):
    """Get a configured PyIceberg REST catalog instance for a Nessie ref (cached).

    Up to 16 instances are cached by reference name; call
    :func:`reset_catalog_cache` first to force fresh connections. PyIceberg
    connection errors are logged, then re-raised.

    Example:
        Get catalog for different branches::

            main_catalog = get_catalog(ref="main")
            dev_catalog = get_catalog(ref="dev-branch")

            # Access tables through catalog
            table = main_catalog.load_table("raw.events")

    """
    logger.debug(
        "iceberg_catalog_get_requested",
        ref=ref,
    )
    catalog_config = get_settings().get_pyiceberg_catalog_config(ref=ref)
    try:
        catalog = load_catalog(name=f"iceberg_{ref}", **catalog_config)
    except Exception:
        logger.error(
            "iceberg_catalog_get_failed",
            ref=ref,
            exc_info=True,
        )
        raise
    logger.debug(
        "iceberg_catalog_get_succeeded",
        ref=ref,
    )
    return catalog


def reset_catalog_cache() -> None:
    """Clear all cached catalog instances.

    Forces fresh catalog connections on next :func:`get_catalog` call.
    Useful when switching environments or after configuration changes.

    Example:
        Force reconnection after config update::

            reset_catalog_cache()
            catalog = get_catalog(ref="main")  # Fresh connection

    """
    from phlo_iceberg.cli_utils import get_iceberg_catalog

    get_catalog.cache_clear()
    get_iceberg_catalog.cache_clear()
    logger.debug("iceberg_catalog_cache_cleared")


def list_tables(namespace: str | None = None, ref: str = "main") -> list[str]:
    """List fully qualified table names in one namespace or across all of them.

    Catalog access errors are logged, then re-raised.

    Example:
        List tables in specific namespace::

            tables = list_tables(namespace="raw", ref="main")
            print(f"Raw tables: {tables}")

        List all tables::

            all_tables = list_tables(namespace=None, ref="main")
            print(f"Total tables: {len(all_tables)}")

    """
    logger.info(
        "iceberg_catalog_list_tables_requested",
        namespace=namespace,
        ref=ref,
    )
    catalog = get_catalog(ref=ref)

    try:
        if namespace:
            tables = [str(table) for table in catalog.list_tables(namespace)]
            logger.info(
                "iceberg_catalog_list_tables_succeeded",
                namespace=namespace,
                ref=ref,
                table_count=len(tables),
            )
            return tables

        all_tables: list[str] = []
        for ns in catalog.list_namespaces():
            ns_name = ".".join(ns)
            all_tables.extend([str(table) for table in catalog.list_tables(ns_name)])
        logger.info(
            "iceberg_catalog_list_tables_succeeded",
            namespace=namespace,
            ref=ref,
            table_count=len(all_tables),
        )
        return all_tables
    except Exception:
        logger.error(
            "iceberg_catalog_list_tables_failed",
            namespace=namespace,
            ref=ref,
            exc_info=True,
        )
        raise


def create_namespace(namespace: str, ref: str = "main") -> None:
    """Create a namespace in the catalog; idempotent when it already exists.

    An existing namespace logs a warning instead of raising, so repeated
    calls are safe. Namespaces are the Iceberg analogue of schemas.

    Example:
        Create namespace for staging tables::

            create_namespace("staging", ref="main")
            create_namespace("staging_temp", ref="dev")

    """
    logger.info(
        "iceberg_catalog_create_namespace_requested",
        namespace=namespace,
        ref=ref,
    )
    catalog = get_catalog(ref=ref)
    try:
        catalog.create_namespace(namespace)
        logger.info(
            "iceberg_catalog_create_namespace_succeeded",
            namespace=namespace,
            ref=ref,
        )
    except Exception as exc:
        if _is_namespace_already_exists_error(exc):
            logger.info(
                "iceberg_catalog_create_namespace_exists",
                namespace=namespace,
                ref=ref,
            )
            return None

        logger.warning(
            "iceberg_catalog_create_namespace_failed",
            namespace=namespace,
            ref=ref,
            exc_info=True,
        )
        return None
