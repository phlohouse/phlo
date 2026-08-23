"""Tests for phlo-lineage resource provider.

Verifies lineage sink capability registration and that asset edges, column
lineage, and row-journey reads are forwarded to the configured store.
"""

from unittest.mock import Mock, patch

from phlo_lineage.resource_provider import LineageResourceProvider


def test_resource_provider_registers_lineage_sink_spec():
    """Resource provider exposes a lineage sink capability."""
    provider = LineageResourceProvider()

    specs = provider.get_lineage_sinks()

    assert len(specs) == 1
    assert specs[0].name == "phlo-lineage"
    assert specs[0].provider is not None


def test_lineage_sink_records_asset_edges():
    """Lineage sink forwards asset edge persistence to the store."""
    sink = LineageResourceProvider().get_lineage_sinks()[0].provider
    store = Mock()
    store.record_asset_edges.return_value = 1

    with (
        patch("phlo_lineage.lineage_sink.LineageStore", return_value=store),
        patch(
            "phlo_lineage.lineage_sink.resolve_lineage_db_url_with_postgres_fallback",
            return_value="postgresql://lineage",
        ),
    ):
        persisted = sink.record_asset_edges([("raw.orders", "silver.orders")])

    assert persisted == 1
    store.record_asset_edges.assert_called_once()


def test_lineage_sink_gets_row_journey():
    """Lineage sink returns the normalized row journey payload."""
    sink = LineageResourceProvider().get_lineage_sinks()[0].provider
    store = Mock()
    store.get_row.return_value = {"row_id": "abc"}
    store.get_ancestors.return_value = [{"row_id": "parent"}]
    store.get_descendants.return_value = [{"row_id": "child"}]

    with (
        patch("phlo_lineage.lineage_sink.LineageStore", return_value=store),
        patch(
            "phlo_lineage.lineage_sink.resolve_lineage_db_url_with_postgres_fallback",
            return_value="postgresql://lineage",
        ),
    ):
        journey = sink.get_row_journey(row_id="abc", depth=3)

    assert journey == {
        "current": {"row_id": "abc"},
        "ancestors": [{"row_id": "parent"}],
        "descendants": [{"row_id": "child"}],
    }


def test_lineage_sink_records_column_lineage():
    """Lineage sink forwards column lineage persistence to the store."""
    sink = LineageResourceProvider().get_lineage_sinks()[0].provider
    store = Mock()
    store.record_column_lineage.return_value = 1

    with (
        patch("phlo_lineage.lineage_sink.LineageStore", return_value=store),
        patch(
            "phlo_lineage.lineage_sink.resolve_lineage_db_url_with_postgres_fallback",
            return_value="postgresql://lineage",
        ),
    ):
        persisted = sink.record_column_lineage(
            [
                {
                    "source_asset": "silver.orders",
                    "source_column": "order_id",
                    "target_asset": "gold.orders",
                    "target_column": "order_id",
                    "source_type": "dbt_heuristic",
                }
            ]
        )

    assert persisted == 1
    store.record_column_lineage.assert_called_once()
