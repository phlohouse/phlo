"""Tests dbt-test to Dagster asset-check translation.

Same-type tests and relationships attached to one model must map to distinct,
stable asset check names so specs and run results refer to the same identity.
"""

from __future__ import annotations

from phlo_dbt.asset_checks import (
    dbt_asset_check_index,
    dbt_asset_check_names,
    dbt_asset_check_specs,
    extract_dbt_asset_checks,
)
from phlo_dbt.translator import DbtSpecTranslator


def test_dbt_test_specs_and_results_share_unique_attached_node_identity() -> None:
    """Same-type tests and relationships use distinct names on their owning model."""
    manifest = {
        "nodes": {
            "model.retail.sales_facts": {"name": "sales_facts", "resource_type": "model"},
            "model.retail.product_dimension": {
                "name": "product_dimension",
                "resource_type": "model",
            },
            "test.retail.not_null_sales_facts_line_id": {
                "name": "not_null_sales_facts_line_id",
                "resource_type": "test",
                "test_metadata": {"name": "not_null"},
                "depends_on": {"nodes": ["model.retail.sales_facts"]},
            },
            "test.retail.not_null_sales_facts_net_amount": {
                "name": "not_null_sales_facts_net_amount",
                "resource_type": "test",
                "test_metadata": {"name": "not_null"},
                "depends_on": {"nodes": ["model.retail.sales_facts"]},
            },
            "test.retail.relationships_sales_facts_product_id": {
                "name": "relationships_sales_facts_product_id",
                "resource_type": "test",
                "test_metadata": {"name": "relationships"},
                "attached_node": "model.retail.sales_facts",
                "depends_on": {
                    "nodes": ["model.retail.product_dimension", "model.retail.sales_facts"]
                },
            },
        }
    }
    run_results = {
        "results": [
            {"unique_id": unique_id, "status": "pass"}
            for unique_id in (
                "test.retail.not_null_sales_facts_line_id",
                "test.retail.not_null_sales_facts_net_amount",
                "test.retail.relationships_sales_facts_product_id",
            )
        ]
    }

    translator = DbtSpecTranslator()
    specs = dbt_asset_check_specs(manifest, translator=translator)
    results = extract_dbt_asset_checks(
        run_results, manifest, translator=translator, partition_key="2025-01-01"
    )

    expected_names = {
        "dbt__not_null__sales_facts__not_null_sales_facts_line_id",
        "dbt__not_null__sales_facts__not_null_sales_facts_net_amount",
        "dbt__relationships__sales_facts__relationships_sales_facts_product_id",
    }
    assert {(spec.asset_key, spec.name) for spec in specs} == {
        ("sales_facts", name) for name in expected_names
    }
    assert {(result.asset_key, result.check_name) for result in results} == {
        ("sales_facts", name) for name in expected_names
    }
    assert dbt_asset_check_names(manifest, asset_key="sales_facts", translator=translator) == [
        "not_null_sales_facts_line_id",
        "not_null_sales_facts_net_amount",
        "relationships_sales_facts_product_id",
    ]
    assert (
        dbt_asset_check_names(manifest, asset_key="product_dimension", translator=translator) == []
    )


def test_check_index_keeps_selector_duplicates_and_spec_order() -> None:
    """Invalid owners are skipped; fallback ownership and spec dedup stay independent."""
    manifest = {
        "nodes": {
            "model.p.a": {"resource_type": "model", "name": "a"},
            "seed.p.b": {"resource_type": "seed", "name": "b"},
            "snapshot.p.c": {"resource_type": "snapshot", "name": "c"},
            "test.p.first": {
                "name": " repeated ",
                "test_metadata": {"name": "unique"},
                "depends_on": {"nodes": ["seed.p.b", "model.p.a"]},
            },
            "test.p.seed": {
                "name": "seed_test",
                "tags": ["warn"],
                "test_metadata": {"name": "unique"},
                "depends_on": {"nodes": ["seed.p.b"]},
            },
            "test.p.duplicate": {
                "name": " repeated ",
                "tags": ["warn"],
                "test_metadata": {"name": "unique"},
                "attached_node": "model.p.a",
            },
            "test.p.unnamed": {"attached_node": "snapshot.p.c"},
            "test.p.empty": {"name": "", "attached_node": "model.p.a"},
            "test.p.whitespace": {"name": " ", "attached_node": "seed.p.b"},
            "test.p.fallback": {
                "name": "fallback",
                "attached_node": "model.p.missing",
                "depends_on": {"nodes": ["snapshot.p.c"]},
            },
            "test.p.missing": {"name": "missing", "attached_node": "model.p.missing"},
            "test.p.invalid": [],
            "test.p.bad_deps": {"depends_on": []},
        }
    }
    translator = DbtSpecTranslator(key_prefix="p")
    specs, names = dbt_asset_check_index(manifest, translator=translator)
    assert names == {
        "p.a": (" repeated ", " repeated "),
        "p.b": ("seed_test", " "),
        "p.c": ("fallback",),
    }
    assert [spec.asset_key for spec in specs] == ["p.a", "p.b", "p.c", "p.a", "p.b", "p.c"]
    assert specs[0].blocking is True
    assert specs[1].blocking is False
    for key, selectors in names.items():
        assert list(selectors) == dbt_asset_check_names(
            manifest, asset_key=key, translator=translator
        )
    assert specs == dbt_asset_check_specs(manifest, translator=translator)
