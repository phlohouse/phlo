"""Tests for lineage API capability resolution.

Endpoints are exercised against a stubbed lineage sink to prove that row and
asset lineage responses are always built from the resolved capability rather
than any built-in data source.
"""

from __future__ import annotations

import asyncio

from phlo_api.observatory_api import lineage


class _LineageSink:
    def get_row_journey(self, *, row_id: str, depth: int = 10):
        return {
            "current": {
                "row_id": row_id,
                "table_name": "raw.events",
                "source_type": "ingestion",
                "parent_row_ids": ["parent-1"],
                "created_at": "2026-03-07T12:00:00+00:00",
            },
            "ancestors": [],
            "descendants": [],
        }

    def get_asset_graph(self):
        return {
            "assets": {
                "raw_events": {
                    "asset_type": "ingestion",
                    "status": "healthy",
                },
                "stg_events": {
                    "asset_type": "transform",
                    "status": "healthy",
                },
            },
            "edges": {"raw_events": ["stg_events"]},
        }


def test_get_row_lineage_uses_lineage_sink(monkeypatch) -> None:
    """Row lookup should read from the resolved lineage capability."""
    monkeypatch.setattr(lineage, "_resolve_lineage_sink", lambda: _LineageSink())

    payload = asyncio.run(lineage.get_row_lineage("row-1"))

    assert isinstance(payload, lineage.RowLineageInfo)
    assert payload.row_id == "row-1"
    assert payload.table_name == "raw.events"


def test_get_asset_lineage_graph_uses_lineage_sink(monkeypatch) -> None:
    """Asset graph responses should be built from the lineage capability."""
    monkeypatch.setattr(lineage, "_resolve_lineage_sink", lambda: _LineageSink())

    payload = asyncio.run(
        lineage.get_asset_lineage_graph(asset_key=None, direction="both", depth=None)
    )

    assert isinstance(payload, lineage.AssetLineageGraph)
    assert payload.edges == {"raw_events": ["stg_events"]}
    assert "raw_events" in payload.assets
