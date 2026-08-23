"""Tests dbt-test to Dagster asset-check translation.

Same-type tests and relationships attached to one model must map to distinct,
stable asset check names so specs and run results refer to the same identity.
"""

from __future__ import annotations

from phlo_dbt.asset_checks import (
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
