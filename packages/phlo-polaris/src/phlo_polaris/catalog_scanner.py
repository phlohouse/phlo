"""Catalog scanner for Polaris-backed Iceberg tables.

Lists namespaces and tables through the PyIceberg REST catalog. Unavailable
endpoints surface as empty results rather than errors so metadata
synchronization degrades gracefully.
"""

from __future__ import annotations

from typing import Any

from phlo.logging import get_logger
from phlo_polaris.catalog_backend import current_snapshot_id

logger = get_logger(__name__)


class PolarisTableScanner:
    """Scan Polaris-managed Iceberg tables for metadata synchronization."""

    def __init__(self, catalog_loader: Any = None) -> None:
        self._catalog_loader = catalog_loader

    @classmethod
    def from_config(cls) -> "PolarisTableScanner":
        """Build a scanner from the resolved Polaris settings."""
        return cls()

    def _load_catalog(self) -> Any:
        if self._catalog_loader is not None:
            return self._catalog_loader()
        from phlo_polaris.catalog_backend import load_pyiceberg_catalog

        return load_pyiceberg_catalog()

    def list_namespaces(self) -> list[str]:
        """List namespaces visible through the REST catalog."""
        try:
            catalog = self._load_catalog()
            return [".".join(namespace) for namespace in catalog.list_namespaces() if namespace]
        except Exception:
            logger.warning("polaris_scanner_namespaces_failed", exc_info=True)
            return []

    def list_tables_in_namespace(self, namespace: str) -> list[str]:
        """List tables within one namespace."""
        try:
            catalog = self._load_catalog()
            return [table[-1] for table in catalog.list_tables(namespace)]
        except Exception:
            logger.warning("polaris_scanner_tables_failed", namespace=namespace, exc_info=True)
            return []

    def get_table_metadata(self, namespace: str, table_name: str) -> dict[str, Any] | None:
        """Return normalized metadata for one table, or None when unavailable."""
        try:
            catalog = self._load_catalog()
            table = catalog.load_table(f"{namespace}.{table_name}")
            schema = table.schema()
            return {
                "namespace": namespace,
                "table": table_name,
                "location": table.location(),
                "columns": [
                    {"name": field.name, "type": str(field.field_type)} for field in schema.fields
                ],
                "current_snapshot_id": current_snapshot_id(table),
            }
        except Exception:
            logger.warning(
                "polaris_scanner_metadata_failed",
                namespace=namespace,
                table=table_name,
                exc_info=True,
            )
            return None

    def scan_all_tables(self) -> dict[str, list[dict[str, Any]]]:
        """Return all discovered tables grouped by namespace."""
        results: dict[str, list[dict[str, Any]]] = {}
        for namespace in self.list_namespaces():
            tables = []
            for table_name in self.list_tables_in_namespace(namespace):
                metadata = self.get_table_metadata(namespace, table_name)
                if metadata is not None:
                    tables.append(metadata)
            if tables:
                results[namespace] = tables
        return results
