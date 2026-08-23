"""Tests for logical references: unresolved refs stay stable, resolved
refs map to physical catalog.schema.table names via the registry."""

from __future__ import annotations

from phlo.capabilities.registry import CapabilityRegistry
from phlo.capabilities.specs import AssetSpec
from phlo.references import LogicalRelation, ref, source


def test_unresolved_logical_ref_is_stable() -> None:
    relation = ref("fct_orders", discover=False)

    assert relation.asset_key == "fct_orders"
    assert relation.is_resolved is False
    assert str(relation) == "fct_orders"
    assert repr(relation) == "LogicalRelation(asset_key='fct_orders')"


def test_ref_resolves_dbt_model_metadata_from_registry() -> None:
    registry = CapabilityRegistry()
    registry.register(
        "asset",
        AssetSpec(
            key="fct_orders",
            group="gold",
            description=None,
            kinds={"dbt", "table"},
            metadata={
                "catalog": "iceberg",
                "schema": "analytics",
                "table": "fct_orders_v2",
                "relation": '"iceberg"."analytics"."fct_orders_v2"',
            },
        ),
    )

    relation = ref("fct_orders", registry=registry, discover=False)

    assert relation == LogicalRelation(
        asset_key="fct_orders",
        catalog="iceberg",
        schema="analytics",
        table="fct_orders_v2",
        relation='"iceberg"."analytics"."fct_orders_v2"',
        metadata={
            "catalog": "iceberg",
            "schema": "analytics",
            "table": "fct_orders_v2",
            "relation": '"iceberg"."analytics"."fct_orders_v2"',
        },
    )
    assert relation.is_resolved is True
    assert str(relation) == '"iceberg"."analytics"."fct_orders_v2"'


def test_source_uses_dbt_source_asset_key_mapping() -> None:
    registry = CapabilityRegistry()
    registry.register(
        "asset",
        AssetSpec(
            key="raw.orders",
            group="raw",
            description=None,
            kinds={"dbt", "table"},
            metadata={"database": "iceberg", "namespace": "raw", "table_name": "orders"},
        ),
    )

    relation = source("raw", "orders", registry=registry, discover=False)

    assert relation.asset_key == "raw.orders"
    assert relation.catalog == "iceberg"
    assert relation.schema == "raw"
    assert relation.table == "orders"
    assert relation.render() == '"iceberg"."raw"."orders"'


def test_render_quotes_catalog_schema_table_identifiers() -> None:
    relation = LogicalRelation(
        asset_key="orders",
        catalog="iceberg",
        schema="sales data",
        table='orders"2026',
    )

    assert relation.render() == '"iceberg"."sales data"."orders""2026"'


def test_top_level_ref_and_source_are_lazy_exports() -> None:
    import phlo

    assert "ref" in phlo.__all__
    assert "source" in phlo.__all__
    assert phlo.ref("orders", discover=False) == ref("orders", discover=False)
    assert phlo.source("raw", "orders", discover=False) == source("raw", "orders", discover=False)
