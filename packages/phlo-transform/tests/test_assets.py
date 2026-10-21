"""Asset specs, activation rules, adapter flags, and run-result translation."""

from __future__ import annotations

from pathlib import Path

import pytest

from phlo.capabilities import AssetSpec, CheckResult, MaterializeResult
from phlo.exceptions import PhloCapabilitySetupError
from phlo.helpers.testing import FakeRuntimeContext
from phlo_transform import assets as assets_module
from phlo_transform.assets import (
    adapter_args,
    build_workspace_asset_specs,
    has_dbt_project,
    provider_is_active,
)
from phlo_transform.binary import Invocation
from phlo_transform.settings import TransformSettings


def _invocation(returncode: int = 0) -> Invocation:
    return Invocation(command=("phlo-transform",), returncode=returncode, stdout="{}", stderr="")


def test_leaf_keys_are_dbt_compatible_and_deps_use_same_map(
    tmp_path: Path, transform_settings: TransformSettings, fake_workspace
) -> None:
    specs = build_workspace_asset_specs(tmp_path, transform_settings)

    assert [spec.key for spec in specs] == ["stg_orders", "orders"]
    assert specs[1].deps == ["stg_orders"]
    assert specs[1].group == "analytics"
    assert specs[1].metadata["transform_model"] == "analytics.orders"
    assert specs[1].metadata["transform_engine"] == "phlo-transform"
    assert specs[1].metadata["transform_test_names"] == [
        "analytics.orders.generated.not_null_id"
    ]
    assert specs[1].checks[0].name == "analytics_orders_generated_not_null_id"


def test_logical_keys_keep_the_namespace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    transform_settings: TransformSettings,
    fake_workspace,
) -> None:
    monkeypatch.setenv("PHLO_TRANSFORM_ASSET_KEY", "logical")
    settings = TransformSettings(_project_root=tmp_path)

    specs = build_workspace_asset_specs(tmp_path, settings)

    assert [spec.key for spec in specs] == ["analytics.stg_orders", "analytics.orders"]
    assert specs[1].deps == ["analytics.stg_orders"]


def test_leaf_collision_fails_with_guidance(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    transform_settings: TransformSettings,
    fake_workspace,
) -> None:
    from phlo_transform.workspace import WorkspaceModel

    duplicate = WorkspaceModel(
        id="model://other/orders",
        name="other.orders",
        namespace="other",
        materialization="table",
        target="other.orders",
        version="9999999",
        path="transforms/orders.sql",
        tags=(),
        depends_on=(),
        sources=(),
    )
    broken = fake_workspace.__class__(
        root=fake_workspace.root,
        binary=fake_workspace.binary,
        models=(*fake_workspace.models, duplicate),
        tests=(),
        sources=(),
    )

    monkeypatch.setattr(assets_module, "load_workspace", lambda *args, **kwargs: broken)

    with pytest.raises(PhloCapabilitySetupError, match="asset key collision"):
        build_workspace_asset_specs(tmp_path, transform_settings)


def test_source_asset_bindings_become_dependencies(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    transform_settings: TransformSettings,
    fake_workspace,
) -> None:
    bound = fake_workspace.__class__(
        root=fake_workspace.root,
        binary=fake_workspace.binary,
        models=fake_workspace.models,
        tests=fake_workspace.tests,
        sources=fake_workspace.sources,
        source_assets={"raw.orders": "sling_orders"},
    )
    monkeypatch.setattr(assets_module, "load_workspace", lambda *args, **kwargs: bound)

    specs = {spec.key: spec for spec in build_workspace_asset_specs(tmp_path, transform_settings)}

    assert specs["stg_orders"].deps == ["sling_orders"]
    assert specs["stg_orders"].metadata["transform_source_assets"] == ["sling_orders"]
    assert specs["orders"].deps == ["stg_orders"]


def test_run_translates_materialization_and_only_own_tests(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    transform_settings: TransformSettings,
    fake_workspace,
) -> None:
    payload = {
        "run_id": "run-1",
        "plan_id": "plan-1",
        "status": "passed",
        "counts": {"passed": 2, "failed": 0, "skipped": 0},
        "models": [{"model": "analytics.orders", "duration_ms": 7, "query_id": "q-1"}],
        "tests": [
            {"test": "analytics.orders.generated.not_null_id", "status": "passed", "row_count": 0},
            {"test": "analytics.stg_orders.generated.other", "status": "failed", "row_count": 3},
        ],
    }
    seen: list[list[str]] = []

    def fake_run_json(binary, args, *, settings, root):
        seen.append(list(args))
        return payload, _invocation()

    monkeypatch.setattr(assets_module, "run_json", fake_run_json)
    specs = {spec.key: spec for spec in build_workspace_asset_specs(tmp_path, transform_settings)}

    results = list(specs["orders"].run.fn(FakeRuntimeContext()))

    checks = [result for result in results if isinstance(result, CheckResult)]
    materializations = [result for result in results if isinstance(result, MaterializeResult)]
    assert len(checks) == 1
    assert checks[0].check_name == "analytics_orders_generated_not_null_id"
    assert checks[0].passed is True
    assert materializations[0].status == "success"
    assert materializations[0].metadata["transform_run_id"] == "run-1"
    assert materializations[0].metadata["transform_duration_ms"] == 7
    assert seen[0][:3] == ["run", "--select", "analytics.orders"]
    assert "duckdb" in seen[0]


