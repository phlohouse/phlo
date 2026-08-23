"""Lineage sink capability provider for phlo-lineage.

This module implements the PhloLineageSink class, which wraps the low-level
LineageStore and graph functionality into a standardized capability interface.
It provides a simplified API for recording lineage events and querying lineage
information through the Phlo capability system.

The lineage sink enables:
    - Asset edge recording (source -> target dependencies)
    - Row-level lineage tracking with parent relationships
    - Column-level lineage mapping persistence
    - Graph retrieval for visualization
    - Row journey queries (ancestors and descendants)

Architecture:
    PhloLineageSink is exposed as a capability provider through the plugin
    system (see resource_provider.py). It wraps LineageStore for persistence
    and LineageGraph for in-memory analysis.

Example:
    >>> from phlo_lineage.lineage_sink import PhloLineageSink
    >>> sink = PhloLineageSink()
    >>>
    >>> # Record asset dependencies
    >>> sink.record_asset_edges([
    ...     ("bronze.orders", "silver.stg_orders"),
    ...     ("silver.stg_orders", "gold.fct_orders"),
    ... ])
    >>>
    >>> # Get the current graph
    >>> graph = sink.get_asset_graph()
    >>> print(f"Total assets: {len(graph.assets)}")

"""

from __future__ import annotations

from typing import Any

from phlo_lineage.graph import get_lineage_graph
from phlo_lineage.store import (
    ColumnLineage,
    LineageStore,
    resolve_lineage_db_url_with_postgres_fallback,
)


