"""Tests for dbt manifest lineage import helpers.

Covers edge extraction from manifest nodes, persistence of assets and columns
into the sink, the skip path when no sink is configured, and the same-name
heuristic used to derive column-level lineage.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

from phlo_dbt.lineage_import import (
    collect_asset_lineage,
    extract_column_lineage,
    import_manifest_lineage,
)


def test_collect_asset_lineage_returns_edges_and_targets() -> None:
    manifest = {
        "nodes": {
            "model.demo.dim_pokemon": {
                "resource_type": "model",
                "schema": "gold",
                "name": "dim_pokemon",
                "depends_on": {"nodes": ["model.demo.stg_pokemon", "source.demo.raw_pokemon"]},
            },
            "model.demo.stg_pokemon": {
                "resource_type": "model",
                "schema": "silver",
                "name": "stg_pokemon",
                "depends_on": {"nodes": ["source.demo.raw_pokemon"]},
            },
        },
        "sources": {
            "source.demo.raw_pokemon": {
                "resource_type": "source",
                "source_name": "raw",
                "name": "pokemon",
            }
        },
    }

    edges, asset_keys = collect_asset_lineage(manifest)

    assert edges == [
        ("stg_pokemon", "dim_pokemon"),
        ("raw.pokemon", "dim_pokemon"),
        ("raw.pokemon", "stg_pokemon"),
    ]
    assert asset_keys == ["dim_pokemon", "stg_pokemon"]


def test_import_manifest_lineage_persists_assets_and_columns(monkeypatch, tmp_path) -> None:
    manifest_path = tmp_path / "target" / "manifest.json"
    manifest_path.parent.mkdir(parents=True)
    manifest = {
        "nodes": {
            "model.demo.dim_pokemon": {
                "resource_type": "model",
                "schema": "gold",
                "name": "dim_pokemon",
                "depends_on": {"nodes": ["model.demo.stg_pokemon"]},
                "columns": {"id": {}, "name": {}},
            },
            "model.demo.stg_pokemon": {
                "resource_type": "model",
                "schema": "silver",
                "name": "stg_pokemon",
                "depends_on": {"nodes": []},
                "columns": {"id": {}, "name": {}, "type": {}},
            },
        },
        "sources": {},
    }
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    sink = SimpleNamespace()
    sink_calls: list[tuple[list[tuple[str, str]], list[str] | None, dict[str, object] | None]] = []
    column_calls: list[list[dict[str, object]]] = []
    sink.record_asset_edges = lambda edges, *, asset_keys=None, metadata=None, tags=None: (
        sink_calls.append((edges, asset_keys, metadata)) or len(edges)
    )
    sink.record_column_lineage = lambda mappings: column_calls.append(mappings) or len(mappings)
    monkeypatch.setattr("phlo_dbt.lineage_import._discover_capabilities", lambda: None)
    monkeypatch.setattr(
        "phlo_dbt.lineage_import._resolve_capability",
        lambda capability_type: (
            SimpleNamespace(name="phlo-lineage", provider=sink)
            if capability_type == "lineage_sink"
            else None
        ),
    )

    summary = import_manifest_lineage(manifest_path)

    assert summary == {"asset_edges": 1, "column_mappings": 2}
    assert sink_calls == [
        (
            [("stg_pokemon", "dim_pokemon")],
            ["dim_pokemon", "stg_pokemon"],
            {"source": "dbt", "manifest_path": str(manifest_path)},
        )
    ]
    assert column_calls == [
        [
            {
                "source_asset": "silver.stg_pokemon",
                "source_column": "id",
                "target_asset": "gold.dim_pokemon",
                "target_column": "id",
                "source_type": "dbt_heuristic",
            },
            {
                "source_asset": "silver.stg_pokemon",
                "source_column": "name",
                "target_asset": "gold.dim_pokemon",
                "target_column": "name",
                "source_type": "dbt_heuristic",
            },
        ]
    ]


def test_import_manifest_lineage_skips_when_no_sink(monkeypatch, tmp_path) -> None:
    manifest_path = tmp_path / "target" / "manifest.json"
    manifest_path.parent.mkdir(parents=True)
    manifest_path.write_text(json.dumps({"nodes": {}, "sources": {}}), encoding="utf-8")

    monkeypatch.setattr("phlo_dbt.lineage_import._discover_capabilities", lambda: None)
    monkeypatch.setattr(
        "phlo_dbt.lineage_import._resolve_capability",
        lambda capability_type: None,
    )

    assert import_manifest_lineage(manifest_path) == {"asset_edges": 0, "column_mappings": 0}


def test_extract_column_lineage_uses_same_name_heuristic() -> None:
    manifest = {
        "nodes": {
            "model.demo.dim_pokemon": {
                "resource_type": "model",
                "schema": "gold",
                "name": "dim_pokemon",
                "depends_on": {"nodes": ["model.demo.stg_pokemon"]},
                "columns": {"id": {}, "name": {}, "generation": {}},
            },
            "model.demo.stg_pokemon": {
                "resource_type": "model",
                "schema": "silver",
                "name": "stg_pokemon",
                "depends_on": {"nodes": []},
                "columns": {"id": {}, "name": {}, "type": {}},
            },
        }
    }

    assert extract_column_lineage(manifest) == [
        {
            "source_asset": "silver.stg_pokemon",
            "source_column": "id",
            "target_asset": "gold.dim_pokemon",
            "target_column": "id",
            "source_type": "dbt_heuristic",
        },
        {
            "source_asset": "silver.stg_pokemon",
            "source_column": "name",
            "target_asset": "gold.dim_pokemon",
            "target_column": "name",
            "source_type": "dbt_heuristic",
        },
    ]
