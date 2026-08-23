"""Nessie catalog scanner for table discovery.

This module provides a lightweight Nessie API client focused on catalog discovery:
- namespaces (schemas)
- tables within namespaces
- per-table metadata payloads

The scanner supports fallback to Trino query engine when direct Nessie REST API
calls fail. It deliberately does not know about any downstream metadata systems
(e.g., OpenMetadata), maintaining separation of concerns.

Example:
    >>> from phlo_nessie.catalog_scanner import NessieTableScanner
    >>> scanner = NessieTableScanner.from_config()
    >>> catalog = scanner.scan_all_tables()

Classes:
    NessieTableScanner: Scan Nessie Iceberg REST catalog for namespaces and tables.

"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlparse

import requests
from phlo.capabilities import QueryEngine, resolve_capability
from phlo.logging import get_logger
from phlo_nessie.settings import get_settings

logger = get_logger(__name__)


class NessieTableScanner:
    """Scan Nessie Iceberg REST catalog for namespaces and tables.

    Lightweight client for catalog discovery against the Nessie REST API,
    falling back to a Trino query engine when direct API calls fail.

    Example:
        >>> scanner = NessieTableScanner("http://nessie:19120/iceberg")
        >>> catalog = scanner.scan_all_tables()
        >>> for ns, tables in catalog.items():
        ...     print(f"{ns}: {len(tables)} tables")

    """

    def __init__(self, nessie_uri: str, timeout_seconds: int = 30):
        """Store the Nessie base URI and per-request timeout.

        Example:
            >>> scanner = NessieTableScanner("http://nessie:19120/iceberg", timeout_seconds=60)

        """
        self.nessie_uri = nessie_uri.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self._scan_fallback_used = False

    @classmethod
    def from_config(cls) -> NessieTableScanner:
        """Build a scanner from the configured Nessie Iceberg REST URI."""
        settings = get_settings()
        return cls(nessie_uri=settings.nessie_iceberg_rest_uri())

    def _request(self, method: str, endpoint: str, params: dict[str, Any] | None = None) -> Any:
        """Execute an HTTP request and parse the JSON body; an empty body yields an empty dict."""
        url = f"{self.nessie_uri.rstrip('/')}/{endpoint.lstrip('/')}"
        response = requests.request(
            method=method,
            url=url,
            params=params,
            timeout=self.timeout_seconds,
        )
        response.raise_for_status()
        return response.json() if response.text else {}

    def list_namespaces(self) -> list[dict[str, Any]]:
        """List namespaces from the REST API, falling back to Trino on 404.

        Each returned object carries a 'namespace' key holding path components;
        API errors other than 404 raise requests.HTTPError.

        Example:
            >>> scanner = NessieTableScanner.from_config()
            >>> namespaces = scanner.list_namespaces()
            >>> print([ns['namespace'] for ns in namespaces])
            [['raw'], ['bronze'], ['silver']]

        """
        try:
            data = self._request("GET", "/v1/namespaces")
        except requests.HTTPError as exc:
            response = getattr(exc, "response", None)
            if response is not None and response.status_code == 404:
                self._scan_fallback_used = True
                logger.warning(
                    "nessie_rest_namespace_list_fallback_to_trino",
                    nessie_uri=self.nessie_uri,
                )
                return self._list_namespaces_via_trino()
            raise
        namespaces = data.get("namespaces", [])
        if not isinstance(namespaces, list):
            return []
        normalized: list[dict[str, Any]] = []
        for entry in namespaces:
            if isinstance(entry, dict) and "namespace" in entry:
                normalized.append(entry)
            elif isinstance(entry, list) and all(isinstance(p, str) for p in entry):
                normalized.append({"namespace": entry})
            elif isinstance(entry, str):
                normalized.append({"namespace": [entry]})
        return normalized

    def list_tables_in_namespace(self, namespace: str | list[str]) -> list[dict[str, Any]]:
        """List tables in a namespace via REST, falling back to Trino on 404.

        Accepts a namespace name or path-component list and returns objects
        with 'name' keys; non-404 API errors raise requests.HTTPError.

        Example:
            >>> scanner = NessieTableScanner.from_config()
            >>> tables = scanner.list_tables_in_namespace("raw")
            >>> print([t['name'] for t in tables])
            ['customers', 'orders', 'products']

        """
        namespace_name = ".".join(namespace) if isinstance(namespace, list) else namespace
        try:
            data = self._request("GET", f"/v1/namespaces/{namespace_name}/tables")
        except requests.HTTPError as exc:
            response = getattr(exc, "response", None)
            if response is not None and response.status_code == 404:
                self._scan_fallback_used = True
                logger.warning(
                    "nessie_rest_table_list_fallback_to_trino",
                    namespace=namespace_name,
                    nessie_uri=self.nessie_uri,
                )
                return self._list_tables_via_trino(namespace_name)
            raise
        tables = data.get("tables")
        if isinstance(tables, list):
            return tables
        identifiers = data.get("identifiers")
        if isinstance(identifiers, list):
            normalized_tables = []
            for ident in identifiers:
                if isinstance(ident, dict) and isinstance(ident.get("name"), str):
                    normalized_tables.append({"name": ident["name"]})
            return normalized_tables
        return []

    def get_table_metadata(self, namespace: str, table_name: str) -> dict[str, Any] | None:
        """Fetch normalized table metadata (schema, partitioning, properties) from Nessie.

        Falls back to a Trino DESCRIBE when the REST call returns 404 and to
        None when the table cannot be resolved anywhere; other API errors
        raise requests.HTTPError.

        Example:
            >>> scanner = NessieTableScanner.from_config()
            >>> meta = scanner.get_table_metadata("raw", "customers")
            >>> print(meta.get('schema', {}).get('fields', []))

        """
        try:
            data = self._request("GET", f"/v1/namespaces/{namespace}/tables/{table_name}")
            return self._normalize_table_metadata(table_name, data)
        except requests.HTTPError as e:
            response = getattr(e, "response", None)
            if response is not None and response.status_code == 404:
                metadata = self._get_table_metadata_via_trino(namespace, table_name)
                if metadata:
                    return metadata
                return None
            raise

    def _list_namespaces_via_trino(self) -> list[dict[str, Any]]:
        """List namespaces via Trino SHOW SCHEMAS; empty list without an engine or on failure.

        Example:
            >>> namespaces = scanner._list_namespaces_via_trino()
            >>> print([ns['namespace'] for ns in namespaces])

        """
        trino = self._get_query_engine()
        if trino is None:
            return []
        try:
            rows = trino.execute("SHOW SCHEMAS")
        except Exception as exc:  # noqa: BLE001 - log and return empty
            logger.warning(
                "nessie_trino_schema_list_failed",
                error=str(exc),
                exc_info=True,
            )
            return []
        namespaces = []
        for row in rows:
            if row and isinstance(row[0], str):
                namespaces.append({"namespace": [row[0]]})
        return namespaces

    def _list_tables_via_trino(self, namespace: str) -> list[dict[str, Any]]:
        """List tables in a namespace via Trino SHOW TABLES; empty list on failure.

        Example:
            >>> tables = scanner._list_tables_via_trino("raw")
            >>> print([t['name'] for t in tables])

        """
        trino = self._get_query_engine()
        if trino is None:
            return []
        try:
            rows = trino.execute(f"SHOW TABLES FROM {namespace}")
        except Exception as exc:  # noqa: BLE001 - log and return empty
            logger.warning(
                "nessie_trino_table_list_failed",
                namespace=namespace,
                error=str(exc),
                exc_info=True,
            )
            return []
        tables = []
        for row in rows:
            if row and isinstance(row[0], str):
                tables.append({"name": row[0]})
        return tables

    def _get_table_metadata_via_trino(
        self, namespace: str, table_name: str
    ) -> dict[str, Any] | None:
        """Build metadata from a Trino DESCRIBE; None when unavailable or empty.

        Example:
            >>> meta = scanner._get_table_metadata_via_trino("raw", "customers")
            >>> print(meta.get('schema', {}).get('fields', []))

        """
        trino = self._get_query_engine()
        if trino is None:
            return None
        try:
            rows = trino.execute(f"DESCRIBE {namespace}.{table_name}")
        except Exception as exc:  # noqa: BLE001 - log and return None
            logger.warning(
                "nessie_trino_describe_failed",
                namespace=namespace,
                table_name=table_name,
                error=str(exc),
                exc_info=True,
            )
            return None
        fields = []
        for row in rows:
            if not row:
                continue
            name = row[0] if isinstance(row[0], str) else None
            data_type = row[1] if len(row) > 1 and isinstance(row[1], str) else None
            if not name or not data_type:
                continue
            fields.append({"name": name, "type": data_type})
        if not fields:
            return None
        return {"name": table_name, "schema": {"fields": fields}}

    def _get_query_engine(self) -> QueryEngine | None:
        """Resolve the configured query-engine capability, or None when unavailable.

        Example:
            >>> engine = scanner._get_query_engine()
            >>> if engine:
            ...     result = engine.execute("SELECT 1")

        """
        query_engine_name = get_settings().nessie_query_engine
        resolution = resolve_capability("query_engine", query_engine_name)
        if resolution is None:
            logger.warning(
                "nessie_query_engine_unavailable",
                required_capability=(
                    f"query_engine:{query_engine_name}" if query_engine_name else "query_engine"
                ),
            )
            return None
        return resolution.provider

    def _normalize_table_metadata(self, table_name: str, data: Any) -> dict[str, Any]:
        """Normalize varied Nessie payloads into a stable {name, schema, properties} shape.

        Example:
            >>> raw_data = {"metadata": {"schema": {"fields": [...]}}}
            >>> normalized = scanner._normalize_table_metadata("customers", raw_data)
            >>> print(normalized.get('schema'))

        """
        if not isinstance(data, dict):
            return {"name": table_name}
        metadata = data.get("metadata")
        if not isinstance(metadata, dict):
            return data

        normalized: dict[str, Any] = {"name": table_name}

        schema = None
        if isinstance(metadata.get("schema"), dict):
            schema = metadata.get("schema")
        else:
            schemas = metadata.get("schemas")
            current_schema_id = metadata.get("current-schema-id")
            if isinstance(schemas, list):
                if isinstance(current_schema_id, int):
                    for entry in schemas:
                        if isinstance(entry, dict) and entry.get("schema-id") == current_schema_id:
                            schema = entry
                            break
                if schema is None and schemas:
                    first = schemas[0]
                    if isinstance(first, dict):
                        schema = first
        if isinstance(schema, dict) and isinstance(schema.get("fields"), list):
            normalized["schema"] = {"fields": schema.get("fields")}

        location = metadata.get("location")
        if isinstance(location, str):
            normalized["properties"] = {"location": location}

        if isinstance(metadata.get("properties"), dict) and "properties" not in normalized:
            normalized["properties"] = metadata.get("properties")

        return normalized

    def _get_catalog_ref_for_logs(self) -> str | None:
        """Infer the Nessie ref (branch/tag) from the URI path for logging.

        Example:
            >>> scanner.nessie_uri = "http://nessie:19120/iceberg/main"
            >>> ref = scanner._get_catalog_ref_for_logs()
            'main'

        """
        path = urlparse(self.nessie_uri).path.rstrip("/")
        if not path:
            return None
        path_parts = [part for part in path.split("/") if part]
        if not path_parts:
            return None
        if path_parts[-1] == "iceberg":
            return None
        return path_parts[-1]

    def scan_all_tables(self) -> dict[str, list[dict[str, Any]]]:
        """Scan every namespace and table, tracking whether Trino fallback was used.

        Errors from underlying calls propagate after logging scan progress;
        check ``scanner._scan_fallback_used`` afterwards to detect fallback.

        Example:
            >>> scanner = NessieTableScanner.from_config()
            >>> catalog = scanner.scan_all_tables()
            >>> for ns, tables in catalog.items():
            ...     print(f"{ns}: {len(tables)} tables")

        """
        self._scan_fallback_used = False
        catalog_ref = self._get_catalog_ref_for_logs()

        logger.info(
            "nessie_catalog_scan_all_tables_started",
            nessie_uri=self.nessie_uri,
            ref=catalog_ref,
        )

        catalog: dict[str, list[dict[str, Any]]] = {}
        namespace_count = 0
        table_count = 0
        try:
            for ns_obj in self.list_namespaces():
                ns_parts = ns_obj.get("namespace")
                if not isinstance(ns_parts, list) or not all(isinstance(p, str) for p in ns_parts):
                    continue
                ns_name = ".".join(ns_parts)
                tables = self.list_tables_in_namespace(ns_parts)
                catalog[ns_name] = tables
                namespace_count += 1
                table_count += len(tables)
        except Exception as exc:
            logger.error(
                "nessie_catalog_scan_all_tables_failed",
                nessie_uri=self.nessie_uri,
                ref=catalog_ref,
                namespace_count=namespace_count,
                table_count=table_count,
                fallback_used=self._scan_fallback_used,
                error_type=type(exc).__name__,
                exc_info=True,
            )
            raise

        if self._scan_fallback_used:
            logger.warning(
                "nessie_catalog_scan_all_tables_fallback_used",
                nessie_uri=self.nessie_uri,
                ref=catalog_ref,
                namespace_count=namespace_count,
                table_count=table_count,
                fallback_used=True,
            )

        logger.info(
            "nessie_catalog_scan_all_tables_completed",
            nessie_uri=self.nessie_uri,
            ref=catalog_ref,
            namespace_count=namespace_count,
            table_count=table_count,
            fallback_used=self._scan_fallback_used,
        )
        return catalog
