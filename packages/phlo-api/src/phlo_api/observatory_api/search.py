"""Search API Router.

Endpoint to aggregate searchable entities for the command palette.
Combines data from Dagster (assets) and Iceberg/Trino (tables, columns).

This module builds a unified search index that enables quick navigation
and discovery across the data platform, including Dagster assets,
Iceberg tables, and table columns.

Key Endpoints:
    GET /index: Build the observability search index.

Example:
    Building search index:

    .. code-block:: bash

        curl "http://localhost:4000/api/search/index?include_columns=true"

"""

from __future__ import annotations

import asyncio
from datetime import datetime

from fastapi import APIRouter, Query
from pydantic import BaseModel

from phlo.logging import get_logger
from phlo_api.observatory_api.dagster import get_assets
from phlo_api.observatory_api.iceberg import get_table_schema, get_tables
from phlo_api.observatory_api.trino import resolve_default_catalog

logger = get_logger(__name__)

router = APIRouter(tags=["search"])


# --- Pydantic Models ---


class SearchableAsset(BaseModel):
    """Represent a searchable Dagster asset."""

    id: str
    key_path: str
    group_name: str | None = None
    compute_kind: str | None = None


class SearchableTable(BaseModel):
    """Represent a searchable table in the catalog."""

    catalog: str
    schema_name: str
    name: str
    full_name: str
    layer: str


class SearchableColumn(BaseModel):
    """Represent a searchable table column."""

    table_name: str
    table_schema: str
    name: str
    type: str


class SearchIndex(BaseModel):
    """Represent the aggregated observability search index."""

    assets: list[SearchableAsset]
    tables: list[SearchableTable]
    columns: list[SearchableColumn]
    last_updated: str


# --- API Endpoints ---


@router.get("/index", response_model=SearchIndex | dict)
async def get_search_index(
    dagster_url: str | None = None,
    trino_url: str | None = None,
    catalog: str | None = None,
    branch: str | None = None,
    include_columns: bool = Query(default=True),
) -> SearchIndex | dict[str, str]:
    """Build the observability search index.

    Aggregates searchable entities from Dagster (assets) and Trino (tables,
    columns); column metadata is included unless ``include_columns`` is False.
    URL and catalog arguments override settings-derived defaults. Exceptions are
    caught and returned in the response rather than raised.
    """
    try:
        effective_catalog = catalog or resolve_default_catalog()
        assets_result, tables_result = await asyncio.gather(
            get_assets(dagster_url),
            get_tables(branch, effective_catalog, None, trino_url),
        )

        if isinstance(assets_result, dict) and "error" in assets_result:
            return {"error": f"Failed to fetch assets: {assets_result['error']}"}
        if isinstance(tables_result, dict) and "error" in tables_result:
            return {"error": f"Failed to fetch tables: {tables_result['error']}"}
        if not isinstance(assets_result, list) or not isinstance(tables_result, list):
            return {"error": "Failed to fetch search index data."}

        assets = [
            SearchableAsset(
                id=asset.id,
                key_path=asset.key_path,
                group_name=asset.group_name,
                compute_kind=asset.compute_kind,
            )
            for asset in assets_result
        ]

        tables = [
            SearchableTable(
                catalog=table.catalog,
                schema_name=table.schema_name,
                name=table.name,
                full_name=table.full_name,
                layer=table.layer,
            )
            for table in tables_result
        ]

        # Fetch columns if requested
        columns: list[SearchableColumn] = []
        if include_columns and tables:
            # Limit to first 20 tables to avoid overwhelming the system
            tables_to_fetch = tables[:20]

            for table in tables_to_fetch:
                try:
                    schema_result = await get_table_schema(
                        table.name,
                        table.schema_name,
                        None,
                        effective_catalog,
                        trino_url,
                    )
                    if isinstance(schema_result, list):
                        for col in schema_result:
                            columns.append(
                                SearchableColumn(
                                    table_name=table.name,
                                    table_schema=table.schema_name,
                                    name=col.name,
                                    type=col.type,
                                )
                            )
                except Exception:
                    pass  # Skip tables that fail

        return SearchIndex(
            assets=assets,
            tables=tables,
            columns=columns,
            last_updated=datetime.now().isoformat(),
        )
    except Exception as e:
        logger.exception("Failed to build search index")
        return {"error": str(e)}
