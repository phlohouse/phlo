"""Tests multi-project dbt federation in build_dbt_asset_specs.

Multi-project activation (DBT_PROJECT_DIRS) merges every activated project's
manifest into one asset graph. Namespaced keys (DBT_NAMESPACED_ASSET_KEYS)
prefix dbt-derived keys with the project name; collisions that survive
namespacing raise, and cross-project source references resolve when the
referenced key exists in any activated project.
"""

from __future__ import annotations

import json

import pytest

from phlo.exceptions import PhloCapabilitySetupError
from phlo_dbt.assets import build_dbt_asset_specs
from phlo_dbt.translator import DbtSpecTranslator


def _write_project(root, name: str, *, models: list[str], source_key: str | None = None) -> None:
    project_path = root / "workflows" / name / "transforms" / "dbt"
    (project_path / "target").mkdir(parents=True)
    (project_path / "dbt_project.yml").write_text(
        f"name: {name}\nversion: '1.0'\nprofile: {name}\n", encoding="utf-8"
    )
    nodes = {
        f"model.{name}.{model}": {
            "resource_type": "model",
            "name": model,
            "path": f"models/{model}.sql",
            "schema": "raw",
            "depends_on": {"nodes": []},
            "columns": {},
        }
        for model in models
    }
    sources = {}
    if source_key is not None:
        nodes[f"model.{name}.bridge"] = {
            "resource_type": "model",
            "name": "bridge",
            "path": "models/bridge.sql",
            "schema": "raw",
            "depends_on": {"nodes": [f"source.{name}.raw.foreign"]},
            "columns": {},
        }
        sources[f"source.{name}.raw.foreign"] = {
            "resource_type": "source",
            "source_name": "raw",
            "name": "foreign",
            "meta": {"phlo_asset_key": source_key},
        }
    (project_path / "target" / "manifest.json").write_text(
        json.dumps({"nodes": nodes, "sources": sources}), encoding="utf-8"
    )


def _stub_settings(monkeypatch, project_paths, *, namespaced: bool) -> None:
    monkeypatch.setattr(
        "phlo_dbt.assets.get_settings",
        lambda: type(
            "Settings",
            (),
            {
                "dbt_project_path": project_paths[0],
                "dbt_project_paths": list(project_paths),
                "dbt_namespaced_asset_keys": namespaced,
                "dbt_profiles_path_for": lambda _s, p: p / "profiles",
            },
        )(),
    )
    monkeypatch.setattr("phlo_dbt.assets.ensure_dbt_profile", lambda *_a, **_k: None)
    monkeypatch.setattr("phlo_dbt.assets.ensure_dbt_manifest", lambda *_a, **_k: True)


def test_translator_prefixes_only_own_resources() -> None:
    translator = DbtSpecTranslator(key_prefix="sales")
    assert (
        translator.get_asset_key({"resource_type": "model", "name": "deal_pipeline"})
        == "sales.deal_pipeline"
    )
    assert (
        translator.get_asset_key(
            {
                "resource_type": "source",
                "source_name": "raw",
                "name": "deals",
                "meta": {"phlo_asset_key": "dlt_deals"},
            }
        )
        == "dlt_deals"
    )
    assert (
        translator.get_asset_key({"resource_type": "source", "source_name": "raw", "name": "deals"})
        == "raw.deals"
    )


def test_multi_project_build_merges_namespaced_specs(monkeypatch, tmp_path) -> None:
    _write_project(tmp_path, "sales_domain", models=["deal_pipeline"])
    _write_project(tmp_path, "finance_domain", models=["invoice_aging"])
    _stub_settings(
        monkeypatch,
        [
            tmp_path / "workflows" / "finance_domain" / "transforms" / "dbt",
            tmp_path / "workflows" / "sales_domain" / "transforms" / "dbt",
        ],
        namespaced=True,
    )

    specs = build_dbt_asset_specs()

    keys = {spec.key for spec in specs}
    assert keys == {"finance_domain.invoice_aging", "sales_domain.deal_pipeline"}
    for spec in specs:
        assert spec.metadata["dbt_project"] in {"finance_domain", "sales_domain"}


