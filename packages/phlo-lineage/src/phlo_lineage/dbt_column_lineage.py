"""Extract column-level lineage from dbt manifest.json.

This module implements heuristic-based extraction of column-level lineage from
dbt's manifest.json file. It uses same-name intersection heuristics rather than
full SQL parsing, making it lightweight but potentially less accurate than
parsing-based approaches.

Heuristic Method:
    For each dbt model node:
    1. Collect target columns from node["columns"]
    2. For each upstream dependency in depends_on["nodes"]:
       a. Collect upstream columns
       b. Create ColumnLineage for every column name appearing in both sets

    Columns sharing a name across an edge are assumed to carry lineage.
    This assumption holds for well-modeled dbt projects using consistent
    naming conventions.

Source Type Tagging:
    All mappings are tagged with source_type="dbt_heuristic" to signal that
    they were derived via name matching rather than SQL analysis.

Limitations:
    - Column renames are not detected (e.g., user_id -> customer_id)
    - Derived columns (calculations, CASE statements) are not linked
    - Column selection via * is not expanded
    - SQL-level transformations are opaque

Example:
    >>> import json
    >>> from phlo_lineage.dbt_column_lineage import extract_column_lineage
    >>>
    >>> with open("target/manifest.json") as f:
    ...     manifest = json.load(f)
    >>>
    >>> mappings = extract_column_lineage(manifest)
    >>> print(f"Found {len(mappings)} column lineage mappings")
    >>>
    >>> for m in mappings[:3]:
    ...     print(f"{m.source_column} in {m.source_asset} -> {m.target_column} in {m.target_asset}")

See Also:
    phlo_lineage.store.ColumnLineage for the data structure.
    phlo_lineage.store.LineageStore.record_column_lineage() for persistence.

"""

from __future__ import annotations

from typing import Any

from phlo.logging import get_logger
from phlo_lineage.store import ColumnLineage

logger = get_logger(__name__)


def _resolve_asset_name(node: dict[str, Any]) -> str:
    """Derive a qualified asset name from a dbt node dictionary.

    Prefers schema + alias, then schema + name, then bare name; returns
    "schema.name" when a (non-None) schema is present. A None schema is not
    handled, only an empty string.

    Example:
        >>> node = {"schema": "silver", "alias": "stg_orders", "name": "staging_orders"}
        >>> _resolve_asset_name(node)
        'silver.stg_orders'
        >>>
        >>> node = {"schema": "", "name": "raw_orders"}
        >>> _resolve_asset_name(node)
        'raw_orders'

    """
    schema = node.get("schema", "")
    name = node.get("alias") or node.get("name", "")
    return f"{schema}.{name}" if schema else name


def _get_node_columns(node: dict[str, Any]) -> set[str]:
    """Extract column names from a dbt node dictionary.

    Returns only names from the node's "columns" property; types and
    descriptions are ignored. Empty set when no columns are defined.

    Example:
        >>> node = {
        ...     "columns": {
        ...         "order_id": {"type": "int"},
        ...         "customer_id": {"type": "int"},
        ...     }
        ... }
        >>> _get_node_columns(node)
        {'order_id', 'customer_id'}
        >>>
        >>> node = {"name": "orphan_model"}
        >>> _get_node_columns(node)
        set()

    """
    columns: dict[str, Any] = node.get("columns", {})
    return set(columns.keys())


def extract_column_lineage(manifest: dict[str, Any]) -> list[ColumnLineage]:
    """Extract column lineage from a dbt manifest using same-name heuristics.

    For every model node with columns defined, intersects its column names
    with each upstream dependency's columns and emits a ColumnLineage per
    shared name (source_type="dbt_heuristic"). Known limitations: renames,
    derived columns, and SELECT * are not detected; common column names may
    produce false positives.

    Example:
        >>> import json
        >>> from phlo_lineage.dbt_column_lineage import extract_column_lineage
        >>>
        >>> with open("target/manifest.json") as f:
        ...     manifest = json.load(f)
        >>>
        >>> mappings = extract_column_lineage(manifest)
        >>> print(f"Extracted {len(mappings)} column mappings")

    """
    nodes: dict[str, Any] = manifest.get("nodes", {})
    mappings: list[ColumnLineage] = []

    model_nodes = {key: node for key, node in nodes.items() if node.get("resource_type") == "model"}

    logger.info(
        "column_lineage_extraction_started",
        model_count=len(model_nodes),
    )

    for node_key, node in model_nodes.items():
        target_asset = _resolve_asset_name(node)
        target_columns = _get_node_columns(node)

        if not target_columns:
            logger.debug(
                "column_lineage_no_columns",
                node_key=node_key,
                target_asset=target_asset,
            )
            continue

        upstream_keys: list[str] = node.get("depends_on", {}).get("nodes", [])

        for upstream_key in upstream_keys:
            upstream_node = nodes.get(upstream_key)
            if upstream_node is None:
                continue

            source_asset = _resolve_asset_name(upstream_node)
            source_columns = _get_node_columns(upstream_node)

            shared = target_columns & source_columns
            for col in sorted(shared):
                mappings.append(
                    ColumnLineage(
                        source_asset=source_asset,
                        source_column=col,
                        target_asset=target_asset,
                        target_column=col,
                        source_type="dbt_heuristic",
                    )
                )

    logger.info(
        "column_lineage_extraction_completed",
        mapping_count=len(mappings),
    )
    return mappings
