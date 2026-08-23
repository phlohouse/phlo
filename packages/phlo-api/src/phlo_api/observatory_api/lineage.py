"""Lineage API Router backed by the neutral lineage sink capability.

Provides endpoints for querying row-level and asset-level lineage data.
Enables data lineage tracking from ingestion through transformation to
consumption, supporting both fine-grained row journeys and coarse-grained
asset dependencies.

Key Endpoints:
    GET /rows/{id}: Get lineage info for a single row.
    GET /rows/{id}/ancestors: Get upstream row lineage.
    GET /rows/{id}/descendants: Get downstream row lineage.
    GET /rows/{id}/journey: Get full row journey (ancestors + descendants).
    GET /assets: Get asset lineage graph.

Environment Variables:
    PHLO_LINEAGE_SINK: Name of the lineage sink provider to use.

Example:
    Querying row lineage:

    .. code-block:: bash

        curl http://localhost:4000/api/lineage/rows/uuid-123/journey

"""

from __future__ import annotations

import os
from collections import deque
from collections.abc import Iterable
from typing import Any

from fastapi import APIRouter, Query
from pydantic import BaseModel

from phlo.capabilities import list_capabilities, resolve_capability
from phlo.capabilities.discovery import discover_capabilities
from phlo.capabilities.interfaces import LineageSink
from phlo.logging import get_logger

logger = get_logger(__name__)

router = APIRouter(tags=["lineage"])
_DEFAULT_LINEAGE_SINK_ENV = "PHLO_LINEAGE_SINK"


class RowLineageInfo(BaseModel):
    """Represent lineage metadata for a single row."""

    row_id: str
    table_name: str
    source_type: str
    parent_row_ids: list[str]
    created_at: str | None = None


class LineageJourney(BaseModel):
    """Represent current, upstream, and downstream lineage for a row."""

    current: RowLineageInfo | None
    ancestors: list[RowLineageInfo]
    descendants: list[RowLineageInfo]


class AssetNode(BaseModel):
    """Represent an asset node in the lineage graph."""

    name: str
    asset_type: str | None = None
    status: str | None = None
    description: str | None = None
    metadata: dict[str, Any] | None = None
    tags: dict[str, Any] | None = None


class AssetEdge(BaseModel):
    """Represent a directed relationship between two assets."""

    source: str
    target: str
    metadata: dict[str, Any] | None = None
    tags: dict[str, Any] | None = None


class AssetLineageGraph(BaseModel):
    """Represent the asset lineage graph payload."""

    assets: dict[str, AssetNode]
    edges: dict[str, list[str]]
    edge_details: list[AssetEdge]


def _resolve_lineage_sink() -> LineageSink:
    """Resolve the configured lineage sink capability."""
    discover_capabilities()
    name = os.environ.get(_DEFAULT_LINEAGE_SINK_ENV)
    resolution = resolve_capability("lineage_sink", name)
    if resolution is None:
        available = list_capabilities("lineage_sink")
        if name:
            raise RuntimeError(f"Lineage sink '{name}' not found. Available providers: {available}")
        if available:
            raise RuntimeError(
                "Multiple lineage_sink providers are installed. "
                f"Set {_DEFAULT_LINEAGE_SINK_ENV} to select one: {available}"
            )
        raise RuntimeError(
            "Lineage APIs require a lineage_sink capability. "
            "Install phlo-lineage or another provider."
        )
    return resolution.provider


def _row_to_lineage_info(row: dict[str, Any] | None) -> RowLineageInfo | None:
    """Convert lineage row payloads into the API model."""
    if row is None:
        return None
    created_at = row.get("created_at")
    return RowLineageInfo(
        row_id=str(row["row_id"]),
        table_name=str(row["table_name"]),
        source_type=str(row["source_type"]),
        parent_row_ids=[str(value) for value in row.get("parent_row_ids") or []],
        created_at=created_at
        if isinstance(created_at, str)
        else str(created_at)
        if created_at
        else None,
    )


def _rows_to_lineage_info(rows: Iterable[dict[str, Any]]) -> list[RowLineageInfo]:
    """Convert provider rows while dropping malformed or absent entries."""
    result: list[RowLineageInfo] = []
    for row in rows:
        info = _row_to_lineage_info(row)
        if info is not None:
            result.append(info)
    return result


