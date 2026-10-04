"""Exercise audit rules on disposable tables and guard scoped proposal operations."""

from contextlib import contextmanager
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import Mock

import duckdb
import pytest

from phlo.capabilities import get_capability_registry
from phlo_api.api import v1_assets
from phlo_api.api.v1_audit_proposals import (
    AssetAuditProposalRequest,
    evaluate_audit_rules,
    generate_check_file,
)
from test_v1_api import client as client


def _rules():
    return AssetAuditProposalRequest.model_validate(
        {
            "check_name": "quality",
            "idempotency_key": "test",
            "rules": [
                {"kind": "not_null", "column": "id"},
                {"kind": "unique", "column": "id"},
                {"kind": "range", "column": "amount", "minimum": -7, "maximum": 23},
            ],
        }
    ).rules


@pytest.mark.parametrize(
    "policy,severity,blocking", [("block", "error", True), ("warn", "warn", False)]
)
def test_generated_check_policy_reaches_real_runtime(policy, severity, blocking):
    # Adapter boundary only: execute the generated Trino SELECT on a disposable
    # DuckDB table. The real decorator, rules, registry and emitters run unchanged.
    connection = duckdb.connect()
    connection.execute("CREATE SCHEMA warehouse")
    connection.execute(
        "CREATE TABLE warehouse.orders AS SELECT * FROM (VALUES (1, -8), (2, 23), (2, 24), (NULL, -7)) t(id, amount)"
    )

    class TrinoTable:
        @contextmanager
        def cursor(self):
            yield connection.cursor()

    registry = get_capability_registry()
    registry.clear("check")
    try:
        _, source = generate_check_file(
            ["logical", "orders"],
            "quality",
            _rules(),
            failure_policy=policy,
            table_relation="warehouse.orders",
        )
        exec(compile(source, "generated_check.py", "exec"), {})
        (spec,) = registry.list("check")
        assert spec.asset_key == "logical.orders"
        assert spec.blocking is blocking
        runtime = SimpleNamespace(
            run_id="disposable-audit",
            partition_key=None,
            tags={"phlo/ref": "test"},
            resources={"trino": TrinoTable()},
            logger=Mock(),
        )
        result = spec.fn(runtime)
        assert result.passed is False
        assert result.severity == severity
        assert result.metadata["total_count"] == 4
        rows = connection.execute("SELECT * FROM warehouse.orders").fetchdf().to_dict("records")
        evaluated = evaluate_audit_rules(_rules(), rows, ["id", "amount"])
        assert [item.failure_count for item in evaluated] == [1, 2, 2]
        assert [item.passed for item in evaluated] == [False, False, False]
        # Both inclusive boundaries pass; asymmetric outside values fail above.
        clean = evaluate_audit_rules(
            _rules(), [{"id": 5, "amount": -7}, {"id": 8, "amount": 23}], ["id", "amount"]
        )
        assert all(item.passed for item in clean)
        assert [item.failure_count for item in clean] == [0, 0, 0]
    finally:
        registry.clear("check")
        connection.close()


