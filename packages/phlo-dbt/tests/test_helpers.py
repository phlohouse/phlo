"""Tests for ergonomic dbt helper utilities.

Covers selector normalization, manifest model selection, qualified table
extraction, partition variable building, and manifest preparation.
"""

from __future__ import annotations

from datetime import date

import pytest
import yaml

from phlo_dbt.helpers import (
    DbtManifestTable,
    build_partition_vars,
    ensure_compiled,
    extract_manifest_tables,
    normalize_selectors,
    select_manifest_models,
)


def _manifest() -> dict:
    return {
        "nodes": {
            "model.analytics.stg_orders": {
                "unique_id": "model.analytics.stg_orders",
                "resource_type": "model",
                "package_name": "analytics",
                "name": "stg_orders",
                "alias": "stg_orders",
                "database": "lake",
                "schema": "staging",
            },
            "model.analytics.mrt_orders": {
                "unique_id": "model.analytics.mrt_orders",
                "resource_type": "model",
                "package_name": "analytics",
                "name": "mrt_orders",
                "alias": "orders",
                "database": "lake",
                "schema": "marts",
                "relation_name": '"lake"."marts"."orders"',
            },
            "test.analytics.not_null_orders_id": {
                "unique_id": "test.analytics.not_null_orders_id",
                "resource_type": "test",
                "name": "not_null_orders_id",
            },
        }
    }


def test_normalize_selectors_flattens_common_cli_shapes() -> None:
    assert normalize_selectors(["stg_* mrt_orders", "tag:nightly, +dim_customers"]) == [
        "stg_*",
        "mrt_orders",
        "tag:nightly",
        "+dim_customers",
    ]


def test_select_manifest_models_matches_name_alias_unique_id_and_globs() -> None:
    manifest = _manifest()

    assert [node["name"] for node in select_manifest_models(manifest, "analytics.stg_*")] == [
        "stg_orders"
    ]
    assert [node["name"] for node in select_manifest_models(manifest, "orders")] == ["mrt_orders"]
    assert [
        node["name"] for node in select_manifest_models(manifest, "model.analytics.mrt_orders")
    ] == ["mrt_orders"]
    assert [node["name"] for node in select_manifest_models(manifest)] == [
        "stg_orders",
        "mrt_orders",
    ]


def test_extract_manifest_tables_returns_qualified_table_refs() -> None:
    assert extract_manifest_tables(_manifest(), selectors="mrt_*") == [
        DbtManifestTable(
            unique_id="model.analytics.mrt_orders",
            name="mrt_orders",
            relation_name='"lake"."marts"."orders"',
            database="lake",
            schema="marts",
            identifier="orders",
        )
    ]


def test_build_partition_vars_uses_partition_key_and_window() -> None:
    assert build_partition_vars(
        partition_key=date(2026, 1, 2),
        start=date(2026, 1, 1),
        end="2026-01-08",
    ) == {
        "partition_date_str": "2026-01-02",
        "partition_start": "2026-01-01",
        "partition_end": "2026-01-08",
    }


@pytest.mark.parametrize("explicit_profiles", [False, True])
def test_ensure_compiled_prepares_discovered_project_profile(
    monkeypatch, tmp_path, explicit_profiles
) -> None:
    project = tmp_path / "dbt"
    (project / "target").mkdir(parents=True)
    (project / "dbt_project.yml").write_text("name: sales\nprofile: sales_profile\n")
    (project / "target" / "manifest.json").write_text('{"nodes": {}}')
    monkeypatch.setenv("DBT_PROJECT_DIR", str(project))
    profiles = tmp_path / "separate_profiles" if explicit_profiles else project

    assert ensure_compiled(profiles_dir=str(profiles) if explicit_profiles else None) is True
    profile = yaml.safe_load((profiles / "profiles.yml").read_text())
    assert profile["sales_profile"]["target"] == "dev"
    assert (project / "target" / "manifest.json").read_text() == '{"nodes": {}}'
