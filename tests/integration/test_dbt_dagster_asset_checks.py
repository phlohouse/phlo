"""Cross-provider dbt-to-Dagster asset-check integration regression.

A multi-model dbt project must emit only the checks owned by the selected
asset, and check failure must fail the Dagster run. Parametrized over pass
and fail outcomes.
"""

from __future__ import annotations

import json
from typing import cast

import dagster as dg
import pytest
from phlo_dagster.adapter import DagsterOrchestratorAdapter
from phlo_dbt.assets import build_dbt_asset_specs

from phlo.operations.transformation import TransformationResult

pytestmark = pytest.mark.integration


@pytest.mark.parametrize(
    ("test_status", "transform_status", "expected_run_success"),
    [("pass", "success", True), ("fail", "failure", False)],
)
def test_dbt_asset_runner_emits_dagster_check_events(
    monkeypatch,
    tmp_path,
    test_status: str,
    transform_status: str,
    expected_run_success: bool,
) -> None:
    """A multi-model provider emits only checks owned by the selected asset."""
    project_path = tmp_path / "dbt"
    target_path = project_path / "target"
    target_path.mkdir(parents=True)
    (project_path / "dbt_project.yml").write_text("name: test\nversion: '1.0'\n", encoding="utf-8")
    manifest = {
        "nodes": {
            "model.phlo.product_dimension": {
                "name": "product_dimension",
                "resource_type": "model",
            },
            "model.phlo.sales_facts": {"name": "sales_facts", "resource_type": "model"},
            "test.phlo.not_null_product_dimension_sku": {
                "name": "not_null_product_dimension_sku",
                "resource_type": "test",
                "test_metadata": {"name": "not_null"},
                "depends_on": {"nodes": ["model.phlo.product_dimension"]},
            },
            "test.phlo.not_null_sales_facts_line_id": {
                "name": "not_null_sales_facts_line_id",
                "resource_type": "test",
                "test_metadata": {"name": "not_null"},
                "depends_on": {"nodes": ["model.phlo.sales_facts"]},
            },
            "test.phlo.not_null_sales_facts_net_amount": {
                "name": "not_null_sales_facts_net_amount",
                "resource_type": "test",
                "test_metadata": {"name": "not_null"},
                "depends_on": {"nodes": ["model.phlo.sales_facts"]},
            },
            "test.phlo.relationships_sales_facts_product_id": {
                "name": "relationships_sales_facts_product_id",
                "resource_type": "test",
                "test_metadata": {"name": "relationships"},
                "attached_node": "model.phlo.sales_facts",
                "depends_on": {"nodes": ["model.phlo.product_dimension", "model.phlo.sales_facts"]},
            },
        },
        "sources": {},
    }
    (target_path / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    (target_path / "run_results.json").write_text(
        json.dumps(
            {
                "results": [
                    {"unique_id": unique_id, "status": test_status, "failures": 3}
                    for unique_id in (
                        "test.phlo.not_null_product_dimension_sku",
                        "test.phlo.relationships_sales_facts_product_id",
                    )
                ]
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "phlo_dbt.assets.get_settings",
        lambda: type(
            "Settings",
            (),
            {
                "dbt_project_path": project_path,
                "dbt_project_paths": [project_path],
                "dbt_namespaced_asset_keys": False,
                "dbt_profiles_path_for": lambda _s, p: p / "profiles",
            },
        )(),
    )
    monkeypatch.setattr("phlo_dbt.assets.ensure_dbt_profile", lambda *_args, **_kwargs: None)
    monkeypatch.setattr("phlo_dbt.assets.ensure_dbt_manifest", lambda *_args, **_kwargs: True)

    class FakeTransformer:
        def __init__(self, **_kwargs) -> None:
            self.build_run_results = None

        def run_transform(self, **kwargs) -> TransformationResult:
            assert kwargs["parameters"] == {
                "select": ["product_dimension", "not_null_product_dimension_sku"],
                "indirect_selection": "empty",
            }
            self.build_run_results = json.loads(
                (target_path / "run_results.json").read_text(encoding="utf-8")
            )
            (target_path / "run_results.json").write_text(
                json.dumps(
                    {
                        "args": {"which": "generate"},
                        "results": [
                            {
                                "unique_id": "test.phlo.not_null_product_dimension_sku",
                                "status": "pass",
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            return TransformationResult(
                status=transform_status,
                models_built=1 if transform_status == "success" else 0,
                models_failed=0 if transform_status == "success" else 1,
                tests_passed=1 if test_status == "pass" else 0,
                tests_failed=1 if test_status == "fail" else 0,
            )

    monkeypatch.setattr("phlo_dbt.assets.DbtTransformer", FakeTransformer)
    spec = next(spec for spec in build_dbt_asset_specs() if spec.key == "product_dimension")
    definitions = DagsterOrchestratorAdapter().build_definitions(
        assets=[spec], checks=[], resources=[]
    )
    result = definitions.resolve_implicit_global_asset_job_def().execute_in_process(
        partition_key="2025-01-01", raise_on_error=False
    )

    assert result.success is expected_run_success
    evaluations = [
        cast(dg.AssetCheckEvaluation, event.event_specific_data)
        for event in result.all_events
        if event.event_type == dg.DagsterEventType.ASSET_CHECK_EVALUATION
    ]
    assert len(evaluations) == 1
    assert all(
        evaluation.asset_key == dg.AssetKey("product_dimension") for evaluation in evaluations
    )
    assert all(evaluation.passed is (test_status == "pass") for evaluation in evaluations)
    assert all(
        evaluation.metadata["partition_key"].value == "2025-01-01" for evaluation in evaluations
    )
    assert json.loads((target_path / "run_results.json").read_text(encoding="utf-8"))["args"] == {
        "which": "generate"
    }
