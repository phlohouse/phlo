"""Tests for the neutral provider workload identity matrix."""

from __future__ import annotations

from phlo.security.workload_identities import (
    WORKLOAD_IDENTITY_SPECS,
    evaluate_workload_identity_references,
)


def _distinct_env() -> dict[str, str]:
    return {
        "PHLO_SERVICE_CREDENTIALS_FILE": "/run/secrets/workload.json",
        "DAGSTER_MINIO_ACCESS_KEY": "dagster-access",
        "DAGSTER_MINIO_SECRET_KEY": "dagster-secret",
        "DAGSTER_TRINO_USER": "dagster-trino",
        "DAGSTER_POSTGRES_USER": "dagster-pg-user",
        "DAGSTER_POSTGRES_PASSWORD": "dagster-pg-password",
        "TRINO_QUERY_ACCESS_KEY": "query-access",
        "TRINO_QUERY_SECRET_KEY": "query-secret",
        "TRINO_USER": "query-user",
        "TRINO_ROLE": "query_role",
        "NESSIE_CATALOG_ACCESS_KEY": "catalog-access",
        "NESSIE_CATALOG_SECRET_KEY": "catalog-secret",
        "QUARKUS_DATASOURCE_USERNAME": "catalog-pg-user",
        "QUARKUS_DATASOURCE_PASSWORD": "catalog-pg-password",
        "MAINTENANCE_TRINO_USER": "maintenance-user",
        "MAINTENANCE_TRINO_ROLE": "maintenance_role",
        "MAINTENANCE_ACCESS_KEY": "maintenance-access",
        "MAINTENANCE_SECRET_KEY": "maintenance-secret",
    }


def test_matrix_covers_five_workloads() -> None:
    assert [spec.name for spec in WORKLOAD_IDENTITY_SPECS] == [
        "api",
        "orchestration",
        "query",
        "catalog",
        "maintenance",
    ]


def test_distinct_references_pass() -> None:
    results = {
        result.name: result for result in evaluate_workload_identity_references(_distinct_env())
    }
    assert all(
        results[name].passed for name in ("api", "orchestration", "query", "catalog", "maintenance")
    )


def test_missing_references_fail() -> None:
    results = {result.name: result for result in evaluate_workload_identity_references({})}
    assert all(not results[spec.name].passed for spec in WORKLOAD_IDENTITY_SPECS)
    assert results["api"].missing == ("PHLO_SERVICE_CREDENTIALS_FILE",)


def test_default_root_values_are_rejected() -> None:
    env = _distinct_env()
    env["TRINO_QUERY_ACCESS_KEY"] = "root"
    results = {result.name: result for result in evaluate_workload_identity_references(env)}
    assert not results["query"].passed
    assert "TRINO_QUERY_ACCESS_KEY" in results["query"].insecure_default


def test_shared_values_across_workloads_are_rejected() -> None:
    env = _distinct_env()
    # Query and catalog share the same secret — cross-workload leakage.
    env["NESSIE_CATALOG_SECRET_KEY"] = env["TRINO_QUERY_SECRET_KEY"]
    results = {result.name: result for result in evaluate_workload_identity_references(env)}
    assert not results["catalog"].passed
    assert any(other == "TRINO_QUERY_SECRET_KEY" for _ref, other in results["catalog"].shared_with)