class PhloLineageSink:
    """Capability wrapper around phlo-lineage store and graph functionality.

    This class provides a simplified, capability-friendly interface to the
    lineage system. It handles connection management internally and exposes
    high-level methods for recording and querying lineage data.

    The sink is designed to be used as a capability provider in the Phlo
    plugin system, but can also be instantiated directly for programmatic use.

    Capabilities Provided:
        - Asset lineage edge recording
        - Row-level provenance tracking
        - Column-level mapping persistence
        - Graph access for visualization
        - Row journey analysis (ancestors/descendants)

    Configuration:
        Requires the PHLO_LINEAGE_DB_URL environment variable or standard
        PostgreSQL connection variables (POSTGRES_HOST, POSTGRES_PORT, etc.).

    Raises RuntimeError when the database URL cannot be resolved while
    recording or querying.

    Example:
        >>> sink = PhloLineageSink()
        >>>
        >>> # Record row lineage
        >>> sink.record_row_lineage(
        ...     row_id="01ARZ3NDEKTSV4RRFFQ69G5FAV",
        ...     table_name="bronze.orders",
        ...     source_type="dlt",
        ...     parent_row_ids=[],
        ... )
        >>>
        >>> # Query row journey
        >>> journey = sink.get_row_journey(row_id="01ARZ3NDEKTSV4RRFFQ69G5FAV")
        >>> print(f"Ancestors: {len(journey['ancestors'])}")

    """

    def record_asset_edges(
        self,
        edges: list[tuple[str, str]],
        *,
        asset_keys: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
        tags: dict[str, str] | None = None,
    ) -> int:
        """Persist directed (source -> target) asset lineage edges, creating
        or updating node entries for every asset mentioned; additional
        unconnected asset_keys can be registered too. Returns the number of
        edges persisted and raises RuntimeError when the lineage database is
        not configured.

        Example:
            >>> sink = PhloLineageSink()
            >>> count = sink.record_asset_edges(
            ...     edges=[
            ...         ("bronze.orders", "silver.stg_orders"),
            ...         ("silver.stg_orders", "gold.fct_orders"),
            ...     ],
            ...     metadata={"run_id": "manual-001"},
            ... )
            >>> print(f"Recorded {count} edges")

        """
        return self._get_store().record_asset_edges(
            edges,
            asset_keys=asset_keys,
            metadata=metadata,
            tags=tags,
        )

    def record_row_lineage(
        self,
        *,
        row_id: str,
        table_name: str,
        source_type: str,
        parent_row_ids: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Persist the provenance of one row: its origin source_type ("dlt",
        "dbt", "external", or "manual"), fully qualified table name, and the
        parent rows it was derived from. row_id is a ULID from
        generate_row_id() or the _phlo_row_id column; raises RuntimeError
        when the lineage database is not configured.

        Example:
            >>> sink = PhloLineageSink()
            >>> sink.record_row_lineage(
            ...     row_id="01ARZ3NDEKTSV4RRFFQ69G5FAV",
            ...     table_name="bronze.orders",
            ...     source_type="dlt",
            ...     parent_row_ids=[],
            ...     metadata={"source": "api", "batch_id": "batch-001"},
            ... )

        """
        self._get_store().record_row(
            row_id=row_id,
            table_name=table_name,
            source_type=source_type,
            parent_row_ids=parent_row_ids,
            metadata=metadata,
        )

    def record_column_lineage(self, mappings: list[dict[str, Any]]) -> int:
        """Persist column-level mappings (raw dicts with source_asset,
        source_column, target_asset, target_column, optional source_type
        defaulting to "dbt_heuristic", and optional metadata); they are
        converted to ColumnLineage dataclasses internally. Returns the number
        of mappings persisted; raises RuntimeError without a configured
        database.

        Example:
            >>> sink = PhloLineageSink()
            >>> mappings = [
            ...     {
            ...         "source_asset": "bronze.orders",
            ...         "source_column": "order_id",
            ...         "target_asset": "silver.stg_orders",
            ...         "target_column": "order_id",
            ...         "source_type": "dbt_heuristic",
            ...         "metadata": {"confidence": 0.95},
            ...     },
            ... ]
            >>> count = sink.record_column_lineage(mappings)

        """
        return self._get_store().record_column_lineage(
            [
                ColumnLineage(
                    source_asset=str(mapping["source_asset"]),
                    source_column=str(mapping["source_column"]),
                    target_asset=str(mapping["target_asset"]),
                    target_column=str(mapping["target_column"]),
                    source_type=str(mapping.get("source_type") or "dbt_heuristic"),
                    metadata=mapping.get("metadata")
                    if isinstance(mapping.get("metadata"), dict | type(None))
                    else None,
                )
                for mapping in mappings
            ]
        )

    def get_asset_graph(self) -> Any:
        """Return the global LineageGraph singleton, lazily loaded from the
        persistent store on first access; empty when no lineage database is
        configured or holds no data. See phlo_lineage.graph.LineageGraph for
        analysis methods.

        Example:
            >>> sink = PhloLineageSink()
            >>> graph = sink.get_asset_graph()
            >>>
            >>> # Analyze dependencies
            >>> upstream = graph.get_upstream("gold.fct_orders")
            >>> downstream = graph.get_downstream("bronze.orders")
            >>>
            >>> # Export visualization
            >>> dot = graph.to_dot()

        """
        return get_lineage_graph()

    def get_row_journey(self, *, row_id: str, depth: int = 10) -> Any:
        """Return a dict with current (the row's own info, or None if not
        found), ancestors sorted newest first, and descendants oldest first,
        traversing up to depth levels (default 10). Useful for tracing a row
        back to its source and assessing downstream impact; raises
        RuntimeError without a configured database.

        Example:
            >>> sink = PhloLineageSink()
            >>> journey = sink.get_row_journey(
            ...     row_id="01ARZ3NDEKTSV4RRFFQ69G5FAV",
            ...     depth=5,
            ... )
            >>>
            >>> if journey["current"]:
            ...     print(f"Row in table: {journey['current']['table_name']}")
            ...     print(f"Ancestors: {len(journey['ancestors'])}")
            ...     print(f"Descendants: {len(journey['descendants'])}")

        """
        store = self._get_store()
        return {
            "current": store.get_row(row_id),
            "ancestors": store.get_ancestors(row_id, max_depth=depth),
            "descendants": store.get_descendants(row_id, max_depth=depth),
        }

    @staticmethod
    def _get_store() -> LineageStore:
        """Build a LineageStore from the environment-resolved connection
        string (PHLO_LINEAGE_DB_URL, then standard PostgreSQL variables).
        A new store is created per call; RuntimeError when no URL resolves.
        See resolve_lineage_db_url_with_postgres_fallback() for resolution
        logic.

        Example:
            >>> store = PhloLineageSink._get_store()
            >>> # Use store directly for advanced operations
            >>> rows = store.get_table_rows("bronze.orders", limit=10)

        """
        connection_string = resolve_lineage_db_url_with_postgres_fallback()
        if not connection_string:
            raise RuntimeError("Lineage sink requires PHLO_LINEAGE_DB_URL to be configured.")
        return LineageStore(connection_string)
