"""Tests for lineage API capability resolution.

Endpoints are exercised against a fake lineage sink whose payloads are
distinctive and independent of the request arguments, so the assertions pin
that responses are mapped from the resolved capability rather than echoed
back from the request.
"""

from __future__ import annotations

import asyncio

import pytest

from phlo_api.observatory_api import lineage


class _FakeLineageSink:
    """Deterministic sink with distinctive payloads and recorded requests."""

    def __init__(self) -> None:
        self.row_journey_requests: list[tuple[str, int]] = []
        self.asset_graph_requests = 0

    def get_row_journey(self, *, row_id: str, depth: int = 10):
        self.row_journey_requests.append((row_id, depth))
        # Store contents deliberately ignore the requested row_id/depth, so any
        # assertion below proves the handler mapped sink data, not the request.
        return {
            "current": {
                "row_id": "row-current-77",
                "table_name": "raw.orders",
                "source_type": "fivetran_sync",
                "parent_row_ids": ["parent-alpha", "parent-beta"],
                "created_at": "2026-03-07T12:00:00+00:00",
            },
            "ancestors": [
                {
                    "row_id": "row-upstream-1",
                    "table_name": "raw.customers",
                    "source_type": "cdc_stream",
                    "parent_row_ids": [],
                    "created_at": "2026-03-06T09:15:00+00:00",
                },
                {
                    "row_id": "row-upstream-2",
                    "table_name": "raw.products",
                    "source_type": "batch_load",
                    "parent_row_ids": ["row-upstream-1"],
                    "created_at": "2026-03-05T22:41:00+00:00",
                },
            ],
            "descendants": [
                {
                    "row_id": "row-downstream-9",
                    "table_name": "marts.fulfillment",
                    "source_type": "dbt_model",
                    "parent_row_ids": ["row-current-77"],
                    "created_at": None,
                },
            ],
        }

    def get_asset_graph(self):
        self.asset_graph_requests += 1
        return {
            "assets": {
                "raw_orders": {
                    "asset_type": "ingestion",
                    "status": "healthy",
                    "description": "Fivetran landing table for orders",
                    "metadata": {"connector": "fivetran", "row_count": 1500},
                    "tags": {"tier": "bronze"},
                },
                "stg_orders": {
                    "asset_type": "transform",
                    "status": "degraded",
                    "description": "dbt staging model for orders",
                    "metadata": {"job": "nightly_dbt"},
                    "tags": {"tier": "silver"},
                },
                "mart_fulfillment": {
                    "asset_type": "export",
                    "status": "healthy",
                },
            },
            "edges": {
                "raw_orders": ["stg_orders"],
                "stg_orders": ["mart_fulfillment"],
            },
            "edge_details": [
                {
                    "source": "raw_orders",
                    "target": "stg_orders",
                    "metadata": {"transformation": "sql"},
                    "tags": {"contract": "v2"},
                },
            ],
        }


def test_get_row_lineage_maps_the_sink_payload(monkeypatch) -> None:
    sink = _FakeLineageSink()
    monkeypatch.setattr(lineage, "_resolve_lineage_sink", lambda: sink)

    payload = asyncio.run(lineage.get_row_lineage("row-requested"))

    assert isinstance(payload, lineage.RowLineageInfo)
    assert sink.row_journey_requests == [("row-requested", 1)]
    assert payload.model_dump() == {
        "row_id": "row-current-77",
        "table_name": "raw.orders",
        "source_type": "fivetran_sync",
        "parent_row_ids": ["parent-alpha", "parent-beta"],
        "created_at": "2026-03-07T12:00:00+00:00",
    }


