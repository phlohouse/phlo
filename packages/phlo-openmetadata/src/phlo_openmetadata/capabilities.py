"""Capability resolution helpers for phlo-openmetadata.

This module provides utilities for resolving phlo capability providers
needed by OpenMetadata integration, specifically catalog scanners and
query engine metadata.

Example:
    >>> from phlo_openmetadata.capabilities import resolve_catalog_scanner
    >>> scanner = resolve_catalog_scanner()
    >>> tables = scanner.scan_all_tables()

"""

from __future__ import annotations

from phlo.capabilities import resolve_capability
from phlo.capabilities.interfaces import CatalogScanner


def _discover_capabilities() -> None:
    """Trigger capability discovery to populate the registry.

    This internal function ensures all capability providers are loaded
    before attempting to resolve specific capabilities.
    """
    from phlo.capabilities.discovery import discover_capabilities

    discover_capabilities()


def resolve_catalog_scanner(name: str | None = None) -> CatalogScanner:
    """Resolve a catalog scanner capability for metadata sync flows; name
    picks a specific scanner, otherwise the first available is used.
    Raises RuntimeError when no matching scanner exists.
    """
    _discover_capabilities()
    resolution = resolve_capability("catalog_scanner", name)
    if resolution is None:
        if name:
            raise RuntimeError(f"Catalog scanner capability '{name}' is not available.")
        raise RuntimeError("No catalog scanner capability is available.")
    return resolution.provider


def resolve_query_engine_catalog(name: str | None = None) -> str:
    """Return the default catalog name from query-engine capability metadata;
    name picks a specific engine, otherwise the first available is used.
    Raises RuntimeError when unavailable or lacking catalog metadata.
    """
    _discover_capabilities()
    resolution = resolve_capability("query_engine", name)
    if resolution is None:
        if name:
            raise RuntimeError(f"Query engine capability '{name}' is not available.")
        raise RuntimeError("No query engine capability is available.")

    metadata = resolution.metadata
    for key in ("catalog", "default_catalog", "catalog_name"):
        catalog = metadata.get(key)
        if isinstance(catalog, str) and catalog:
            return catalog

    provider_name = name or getattr(resolution, "name", None) or "resolved query engine"
    raise RuntimeError(
        f"Query engine capability '{provider_name}' does not declare catalog metadata."
    )


def resolve_query_engine_service_type(name: str | None = None) -> str:
    """Return the OpenMetadata service type (e.g. 'Trino', 'Snowflake') from
    query-engine capability metadata; name picks a specific engine,
    otherwise the first available is used. Raises RuntimeError when
    unavailable or lacking service_type metadata.
    """
    _discover_capabilities()
    resolution = resolve_capability("query_engine", name)
    if resolution is None:
        if name:
            raise RuntimeError(f"Query engine capability '{name}' is not available.")
        raise RuntimeError("No query engine capability is available.")

    metadata = resolution.metadata
    for key in ("service_type", "openmetadata_service_type"):
        service_type = metadata.get(key)
        if isinstance(service_type, str) and service_type:
            return service_type

    provider_name = name or getattr(resolution, "name", None) or "resolved query engine"
    raise RuntimeError(
        f"Query engine capability '{provider_name}' does not declare service_type metadata."
    )
