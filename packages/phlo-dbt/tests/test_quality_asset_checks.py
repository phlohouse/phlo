"""Integration tests for Pandera contract checks over parquet payloads.

Validates the pass/fail evaluation of DemoSchema against real parquet files
and how evaluations convert into dbt-style asset check results with failure
metadata (counts, sample, schema).
"""

from __future__ import annotations

from datetime import datetime

import pandas as pd
import pytest
from phlo_dbt.translator import DbtSpecTranslator

from phlo_dbt.asset_checks import extract_dbt_asset_checks
from phlo_pandera.pandera_asset_checks import (
    evaluate_pandera_contract_parquet,
    pandera_contract_asset_check_result,
)
from phlo_pandera.schemas.base import PhloSchema

pytestmark = pytest.mark.integration


class DemoSchema(PhloSchema):
    """Minimal schema for validating test parquet payloads."""

    id: int
    created_at: datetime


def test_pandera_contract_asset_check_fails_for_invalid_parquet(tmp_path) -> None:
    """Verify parquet contract check fails and reports metadata for invalid data."""
    parquet_path = tmp_path / "data.parquet"
    df = pd.DataFrame([{"id": 1, "created_at": "not-a-date"}])
    df.to_parquet(parquet_path)

    evaluation = evaluate_pandera_contract_parquet(parquet_path, schema_class=DemoSchema)
    assert evaluation.passed is False
    assert evaluation.failed_count >= 1
    assert evaluation.total_count == 1
    assert evaluation.sample

    check = pandera_contract_asset_check_result(
        evaluation,
        partition_key="2025-01-01",
        asset_key="demo",
        schema_class=DemoSchema,
        query_or_sql=f"parquet:{parquet_path}",
    )
    assert check.check_name == "pandera_contract"
    assert check.passed is False
    assert check.severity == "error"
    assert "failed_count" in check.metadata
    assert "total_count" in check.metadata
    assert "sample" in check.metadata
    assert "schema" in check.metadata


def test_pandera_contract_asset_check_passes_for_valid_parquet(tmp_path) -> None:
    """Verify parquet contract check passes for schema-compliant data."""
    parquet_path = tmp_path / "data.parquet"
    df = pd.DataFrame([{"id": 1, "created_at": "2025-01-01T00:00:00Z"}])
    df.to_parquet(parquet_path)

    evaluation = evaluate_pandera_contract_parquet(parquet_path, schema_class=DemoSchema)
    assert evaluation.passed is True
    assert evaluation.failed_count == 0
    assert evaluation.total_count == 1


@pytest.mark.parametrize(
    ("tags", "expected_severity"),
    [
        ([], "error"),
        (["warn"], "warn"),
        (["anomaly"], "warn"),
        (["blocking"], "error"),
    ],
)
def test_dbt_test_results_emit_asset_checks_with_severity(tags, expected_severity) -> None:
    """Verify dbt test tags map to expected emitted asset check severity."""
    manifest = {
        "nodes": {
            "model.phlo.marts.mrt_orders": {
                "name": "mrt_orders",
                "resource_type": "model",
            },
            "test.phlo.not_null_mrt_orders_id": {
                "resource_type": "test",
                "test_metadata": {"name": "not_null"},
                "tags": tags,
                "compiled_code": "select * from marts.mrt_orders where id is null",
            },
        }
    }
    run_results = {
        "results": [
            {
                "unique_id": "test.phlo.not_null_mrt_orders_id",
                "status": "fail",
                "failures": 5,
                "message": "Got 5 results",
                "depends_on": {"nodes": ["model.phlo.marts.mrt_orders"]},
            }
        ]
    }

    checks = extract_dbt_asset_checks(
        run_results,
        manifest,
        translator=DbtSpecTranslator(),
        partition_key="2025-01-01",
    )
    assert len(checks) == 1
    check = checks[0]
    assert check.asset_key == "mrt_orders"
    assert check.check_name == "dbt__not_null__mrt_orders__test_phlo_not_null_mrt_orders_id"
    assert check.passed is False
    assert check.severity == expected_severity
    assert "partition_key" in check.metadata
    assert "query_or_sql" in check.metadata
    assert "failed_count" in check.metadata