def test_multi_project_cross_reference_resolves_to_foreign_model(monkeypatch, tmp_path) -> None:
    _write_project(tmp_path, "sales_domain", models=["deal_pipeline"])
    _write_project(
        tmp_path,
        "finance_domain",
        models=["invoice_aging"],
        source_key="sales_domain.deal_pipeline",
    )
    _stub_settings(
        monkeypatch,
        [
            tmp_path / "workflows" / "finance_domain" / "transforms" / "dbt",
            tmp_path / "workflows" / "sales_domain" / "transforms" / "dbt",
        ],
        namespaced=True,
    )

    specs = {spec.key: spec for spec in build_dbt_asset_specs()}

    finance_spec = specs["finance_domain.invoice_aging"]
    assert finance_spec is not None
    finance_bridge = specs["finance_domain.bridge"]
    assert "sales_domain.deal_pipeline" in finance_bridge.deps


def test_multi_project_collision_raises_without_namespacing(monkeypatch, tmp_path) -> None:
    _write_project(tmp_path, "sales_domain", models=["customers"])
    _write_project(tmp_path, "finance_domain", models=["customers"])
    _stub_settings(
        monkeypatch,
        [
            tmp_path / "workflows" / "finance_domain" / "transforms" / "dbt",
            tmp_path / "workflows" / "sales_domain" / "transforms" / "dbt",
        ],
        namespaced=False,
    )

    with pytest.raises(PhloCapabilitySetupError, match="collision"):
        build_dbt_asset_specs()


def test_multi_project_missing_project_degrades_to_empty(monkeypatch, tmp_path) -> None:
    _stub_settings(monkeypatch, [tmp_path / "does_not_exist"], namespaced=False)

    assert build_dbt_asset_specs() == []


def test_multi_project_duplicate_project_name_raises(monkeypatch, tmp_path) -> None:
    """Two directories declaring the same dbt project name must fail loudly."""
    _write_project(tmp_path, "sales_domain", models=["deal_pipeline"])
    finance_dir = tmp_path / "workflows" / "finance_domain" / "transforms" / "dbt"
    (finance_dir / "target").mkdir(parents=True)
    (finance_dir / "dbt_project.yml").write_text(
        "name: sales_domain\nversion: '1.0'\nprofile: sales_domain\n", encoding="utf-8"
    )
    (finance_dir / "target" / "manifest.json").write_text(
        json.dumps({"nodes": {}, "sources": {}}), encoding="utf-8"
    )
    _stub_settings(
        monkeypatch,
        [
            tmp_path / "workflows" / "finance_domain" / "transforms" / "dbt",
            tmp_path / "workflows" / "sales_domain" / "transforms" / "dbt",
        ],
        namespaced=True,
    )

    with pytest.raises(PhloCapabilitySetupError, match="more than one"):
        build_dbt_asset_specs()


def test_multi_project_duplicate_directory_raises(monkeypatch, tmp_path) -> None:
    _write_project(tmp_path, "sales_domain", models=["deal_pipeline"])
    sales_dir = tmp_path / "workflows" / "sales_domain" / "transforms" / "dbt"
    _stub_settings(monkeypatch, [sales_dir, sales_dir], namespaced=True)

    with pytest.raises(PhloCapabilitySetupError, match="more than once"):
        build_dbt_asset_specs()


def test_run_transform_profile_write_uses_own_project_profile(monkeypatch, tmp_path) -> None:
    """Materialization must not clobber a project's profiles.yml with another
    project's profile name (federation regression)."""
    from phlo_dbt.transformer import DbtTransformer

    project = tmp_path / "finance"
    project.mkdir()
    (project / "dbt_project.yml").write_text("name: finance_domain\nprofile: finance_domain\n")

    captured: dict[str, object] = {}

    def fake_ensure_dbt_profile(profiles_dir, **kwargs):
        captured["project_dir"] = kwargs.get("project_dir")

    monkeypatch.setattr("phlo_dbt.transformer.ensure_dbt_profile", fake_ensure_dbt_profile)
    monkeypatch.setattr(
        DbtTransformer, "_run_command", lambda self, args: type("R", (), {"returncode": 0})()
    )

    transformer = DbtTransformer(
        context=type("Ctx", (), {})(),
        logger=type("Log", (), {"info": lambda *a, **k: None})(),
        project_dir=project,
        profiles_dir=project / "profiles",
    )
    try:
        transformer.run_transform(parameters={"skip_build": True, "generate_docs": False})
    except Exception:
        pass  # telemetry hooks may be absent in this stub context

    assert captured["project_dir"] == project