def _coerce_asset_node(name: str, payload: Any) -> AssetNode:
    """Normalize provider-native asset payloads into the API model."""
    if isinstance(payload, dict):
        return AssetNode(
            name=name,
            asset_type=_optional_str(payload.get("asset_type")),
            status=_optional_str(payload.get("status")),
            description=_optional_str(payload.get("description")),
            metadata=_optional_dict(payload.get("metadata")),
            tags=_optional_dict(payload.get("tags")),
        )
    return AssetNode(
        name=name,
        asset_type=_optional_str(getattr(payload, "asset_type", None)),
        status=_optional_str(getattr(payload, "status", None)),
        description=_optional_str(getattr(payload, "description", None)),
        metadata=_optional_dict(getattr(payload, "metadata", None)),
        tags=_optional_dict(getattr(payload, "tags", None)),
    )


def _build_asset_graph(
    payload: Any,
) -> tuple[dict[str, AssetNode], dict[str, list[str]], list[AssetEdge]]:
    """Normalize provider-native asset graph payloads into the API model."""
    if isinstance(payload, dict):
        raw_assets = payload.get("assets", {})
        raw_edges = payload.get("edges", {})
        raw_edge_details = payload.get("edge_details", [])
    else:
        raw_assets = getattr(payload, "assets", {})
        raw_edges = getattr(payload, "edges", {})
        raw_edge_details = getattr(payload, "edge_details", [])

    assets = {
        str(name): _coerce_asset_node(str(name), node) for name, node in dict(raw_assets).items()
    }
    edges = {
        str(source): [str(target) for target in targets]
        for source, targets in dict(raw_edges).items()
    }

    edge_details: list[AssetEdge] = []
    if raw_edge_details:
        for edge in raw_edge_details:
            if isinstance(edge, dict):
                source = str(edge["source"])
                target = str(edge["target"])
                metadata = _optional_dict(edge.get("metadata"))
                tags = _optional_dict(edge.get("tags"))
            else:
                source = str(getattr(edge, "source"))
                target = str(getattr(edge, "target"))
                metadata = _optional_dict(getattr(edge, "metadata", None))
                tags = _optional_dict(getattr(edge, "tags", None))
            edge_details.append(
                AssetEdge(source=source, target=target, metadata=metadata, tags=tags)
            )
    else:
        for source, targets in edges.items():
            for target in targets:
                edge_details.append(AssetEdge(source=source, target=target))

    # A provider may reference an asset in edges without listing it under
    # "assets"; synthesize a bare node for each missing endpoint so every edge
    # in the returned graph points at an existing node.
    for edge in edge_details:
        assets.setdefault(edge.source, AssetNode(name=edge.source))
        assets.setdefault(edge.target, AssetNode(name=edge.target))

    return assets, edges, edge_details


def _filter_asset_graph(
    assets: dict[str, AssetNode],
    edges: dict[str, list[str]],
    edge_details: list[AssetEdge],
    *,
    asset_key: str,
    direction: str,
    depth: int | None,
) -> tuple[dict[str, AssetNode], dict[str, list[str]], list[AssetEdge]]:
    """Filter an asset graph around a focal asset."""
    reverse_edges: dict[str, list[str]] = {}
    for source, targets in edges.items():
        for target in targets:
            reverse_edges.setdefault(target, []).append(source)

    def _walk(
        start: str,
        adjacency: dict[str, list[str]],
        max_depth: int | None,
    ) -> set[str]:
        visited: set[str] = set()
        queue: deque[tuple[str, int]] = deque([(start, 0)])
        while queue:
            current, current_depth = queue.popleft()
            if current in visited:
                continue
            visited.add(current)
            if max_depth is not None and current_depth >= max_depth:
                continue
            for neighbor in adjacency.get(current, []):
                if neighbor not in visited:
                    queue.append((neighbor, current_depth + 1))
        visited.discard(start)
        return visited

    upstream = (
        _walk(asset_key, reverse_edges, depth) if direction in {"upstream", "both"} else set()
    )
    downstream = _walk(asset_key, edges, depth) if direction in {"downstream", "both"} else set()
    keep_assets = {asset_key} | upstream | downstream

    filtered_assets = {name: node for name, node in assets.items() if name in keep_assets}
    filtered_edges = {
        source: [target for target in targets if target in keep_assets]
        for source, targets in edges.items()
        if source in keep_assets
    }
    filtered_edge_details = [
        edge for edge in edge_details if edge.source in keep_assets and edge.target in keep_assets
    ]
    return filtered_assets, filtered_edges, filtered_edge_details


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    return str(value)