def test_failed_run_materialises_as_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    transform_settings: TransformSettings,
    fake_workspace,
) -> None:
    payload = {
        "status": "failed",
        "counts": {"passed": 0, "failed": 1},
        "models": [{"model": "analytics.orders", "duration_ms": 2}],
        "tests": [
            {"test": "analytics.orders.generated.not_null_id", "status": "failed", "row_count": 4}
        ],
    }
    monkeypatch.setattr(
        assets_module, "run_json", lambda *args, **kwargs: (payload, _invocation(returncode=1))
    )
    specs = {spec.key: spec for spec in build_workspace_asset_specs(tmp_path, transform_settings)}

    results = list(specs["orders"].run.fn(FakeRuntimeContext()))

    checks = [result for result in results if isinstance(result, CheckResult)]
    materializations = [result for result in results if isinstance(result, MaterializeResult)]
    assert checks[0].passed is False
    assert materializations[0].status == "failure"


def test_missing_test_report_is_a_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    transform_settings: TransformSettings,
    fake_workspace,
) -> None:
    payload = {"status": "skipped", "counts": {"skipped": 1}, "models": [], "tests": []}
    monkeypatch.setattr(assets_module, "run_json", lambda *args, **kwargs: (payload, _invocation()))
    specs = {spec.key: spec for spec in build_workspace_asset_specs(tmp_path, transform_settings)}

    results = list(specs["orders"].run.fn(FakeRuntimeContext()))

    check = next(result for result in results if isinstance(result, CheckResult))
    assert check.passed is False
    assert check.metadata["error"] == "test did not report a result"


def test_activation_follows_dbt_default_then_marker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("PHLO_TRANSFORMATION_PROVIDER", raising=False)
    assert has_dbt_project(tmp_path) is False
    assert provider_is_active(tmp_path) is True

    dbt_project = tmp_path / "workflows" / "transforms" / "dbt"
    dbt_project.mkdir(parents=True)
    (dbt_project / "dbt_project.yml").write_text("name: demo\n")
    assert provider_is_active(tmp_path) is False

    (tmp_path / ".phlo").mkdir()
    (tmp_path / ".phlo" / "transformation-provider").write_text("phlo-transform\n")
    assert provider_is_active(tmp_path) is True


def test_env_override_beats_project_marker(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / ".phlo").mkdir()
    (tmp_path / ".phlo" / "transformation-provider").write_text("transform\n")
    monkeypatch.setenv("PHLO_TRANSFORMATION_PROVIDER", "dbt")

    assert provider_is_active(tmp_path) is False


def test_adapter_args_use_trino_engine_metadata(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("PHLO_TRANSFORM_ADAPTER", raising=False)
    monkeypatch.setenv("PHLO_TRANSFORM_TRINO_ENDPOINT", "http://trino.internal:8080")
    settings = TransformSettings(_project_root=tmp_path)

    args = adapter_args(None, settings)

    assert args[:4] == ["--adapter", "trino", "--trino-endpoint", "http://trino.internal:8080"]


def test_adapter_args_use_explicit_duckdb(
    tmp_path: Path, transform_settings: TransformSettings
) -> None:
    args = adapter_args(None, transform_settings)

    assert args[:2] == ["--adapter", "duckdb"]


def test_transform_assets_skip_the_workspace_when_dbt_is_active(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    declared = AssetSpec(key="declared", group=None, description=None)
    called: list[bool] = []
    monkeypatch.setattr(assets_module, "get_transform_assets", lambda: [declared])
    monkeypatch.setattr(
        assets_module,
        "build_workspace_asset_specs",
        lambda *args, **kwargs: called.append(True) or [],
    )
    monkeypatch.setenv("PHLO_TRANSFORMATION_PROVIDER", "dbt")

    assert assets_module.build_transform_asset_specs(tmp_path) == [declared]
    assert called == []


def test_adapter_args_provision_the_runtime_ref_catalog(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from types import SimpleNamespace

    class CatalogManager:
        def provision_ref_query_catalog(self, ref: str) -> str:
            return "phlo_" + ref.replace("/", "_")

        def drop_ref_query_catalog(self, ref: str) -> None:
            """Match the query-engine ref catalog protocol."""

    resolution = SimpleNamespace(
        provider=CatalogManager(),
        metadata={"host": "trino", "port": 8080, "default_catalog": "iceberg"},
    )
    monkeypatch.setattr(assets_module, "_query_engine_resolution", lambda runtime: resolution)
    monkeypatch.setenv("PHLO_TRANSFORM_ADAPTER", "trino")
    settings = TransformSettings(_project_root=tmp_path)
    runtime = FakeRuntimeContext(tags={"phlo/ref": "ci/pr-1"})

    args = adapter_args(runtime, settings)

    assert "--catalog" in args
    assert args[args.index("--catalog") + 1] == "phlo_ci_pr-1"
    assert args[args.index("--environment") + 1] == "ci/pr-1"


def test_adapter_args_use_the_engine_catalog_without_a_ref(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from types import SimpleNamespace

    resolution = SimpleNamespace(
        provider=object(), metadata={"host": "trino", "port": 8080, "default_catalog": "iceberg"}
    )
    monkeypatch.setattr(assets_module, "_query_engine_resolution", lambda runtime: resolution)
    monkeypatch.setenv("PHLO_TRANSFORM_ADAPTER", "trino")
    settings = TransformSettings(_project_root=tmp_path)

    args = adapter_args(FakeRuntimeContext(), settings)

    assert args[args.index("--catalog") + 1] == "iceberg"
    assert "--environment" not in args
