"""Tests for ergonomic dbt helper utilities.

Covers selector normalization, manifest model selection, qualified table
extraction, partition variable building, and compiler wrapping.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

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


def test_ensure_compiled_wraps_existing_manifest_compiler(monkeypatch, tmp_path) -> None:
    calls: list[tuple[Path, Path]] = []
    monkeypatch.setattr("phlo_dbt.helpers.get_dbt_project_dir", lambda: tmp_path / "dbt")
    monkeypatch.setattr(
        "phlo_dbt.helpers.ensure_dbt_manifest",
        lambda project, profiles: calls.append((project, profiles)) or True,
    )

    assert ensure_compiled(profiles_dir=tmp_path / "profiles") is True
    assert calls == [(tmp_path / "dbt", tmp_path / "profiles")]