def test_external_deps_recorded_for_cross_provider_references(monkeypatch, tmp_path) -> None:
    """Deps that no dbt manifest produces are recorded as external deps for
    the aggregation-point validator."""
    from phlo.capabilities.external_refs import EXTERNAL_DEPS_METADATA_KEY

    project_path = tmp_path / "workflows" / "sales_domain" / "transforms" / "dbt"
    (project_path / "target").mkdir(parents=True)
    (project_path / "dbt_project.yml").write_text(
        "name: sales_domain\nversion: '1.0'\nprofile: sales_domain\n", encoding="utf-8"
    )
    (project_path / "target" / "manifest.json").write_text(
        json.dumps(
            {
                "nodes": {
                    "model.sales_domain.deal_pipeline": {
                        "resource_type": "model",
                        "name": "deal_pipeline",
                        "path": "models/deal_pipeline.sql",
                        "schema": "raw",
                        "depends_on": {"nodes": ["source.sales_domain.raw.deals"]},
                        "columns": {},
                    }
                },
                "sources": {
                    "source.sales_domain.raw.deals": {
                        "resource_type": "source",
                        "source_name": "dagster_assets",
                        "name": "deals",
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    _stub_settings(monkeypatch, [project_path], namespaced=True)

    specs = {spec.key: spec for spec in build_dbt_asset_specs()}

    sales = specs["sales_domain.deal_pipeline"]
    assert sales.deps == ["dlt_deals"]
    assert sales.metadata[EXTERNAL_DEPS_METADATA_KEY] == ["dlt_deals"]


def test_plain_source_binding_is_recorded_as_external(monkeypatch, tmp_path) -> None:
    """A conventional dbt source ({source_name}.{table}) is owned by no
    provider, so it is recorded as an external dep and the validator warns
    when it is absent from the merged graph."""
    from phlo.capabilities.external_refs import (
        EXTERNAL_DEPS_METADATA_KEY,
        validate_external_asset_references,
    )

    project_path = tmp_path / "workflows" / "sales_domain" / "transforms" / "dbt"
    (project_path / "target").mkdir(parents=True)
    (project_path / "dbt_project.yml").write_text(
        "name: sales_domain\nversion: '1.0'\nprofile: sales_domain\n", encoding="utf-8"
    )
    (project_path / "target" / "manifest.json").write_text(
        json.dumps(
            {
                "nodes": {
                    "model.sales_domain.deal_pipeline": {
                        "resource_type": "model",
                        "name": "deal_pipeline",
                        "path": "models/deal_pipeline.sql",
                        "schema": "raw",
                        "depends_on": {"nodes": ["source.sales_domain.upstream.deals"]},
                        "columns": {},
                    }
                },
                "sources": {
                    "source.sales_domain.upstream.deals": {
                        "resource_type": "source",
                        "source_name": "upstream",
                        "name": "deals",
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    _stub_settings(monkeypatch, [project_path], namespaced=True)

    specs = build_dbt_asset_specs()

    assert specs[0].metadata[EXTERNAL_DEPS_METADATA_KEY] == ["upstream.deals"]
    warnings: list[str] = []
    import logging

    handler = logging.Handler()
    handler.emit = lambda record: warnings.append(record.getMessage())
    logging.getLogger("phlo.capabilities.external_refs").addHandler(handler)
    try:
        validate_external_asset_references(specs)
    finally:
        logging.getLogger("phlo.capabilities.external_refs").removeHandler(handler)
    assert any("upstream.deals" in message for message in warnings)
