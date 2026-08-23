"""Helpers for importing dbt manifest lineage into configured Phlo sinks.

This module provides utilities for extracting lineage information from dbt
manifest files and importing it into Phlo's lineage tracking system. It handles
both asset-level lineage (dependencies between models) and column-level lineage
(where available).

Example:
    >>> from phlo_dbt.lineage_import import import_manifest_lineage
    >>> from pathlib import Path
    >>>
    >>> summary = import_manifest_lineage(Path("target/manifest.json"))
    >>> print(f"Imported {summary['asset_edges']} asset edges")
    >>> print(f"Imported {summary['column_mappings']} column mappings")

"""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from phlo.logging import get_logger
from phlo_dbt.translator import DbtSpecTranslator

logger = get_logger(__name__)


def _discover_capabilities() -> None:
    """Import and run capability discovery lazily.

    This avoids importing capability discovery while dbt plugins are themselves
    being discovered, which can otherwise create a circular import through
    ``phlo.plugins.discovery``.
    """
    from phlo.capabilities.discovery import discover_capabilities

    discover_capabilities()


def _resolve_capability(capability_type: str) -> Any:
    """Import capability resolution lazily to avoid discovery-time cycles."""
    from phlo.capabilities.resolver import resolve_capability

    return resolve_capability(capability_type)


def load_dbt_manifest(manifest_path: Path) -> dict[str, Any] | None:
    """Return a parsed dbt manifest payload when available."""
    if not manifest_path.exists():
        logger.info("dbt_lineage_manifest_missing", manifest_path=str(manifest_path))
        return None

    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        logger.warning(
            "dbt_lineage_manifest_read_failed",
            manifest_path=str(manifest_path),
            exc_info=True,
        )
        return None

    if not isinstance(manifest, dict):
        logger.warning(
            "dbt_lineage_manifest_invalid",
            manifest_path=str(manifest_path),
            manifest_type=type(manifest).__name__,
        )
        return None

    return manifest


def collect_asset_lineage(
    manifest: Mapping[str, Any],
    *,
    translator: DbtSpecTranslator | None = None,
) -> tuple[list[tuple[str, str]], list[str]]:
    """Collect asset-level lineage edges and asset keys from a dbt manifest."""
    nodes = manifest.get("nodes") or {}
    sources = manifest.get("sources") or {}
    if not isinstance(nodes, Mapping) or not isinstance(sources, Mapping):
        return [], []

    translator = translator or DbtSpecTranslator()
    asset_keys: dict[str, str] = {}
    for unique_id, props in {**nodes, **sources}.items():
        if not isinstance(props, Mapping):
            continue
        try:
            asset_key = translator.get_asset_key(props)
        except Exception:
            logger.debug(
                "dbt_lineage_asset_key_skipped",
                unique_id=str(unique_id),
                exc_info=True,
            )
            continue
        asset_keys[str(unique_id)] = str(asset_key)

    edges: list[tuple[str, str]] = []
    target_keys: set[str] = set()
    for unique_id, props in nodes.items():
        if not isinstance(props, Mapping):
            continue
        resource_type = str(props.get("resource_type") or "")
        if resource_type not in {"model", "seed", "snapshot"}:
            continue
        target_key = asset_keys.get(str(unique_id))
        if not target_key:
            continue

        depends_on = props.get("depends_on") or {}
        depends_nodes = depends_on.get("nodes") or []
        if not isinstance(depends_nodes, list):
            continue

        for upstream_id in depends_nodes:
            source_key = asset_keys.get(str(upstream_id))
            if source_key:
                edges.append((source_key, target_key))
        target_keys.add(target_key)

    return edges, sorted(target_keys)


def extract_column_lineage(manifest: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Extract column lineage from a dbt manifest using same-name heuristics."""
    nodes = manifest.get("nodes") or {}
    if not isinstance(nodes, Mapping):
        return []

    mappings: list[dict[str, Any]] = []
    model_nodes = {
        key: node
        for key, node in nodes.items()
        if isinstance(node, Mapping) and node.get("resource_type") == "model"
    }

    for node in model_nodes.values():
        target_asset = _resolve_manifest_asset_name(node)
        target_columns = _get_manifest_columns(node)
        if not target_columns:
            continue

        upstream_keys = (node.get("depends_on") or {}).get("nodes") or []
        if not isinstance(upstream_keys, list):
            continue

        for upstream_key in upstream_keys:
            upstream_node = nodes.get(upstream_key)
            if not isinstance(upstream_node, Mapping):
                continue

            source_asset = _resolve_manifest_asset_name(upstream_node)
            shared_columns = target_columns & _get_manifest_columns(upstream_node)
            for column_name in sorted(shared_columns):
                mappings.append(
                    {
                        "source_asset": source_asset,
                        "source_column": column_name,
                        "target_asset": target_asset,
                        "target_column": column_name,
                        "source_type": "dbt_heuristic",
                    }
                )

    return mappings


def _resolve_manifest_asset_name(node: Mapping[str, Any]) -> str:
    """Derive a qualified asset name from a dbt manifest node."""
    schema = str(node.get("schema") or "")
    name = str(node.get("alias") or node.get("name") or "")
    return f"{schema}.{name}" if schema else name


def _get_manifest_columns(node: Mapping[str, Any]) -> set[str]:
    """Return declared dbt column names for a manifest node."""
    columns = node.get("columns") or {}
    if not isinstance(columns, Mapping):
        return set()
    return {str(column_name) for column_name in columns}


def import_manifest_lineage(manifest_path: Path) -> dict[str, int]:
    """Import asset lineage and best-effort column lineage from a dbt
    manifest.json into the configured Phlo lineage sink: asset-level
    dependency edges plus column mappings via same-name heuristics.
    Requires a lineage sink capability (e.g., phlo-lineage). Returns counts
    of asset_edges and column_mappings imported.

    Example:
        >>> from pathlib import Path
        >>> from phlo_dbt.lineage_import import import_manifest_lineage
        >>>
        >>> summary = import_manifest_lineage(Path("target/manifest.json"))
        >>> print(f"Assets: {summary['asset_edges']}")
        >>> print(f"Columns: {summary['column_mappings']}")
        >>>
        >>> # Typically called after dbt run
        >>> # Can be integrated into CI/CD or post-run hooks

    """
    manifest = load_dbt_manifest(manifest_path)
    if manifest is None:
        return {"asset_edges": 0, "column_mappings": 0}

    _discover_capabilities()
    resolution = _resolve_capability("lineage_sink")
    if resolution is None:
        logger.info(
            "dbt_lineage_import_skipped_no_sink",
            manifest_path=str(manifest_path),
        )
        return {"asset_edges": 0, "column_mappings": 0}

    edges, asset_keys = collect_asset_lineage(manifest)
    if edges or asset_keys:
        resolution.provider.record_asset_edges(
            edges,
            asset_keys=asset_keys,
            metadata={"source": "dbt", "manifest_path": str(manifest_path)},
            tags={"tool": "dbt"},
        )

    column_mappings = 0
    if hasattr(resolution.provider, "record_column_lineage"):
        mappings = extract_column_lineage(manifest)
        if mappings:
            column_mappings = resolution.provider.record_column_lineage(mappings)

    logger.info(
        "dbt_lineage_import_completed",
        manifest_path=str(manifest_path),
        asset_edge_count=len(edges),
        asset_key_count=len(asset_keys),
        column_mapping_count=column_mappings,
        lineage_sink_name=resolution.name,
    )
    return {"asset_edges": len(edges), "column_mappings": column_mappings}
