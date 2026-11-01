"""Tests multi-project dbt federation in build_dbt_asset_specs.

Multi-project activation (DBT_PROJECT_DIRS) merges every activated project's
manifest into one asset graph. Namespaced keys (DBT_NAMESPACED_ASSET_KEYS)
prefix dbt-derived keys with the project name; collisions that survive
namespacing raise, and cross-project source references resolve when the
referenced key exists in any activated project.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from phlo.exceptions import PhloCapabilitySetupError
from phlo.logging import get_logger
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
        assert spec.metadata["transform_engine"] == "dbt"
    assert {spec.metadata["transform_model"] for spec in specs} == {
        "finance_domain.invoice_aging",
        "sales_domain.deal_pipeline",
    }


def test_discovery_binds_owned_tests_and_reloads_per_project(monkeypatch, tmp_path) -> None:
    """Federated assets, retries and reloads keep their own selectors."""
    paths = []
    for project in ("sales", "finance"):
        _write_project(tmp_path, project, models=["empty", "one", "three", "seven"])
        path = tmp_path / "workflows" / project / "transforms" / "dbt"
        paths.append(path)
        manifest_path = path / "target" / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        for model, count in (("one", 1), ("three", 3), ("seven", 7)):
            for index in range(count):
                manifest["nodes"][f"test.{project}.{model}_{index}"] = {
                    "name": f"{project}_{model}_{index}",
                    "resource_type": "test",
                    "test_metadata": {"name": "relationships"},
                    "attached_node": f"model.{project}.{model}",
                    "depends_on": {"nodes": [f"model.{project}.empty"]},
                }
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    _stub_settings(monkeypatch, paths, namespaced=True)
    monkeypatch.setattr("phlo_dbt.transformer.ensure_dbt_profile", lambda *_a, **_k: None)

    def run_dbt(args, *, cwd, **kwargs):
        manifest = json.loads((Path(cwd) / "target" / "manifest.json").read_text(encoding="utf-8"))
        selectors = []
        if "--select" in args:
            for arg in args[args.index("--select") + 1 :]:
                if arg.startswith("--"):
                    break
                selectors.append(arg)
        selected = {
            key: node
            for key, node in manifest["nodes"].items()
            if not selectors or node["name"] in selectors
        }
        if (
            "--indirect-selection" not in args
            or args[args.index("--indirect-selection") + 1] != "empty"
        ):
            selected.update(
                (key, node)
                for key, node in manifest["nodes"].items()
                if any(dep in selected for dep in node.get("depends_on", {}).get("nodes", []))
            )
        artifact_path = Path(args[args.index("--target-path") + 1])
        artifact_path.mkdir(parents=True, exist_ok=True)
        (artifact_path / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        (artifact_path / "run_results.json").write_text(
            json.dumps(
                {
                    "results": [
                        {
                            "unique_id": key,
                            "status": "pass" if key.startswith("test.") else "success",
                        }
                        for key in selected
                    ]
                }
            ),
            encoding="utf-8",
        )
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    monkeypatch.setattr("phlo_dbt.transformer.subprocess.run", run_dbt)
    monkeypatch.setattr("phlo_dbt.assets.resolve_dbt_target_name", lambda _: "dev")
    runtime = SimpleNamespace(
        partition_key="2026-11-01",
        tags={"phlo/full_refresh_asset": "sales.three"},
        logger=get_logger("federated-runners"),
        run_id="retry",
    )
    specs = build_dbt_asset_specs()
    for spec in reversed(specs):
        assert spec.run is not None
        assert (
            len(spec.checks)
            == {"empty": 0, "one": 1, "three": 3, "seven": 7}[spec.key.split(".")[1]]
        )
        for _ in range(2):
            *checks, materialization = spec.run.fn(runtime)
            project, model = spec.key.split(".")
            assert {check.metadata["test_unique_id"] for check in checks} == {
                f"test.{project}.{model}_{i}" for i in range(len(spec.checks))
            }
            assert all(check.asset_key == spec.key and check.passed for check in checks)
            assert all(check.metadata["partition_key"] == "2026-11-01" for check in checks)
            assert materialization.status == "success"
            assert materialization.metadata["transform_counts"] == {
                "passed": len(spec.checks),
                "failed": 0,
            }

    manifest_path = paths[0] / "target" / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["nodes"]["test.sales.one_1"] = {
        **manifest["nodes"]["test.sales.one_0"],
        "name": "replacement",
    }
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    reloaded = {spec.key: spec for spec in build_dbt_asset_specs()}
    reloaded_run = reloaded["sales.one"].run
    assert reloaded_run is not None
    *checks, materialization = reloaded_run.fn(runtime)
    assert {check.metadata["test_unique_id"] for check in checks} == {
        "test.sales.one_0",
        "test.sales.one_1",
    }
    assert materialization.metadata["transform_counts"] == {"passed": 2, "failed": 0}
    original = next(spec for spec in specs if spec.key == "sales.one")
    assert original.run is not None
    *checks, materialization = original.run.fn(runtime)
    assert [check.metadata["test_unique_id"] for check in checks] == ["test.sales.one_0"]
    assert materialization.metadata["transform_counts"] == {"passed": 1, "failed": 0}


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
    result = transformer.run_transform(parameters={"skip_build": True, "generate_docs": False})

    assert captured["project_dir"] == project
    assert result.status == "success"


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