def test_get_row_journey_maps_current_ancestors_and_descendants(monkeypatch) -> None:
    sink = _FakeLineageSink()
    monkeypatch.setattr(lineage, "_resolve_lineage_sink", lambda: sink)

    payload = asyncio.run(lineage.get_row_journey("row-requested"))

    assert isinstance(payload, lineage.LineageJourney)
    assert sink.row_journey_requests == [("row-requested", 10)]
    assert payload.model_dump() == {
        "current": {
            "row_id": "row-current-77",
            "table_name": "raw.orders",
            "source_type": "fivetran_sync",
            "parent_row_ids": ["parent-alpha", "parent-beta"],
            "created_at": "2026-03-07T12:00:00+00:00",
        },
        "ancestors": [
            {
                "row_id": "row-upstream-1",
                "table_name": "raw.customers",
                "source_type": "cdc_stream",
                "parent_row_ids": [],
                "created_at": "2026-03-06T09:15:00+00:00",
            },
            {
                "row_id": "row-upstream-2",
                "table_name": "raw.products",
                "source_type": "batch_load",
                "parent_row_ids": ["row-upstream-1"],
                "created_at": "2026-03-05T22:41:00+00:00",
            },
        ],
        "descendants": [
            {
                "row_id": "row-downstream-9",
                "table_name": "marts.fulfillment",
                "source_type": "dbt_model",
                "parent_row_ids": ["row-current-77"],
                "created_at": None,
            },
        ],
    }


def test_get_asset_lineage_graph_maps_nodes_edges_and_edge_details(monkeypatch) -> None:
    sink = _FakeLineageSink()
    monkeypatch.setattr(lineage, "_resolve_lineage_sink", lambda: sink)

    payload = asyncio.run(
        lineage.get_asset_lineage_graph(asset_key=None, direction="both", depth=None)
    )

    assert isinstance(payload, lineage.AssetLineageGraph)
    assert sink.asset_graph_requests == 1
    assert payload.model_dump() == {
        "assets": {
            "raw_orders": {
                "name": "raw_orders",
                "asset_type": "ingestion",
                "status": "healthy",
                "description": "Fivetran landing table for orders",
                "metadata": {"connector": "fivetran", "row_count": 1500},
                "tags": {"tier": "bronze"},
            },
            "stg_orders": {
                "name": "stg_orders",
                "asset_type": "transform",
                "status": "degraded",
                "description": "dbt staging model for orders",
                "metadata": {"job": "nightly_dbt"},
                "tags": {"tier": "silver"},
            },
            "mart_fulfillment": {
                "name": "mart_fulfillment",
                "asset_type": "export",
                "status": "healthy",
                "description": None,
                "metadata": None,
                "tags": None,
            },
        },
        "edges": {
            "raw_orders": ["stg_orders"],
            "stg_orders": ["mart_fulfillment"],
        },
        "edge_details": [
            {
                "source": "raw_orders",
                "target": "stg_orders",
                "metadata": {"transformation": "sql"},
                "tags": {"contract": "v2"},
            },
        ],
    }


class _ExplodingLineageSink:
    """Sink whose every callable raises, to prove handlers fail soft."""

    def get_row_journey(self, *, row_id: str, depth: int = 10):
        raise RuntimeError("lineage sink unavailable")

    def get_asset_graph(self):
        raise RuntimeError("lineage sink unavailable")


_ENDPOINT_CALLS = [
    lambda _: lineage.get_row_lineage("row-requested"),
    lambda _: lineage.get_row_ancestors("row-requested", max_depth=3),
    lambda _: lineage.get_row_descendants("row-requested", max_depth=3),
    lambda _: lineage.get_row_journey("row-requested"),
    lambda _: lineage.get_asset_lineage_graph(asset_key=None, direction="both", depth=None),
]


@pytest.mark.parametrize(
    "call",
    _ENDPOINT_CALLS,
    ids=["row", "ancestors", "descendants", "journey", "assets"],
)
def test_lineage_endpoints_fail_soft_when_the_sink_raises(call, monkeypatch) -> None:
    """A broken sink degrades every lineage endpoint to an error payload."""
    monkeypatch.setattr(lineage, "_resolve_lineage_sink", lambda: _ExplodingLineageSink())

    payload = asyncio.run(call(None))

    assert payload == {"error": "lineage sink unavailable"}