def test_proposal_dry_run_scope_coverage_and_stale_publication(client, tmp_path, monkeypatch):
    http, decisions, *_ = client
    monkeypatch.setenv("PHLO_PROJECT_PATH", str(tmp_path))
    monkeypatch.setenv("PHLO_AUTHORIZATION_MODE", "required")
    monkeypatch.setenv("PHLO_V1_ACTIONS_SINGLE_REPLICA", "1")
    monkeypatch.setenv("PHLO_V1_ACTIONS_SINGLE_PROCESS", "1")
    monkeypatch.setenv(
        "PHLO_API_TOKENS",
        '{"writer":{"subject":"alice","scopes":["project:write"]},"reader":{"subject":"reader","scopes":["project:read"]}}',
    )
    asset = v1_assets.AssetDetail(
        id="warehouse/orders",
        key=["warehouse", "orders"],
        description=None,
        compute_kind="sql",
        group_name="warehouse",
        is_source=False,
        dependencies=[],
        last_materialization_at=None,
        last_run_id=None,
        relation="warehouse.orders",
        columns=[
            v1_assets.AssetColumn(name="id", type="bigint", description=None),
            v1_assets.AssetColumn(name="amount", type="double", description=None),
        ],
        schema_observed_at=None,
    )

    async def assets(*args, **kwargs):
        return [asset]

    async def detail(*args, **kwargs):
        return asset

    monkeypatch.setattr(v1_assets, "_assets", assets)
    monkeypatch.setattr(v1_assets, "v1_asset_detail", detail)
    monkeypatch.setattr(v1_assets, "preview_catalog", lambda env, ref: "test_catalog")
    connection = duckdb.connect()
    connection.execute("CREATE SCHEMA warehouse")
    connection.execute(
        "CREATE TABLE warehouse.orders AS SELECT i AS id, CAST(0 AS DOUBLE) AS amount FROM range(101) t(i)"
    )
    queried = []

    async def preview(sql, *, catalog, disconnected, limit):
        queried.append((sql, catalog, limit))
        frame = connection.execute(sql.replace('"test_catalog".', "")).fetchdf()
        return {
            "columns": [{"name": "id", "type": "bigint"}, {"name": "amount", "type": "double"}],
            "rows": frame.head(limit).to_dict("records"),
            "has_more": len(frame) > limit,
        }

    monkeypatch.setattr(v1_assets, "execute_preview", preview)
    headers = {"Authorization": "Bearer writer"}
    base = "/api/v1/assets/warehouse/orders/audits"
    body = {
        "check_name": "quality",
        "rules": [rule.model_dump() for rule in _rules()],
        "failure_policy": "warn",
        "idempotency_key": "coverage-proposal",
    }
    assert (
        http.post(
            base + "?env=prod", json={**body, "check_name": "class"}, headers=headers
        ).status_code
        == 422
    )
    # Selected definition/schema scope is sufficient; no materialization is required.
    asset.history_scoped = False
    proposal = http.post(base + "?env=prod", json=body, headers=headers).json()
    assert proposal["failure_policy"] == "warn"
    asset.history_scoped = True
    url = base + "/" + proposal["proposal_id"]
    assert http.post(url + "/test?env=staging", headers=headers).status_code == 404
    assert (
        http.post(url + "/test?env=prod", headers={"Authorization": "Bearer reader"}).status_code
        == 403
    )
    execution = http.post(url + "/test?env=prod", headers=headers)
    assert execution.status_code == 200, execution.text
    result = execution.json()
    assert result["rows_checked"] == 100 and result["sampled"] is True and result["passed"] is True
    assert result["engine"] == "trino" and result["nessie_ref"] == "main"
    assert datetime.fromisoformat(result["executed_at"]).tzinfo is not None
    assert queried == [
        ('SELECT * FROM "test_catalog"."warehouse"."orders" LIMIT 101', "test_catalog", 100)
    ]
    assert any(action == "asset.read" for action, *_ in decisions)
    connection.execute("DELETE FROM warehouse.orders WHERE id >= 4")
    connection.execute("UPDATE warehouse.orders SET amount = 24 WHERE id = 2")
    execution = http.post(url + "/test?env=prod", headers=headers).json()
    assert (
        execution["rows_checked"] == 4
        and execution["sampled"] is False
        and execution["passed"] is False
    )
    assert execution["results"][2]["failure_count"] == 1
    assert not (tmp_path / "workflows").exists()
    before = len(queried)
    asset.columns[1].type = "varchar"
    assert http.post(url + "/test?env=prod", headers=headers).status_code == 409
    response = http.post(
        url + "/pull-request?env=prod",
        json={
            "idempotency_key": "stale-publication",
            "expected_source_digest": proposal["source_digest"],
        },
        headers=headers,
    )
    assert response.status_code == 409
    assert "audit_proposal_schema_changed" in response.json()["error"]["message"]
    assert len(queried) == before
    asset.columns[1].type = "double"
    asset.history_scoped = False
    publication = http.post(
        url + "/pull-request?env=prod",
        json={"idempotency_key": "no-materialization"},
        headers=headers,
    )
    assert publication.status_code == 503
    assert publication.json()["error"]["message"] == "Project Git review is not configured."
    assert len(queried) == before
    asset.history_scoped = True
    response = http.post(
        url + "/pull-request?env=prod",
        json={"idempotency_key": "wrong-digest", "expected_source_digest": "0" * 64},
        headers=headers,
    )
    assert response.status_code == 409
    connection.close()