def _optional_dict(value: Any) -> dict[str, Any] | None:
    return value if isinstance(value, dict) else None


@router.get("/rows/{row_id}", response_model=RowLineageInfo | dict)
async def get_row_lineage(row_id: str) -> RowLineageInfo | dict[str, str]:
    """Get lineage info for a single row.

    Return an ``error`` dictionary instead of raising when the sink fails or
    the row is absent from the lineage store.
    """
    try:
        journey = _resolve_lineage_sink().get_row_journey(row_id=row_id, depth=1)
        current = _row_to_lineage_info(journey.get("current"))
        if current is None:
            return {"error": f"Row {row_id} not found in lineage store"}
        return current
    except RuntimeError as exc:
        return {"error": str(exc)}
    except Exception as exc:
        logger.exception("lineage_row_lookup_failed")
        return {"error": str(exc)}


@router.get("/rows/{row_id}/ancestors", response_model=list[RowLineageInfo] | dict)
async def get_row_ancestors(
    row_id: str,
    max_depth: int = Query(default=10, le=50),
) -> list[RowLineageInfo] | dict[str, str]:
    """Get ancestor rows recursively upstream.

    Traverses up to ``max_depth`` levels. Return an ``error`` dictionary
    instead of raising when the sink fails.
    """
    try:
        journey = _resolve_lineage_sink().get_row_journey(row_id=row_id, depth=max_depth)
        return _rows_to_lineage_info(journey.get("ancestors", []))
    except RuntimeError as exc:
        return {"error": str(exc)}
    except Exception as exc:
        logger.exception("lineage_row_ancestors_failed")
        return {"error": str(exc)}


@router.get("/rows/{row_id}/descendants", response_model=list[RowLineageInfo] | dict)
async def get_row_descendants(
    row_id: str,
    max_depth: int = Query(default=10, le=50),
) -> list[RowLineageInfo] | dict[str, str]:
    """Get descendant rows recursively downstream.

    Traverses up to ``max_depth`` levels. Return an ``error`` dictionary
    instead of raising when the sink fails.
    """
    try:
        journey = _resolve_lineage_sink().get_row_journey(row_id=row_id, depth=max_depth)
        return _rows_to_lineage_info(journey.get("descendants", []))
    except RuntimeError as exc:
        return {"error": str(exc)}
    except Exception as exc:
        logger.exception("lineage_row_descendants_failed")
        return {"error": str(exc)}


@router.get("/rows/{row_id}/journey", response_model=LineageJourney | dict)
async def get_row_journey(row_id: str) -> LineageJourney | dict[str, str]:
    """Get full lineage journey for a row (current, ancestors, descendants).

    Return an ``error`` dictionary instead of raising when the sink fails.
    """
    try:
        journey = _resolve_lineage_sink().get_row_journey(row_id=row_id, depth=10)
        return LineageJourney(
            current=_row_to_lineage_info(journey.get("current")),
            ancestors=_rows_to_lineage_info(journey.get("ancestors", [])),
            descendants=_rows_to_lineage_info(journey.get("descendants", [])),
        )
    except RuntimeError as exc:
        return {"error": str(exc)}
    except Exception as exc:
        logger.exception("lineage_row_journey_failed")
        return {"error": str(exc)}


@router.get("/assets", response_model=AssetLineageGraph | dict)
async def get_asset_lineage_graph(
    asset_key: str | None = Query(default=None),
    direction: str = Query(default="both", pattern="^(upstream|downstream|both)$"),
    depth: int | None = Query(default=None, ge=1, le=50),
) -> AssetLineageGraph | dict[str, str]:
    """Get the asset lineage graph.

    Return the full graph, or a subgraph filtered around ``asset_key`` in
    the requested ``direction`` up to ``depth`` levels. Errors are returned
    as an ``error`` dictionary instead of raised.
    """

    try:
        assets, edges, edge_details = _build_asset_graph(_resolve_lineage_sink().get_asset_graph())
        if asset_key:
            if asset_key not in assets:
                return {"error": f"Asset {asset_key} not found in lineage store"}
            assets, edges, edge_details = _filter_asset_graph(
                assets,
                edges,
                edge_details,
                asset_key=asset_key,
                direction=direction,
                depth=depth,
            )
        return AssetLineageGraph(assets=assets, edges=edges, edge_details=edge_details)
    except RuntimeError as exc:
        return {"error": str(exc)}
    except Exception as exc:
        logger.exception("lineage_asset_graph_failed")
        return {"error": str(exc)}
