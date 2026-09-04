"""Search result composition for Observatory.

Ranks services, Datasets, assets, tables, operations, quality checks, and
extensions against a free-text query and maps each hit to its route path
segment.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any
from urllib.parse import quote

from phlo_api.observatory_api.observatory_models import (
    ObservatoryAsset,
    ObservatoryDataset,
    ObservatoryExtension,
    ObservatoryOperation,
    ObservatoryQualityCheck,
    ObservatorySearchResult,
    ObservatoryService,
    ObservatoryTable,
)


def search_results(
    *,
    query: str,
    services: Sequence[ObservatoryService],
    assets: Sequence[ObservatoryAsset],
    tables: Sequence[ObservatoryTable],
    operations: Sequence[ObservatoryOperation],
    quality: Sequence[ObservatoryQualityCheck] = (),
    extensions: Sequence[ObservatoryExtension] = (),
    datasets: Sequence[ObservatoryDataset] = (),
) -> list[ObservatorySearchResult]:
    """Match services, Datasets, assets, tables, operations, quality checks, and extensions to a query.

    Performs a case-insensitive substring match over each record's identifying fields and
    returns every match in collection order; returns an empty list when the query is blank.
    """
    needle = query.strip().lower()
    if not needle:
        return []

    results: list[ObservatorySearchResult] = []

    for dataset in datasets:
        haystack = " ".join(
            [
                dataset.id,
                dataset.name,
                dataset.description or "",
                dataset.owner or "",
                *dataset.classifications,
                *dataset.kinds,
                *(ref.label for ref in dataset.source_refs),
            ]
        ).lower()
        if needle in haystack:
            metadata: dict[str, Any] = {
                "classifications": dataset.classifications,
                "candidate": dataset.candidate,
                "publication_state": dataset.publication_state,
                "readiness_state": dataset.readiness_state,
            }
            if dataset.owner:
                metadata["owner"] = dataset.owner
            results.append(
                ObservatorySearchResult(
                    id=f"dataset:{dataset.id}",
                    label=dataset.name,
                    kind="dataset",
                    summary=f"{dataset.publication_state} · {dataset.readiness_state}",
                    href=f"/datasets/{route_path_segment(dataset.id)}",
                    metadata=metadata,
                )
            )

    for service in services:
        haystack = " ".join([service.id, service.name, service.kind, service.status]).lower()
        if needle in haystack:
            results.append(
                ObservatorySearchResult(
                    id=f"service:{service.id}",
                    label=service.name,
                    kind="service",
                    summary=f"{service.kind} · {service.status}",
                    href="/services",
                )
            )

    for asset in assets:
        haystack = " ".join(
            [asset.id, asset.name, asset.group or "", asset.description or "", *asset.kinds]
        ).lower()
        if needle in haystack:
            results.append(
                ObservatorySearchResult(
                    id=f"asset:{asset.id}",
                    label=asset.name,
                    kind="asset",
                    summary=asset.description or asset.group,
                    href=f"/lineage?assetId={route_path_segment(asset.id)}",
                )
            )

    for table in tables:
        haystack = " ".join(
            [table.id, table.name, table.namespace or "", table.format or "", table.branch or ""]
        ).lower()
        if needle in haystack:
            results.append(
                ObservatorySearchResult(
                    id=f"table:{table.id}",
                    label=table.namespace + "." + table.name if table.namespace else table.name,
                    kind="table",
                    summary=f"{table.format or 'table'} · {table.branch or 'main'}",
                    href=f"/tables?tableId={route_path_segment(table.id)}",
                )
            )

    for operation in operations:
        haystack = " ".join(
            [operation.id, operation.name, operation.kind, operation.status]
        ).lower()
        if needle in haystack:
            results.append(
                ObservatorySearchResult(
                    id=f"operation:{operation.id}",
                    label=operation.name,
                    kind="operation",
                    summary=f"{operation.kind} · {operation.status}",
                    href=f"/operations?operationId={route_path_segment(operation.id)}",
                )
            )

    for check in quality:
        haystack = " ".join(
            [check.id, check.name, check.asset_id, check.status, check.severity or ""]
        ).lower()
        if needle in haystack:
            results.append(
                ObservatorySearchResult(
                    id=f"quality:{check.id}",
                    label=check.name,
                    kind="quality",
                    summary=f"{check.asset_id} · {check.status}",
                    href=f"/quality?checkId={route_path_segment(check.id)}",
                )
            )

    for extension in extensions:
        haystack = " ".join([extension.id, extension.name, extension.version or ""]).lower()
        if needle in haystack:
            results.append(
                ObservatorySearchResult(
                    id=f"extension:{extension.id}",
                    label=extension.name,
                    kind="extension",
                    summary=extension.settings_scope or extension.version,
                    href=f"/extensions/{route_path_segment(extension.id)}",
                )
            )

    return results


def route_path_segment(resource_id: str) -> str:
    """Percent-encode a resource id for safe inclusion in a route path segment."""
    return quote(resource_id, safe="")
