"""Unit tests for the dbt spec translator and DbtTransformer runtime behavior."""

import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest
from phlo.hooks.events import TelemetryEvent, TransformEvent
from phlo.logging import get_logger
from phlo_dbt.transformer import DbtTransformer, ensure_dbt_manifest
from phlo_dbt.translator import DbtSpecTranslator


def test_custom_dbt_translator_asset_key_model() -> None:
    """Verifies model resources map directly to their model-name asset key."""
    translator = DbtSpecTranslator()
    asset_key = translator.get_asset_key(
        {"name": "stg_nightscout_entries", "resource_type": "model"}
    )
    assert asset_key == "stg_nightscout_entries"


def test_custom_dbt_translator_asset_key_source_dagster_assets_maps_to_dlt() -> None:
    """Verifies Dagster source assets map to DLT-prefixed asset keys."""
    translator = DbtSpecTranslator()
    asset_key = translator.get_asset_key(
        {"resource_type": "source", "source_name": "dagster_assets", "name": "entries"}
    )
    assert asset_key == "dlt_entries"


def test_custom_dbt_translator_asset_key_raw_source_maps_to_dlt() -> None:
    """Verifies raw dbt sources map to corresponding Phlo DLT assets."""
    translator = DbtSpecTranslator()
    asset_key = translator.get_asset_key(
        {"resource_type": "source", "source_name": "raw_lims", "name": "qc_safe_results"}
    )
    assert asset_key == "dlt_qc_safe_results"


def test_custom_dbt_translator_asset_key_source_meta_override() -> None:
    """Verifies projects can explicitly map dbt sources to non-default asset keys."""
    translator = DbtSpecTranslator()
    asset_key = translator.get_asset_key(
        {
            "resource_type": "source",
            "source_name": "warehouse",
            "name": "orders",
            "meta": {"phlo_asset_key": "external_orders"},
        }
    )
    assert asset_key == "external_orders"


def test_ensure_dbt_manifest_uses_parse_for_discovery(monkeypatch, tmp_path: Path) -> None:
    """dbt asset discovery should not require a live query engine connection."""
    project_dir = tmp_path / "dbt"
    profiles_dir = project_dir / "profiles"
    target_dir = project_dir / "target"
    profiles_dir.mkdir(parents=True)
    target_dir.mkdir()
    (project_dir / "dbt_project.yml").write_text("name: demo\n")

    captured: list[list[str]] = []

    def fake_run(cmd: list[str], **_kwargs):
        captured.append(cmd)
        (target_dir / "manifest.json").write_text('{"nodes": {}, "sources": {}}')
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr("phlo_dbt.transformer.subprocess.run", fake_run)
    monkeypatch.setattr("phlo_dbt.transformer.ensure_dbt_profile", lambda _profiles_dir: None)

    assert ensure_dbt_manifest(project_dir, profiles_dir) is True
    assert captured[0][:2] == ["dbt", "parse"]
    assert "--project-dir" in captured[0]
    assert str(project_dir) in captured[0]
    assert "--profiles-dir" in captured[0]


@pytest.mark.parametrize(
    ("props", "expected"),
    [
        ({"name": "anything", "fqn": ["project", "bronze", "stg_entries"]}, "bronze"),
        ({"name": "anything", "fqn": ["project", "staging", "stg_entries"]}, "silver"),
        ({"name": "anything", "path": "models/silver/stg_entries.sql"}, "silver"),
        ({"name": "anything", "path": "models/marts/mrt_patient_summary.sql"}, "marts"),
    ],
)
def test_custom_dbt_translator_group_name_prefers_folder(props: dict, expected: str) -> None:
    """Verifies folder-derived grouping takes precedence when path/fqn is available."""
    translator = DbtSpecTranslator()
    assert translator.get_group_name(props) == expected


@pytest.mark.parametrize(
    ("model_name", "expected"),
    [
        ("stg_nightscout_entries", "silver"),
        ("dim_patients", "gold"),
        ("fct_glucose_readings", "gold"),
        ("mrt_patient_summary", "marts"),
        ("unknown_model", "transform"),
    ],
)
def test_custom_dbt_translator_group_name_fallbacks(model_name: str, expected: str) -> None:
    """Verifies naming-convention fallback mapping for group assignment."""
    translator = DbtSpecTranslator()
    assert translator.get_group_name({"name": model_name}) == expected


def test_custom_dbt_translator_description_does_not_embed_compiled_sql_by_default() -> None:
    """Verifies compiled SQL is excluded from description text by default."""
    translator = DbtSpecTranslator()
    description = translator.get_description(
        {"name": "model_x", "description": "Doc", "compiled_code": "select 1 as x"}
    )
    assert "select 1 as x" not in description


def test_custom_dbt_translator_metadata_compiled_sql_is_capped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verifies metadata compiled SQL payload is truncated at configured limit."""
    monkeypatch.setenv("PHLO_DBT_COMPILED_SQL_MAX_BYTES", "64")
    translator = DbtSpecTranslator()

    compiled_code = "select '" + ("x" * 10_000) + "' as big"
    metadata = translator.get_metadata({"name": "model_x", "compiled_code": compiled_code})

    assert "phlo/compiled_sql" in metadata
    assert metadata["phlo/compiled_sql_truncated"] is True
    assert "TRUNCATED compiled SQL" in metadata["phlo/compiled_sql"]


def test_run_transform_skip_build_returns_success(tmp_path: Path) -> None:
    """Verifies skip-build mode returns success without invoking dbt commands."""
    transformer = DbtTransformer(
        context=None,
        logger=get_logger("test_dbt_transformer_skip_build"),
        project_dir=tmp_path,
        profiles_dir=tmp_path,
    )
    run_calls: list[list[str]] = []

    def fake_run_command(args: list[str], env: dict[str, str] | None = None):
        """Capture dbt command calls and return a synthetic success result."""
        run_calls.append(args)
        return subprocess.CompletedProcess(
            args=["dbt"] + args,
            returncode=0,
            stdout="PASS=1 WARN=0 ERROR=0 SKIP=0 TOTAL=1",
            stderr="",
        )

    transformer._run_command = fake_run_command  # type: ignore[method-assign]

    result = transformer.run_transform(parameters={"skip_build": True, "generate_docs": False})

    assert result.status == "success"
    assert result.models_built == 0
    assert result.models_failed == 0
    assert result.tests_passed == 0
    assert result.tests_failed == 0
    assert result.metadata["dbt_output"] == ""
    assert run_calls == []


def test_run_transform_writes_canonical_profile(tmp_path: Path) -> None:
    """Verifies runtime execution materializes canonical `profiles.yml` before dbt runs."""
    transformer = DbtTransformer(
        context=SimpleNamespace(
            run_id="run-1",
            partition_key=None,
            tags={"environment": "ci", "phlo/ref": "feature_orders"},
            resources={},
        ),
        logger=get_logger("test_dbt_transformer_profile_write"),
        project_dir=tmp_path,
        profiles_dir=tmp_path / "profiles",
        target="ci",
    )

    def fake_run_command(args: list[str], env: dict[str, str] | None = None):
        return subprocess.CompletedProcess(
            args=["dbt"] + args,
            returncode=0,
            stdout="PASS=1 WARN=0 ERROR=0 SKIP=0 TOTAL=1",
            stderr="",
        )

    transformer._run_command = fake_run_command  # type: ignore[method-assign]

    result = transformer.run_transform(parameters={"generate_docs": False})

    assert result.status == "success"
    profile_payload = (tmp_path / "profiles" / "profiles.yml").read_text(encoding="utf-8")
    assert "target: ci" in profile_payload
    assert "catalog: iceberg_feature_orders" in profile_payload


def test_run_transform_counts_models_and_tests_from_run_results(tmp_path: Path) -> None:
    """Verifies model/test counts are derived from dbt run_results artifacts."""
    transformer = DbtTransformer(
        context=None,
        logger=get_logger("test_dbt_transformer_run_results_counts"),
        project_dir=tmp_path,
        profiles_dir=tmp_path,
    )
    (tmp_path / "target").mkdir(parents=True, exist_ok=True)
    (tmp_path / "target" / "run_results.json").write_text(
        """
{
  "results": [
    {"resource_type": "model", "status": "success"},
    {"resource_type": "model", "status": "error"},
    {"resource_type": "test", "status": "pass"},
    {"resource_type": "test", "status": "fail"},
    {"unique_id": "test.pkg.fallback_type", "status": "pass"},
    {"resource_type": "test", "status": "skipped"}
  ]
}
""".strip()
    )

    def fake_run_command(args: list[str], env: dict[str, str] | None = None):
        """Return a synthetic successful dbt run command result."""
        return subprocess.CompletedProcess(
            args=["dbt"] + args,
            returncode=0,
            stdout="PASS=3 WARN=0 ERROR=2 SKIP=1 TOTAL=6",
            stderr="",
        )

    transformer._run_command = fake_run_command  # type: ignore[method-assign]

    result = transformer.run_transform(parameters={"generate_docs": False})

    assert result.status == "success"
    assert result.models_built == 1
    assert result.models_failed == 1
    assert result.tests_passed == 2
    assert result.tests_failed == 1


def test_run_transform_preserves_build_results_before_docs_overwrites_them(tmp_path: Path) -> None:
    """The build artifact remains available after dbt docs generate overwrites its file."""
    transformer = DbtTransformer(
        context=None,
        logger=get_logger("test_dbt_transformer_build_results_before_docs"),
        project_dir=tmp_path,
        profiles_dir=tmp_path,
    )
    target_dir = tmp_path / "target"
    target_dir.mkdir()
    build_results = {
        "args": {"which": "build"},
        "results": [
            {"unique_id": "model.phlo.product_dimension", "status": "success"},
            {"unique_id": "test.phlo.not_null_product_dimension_sku", "status": "pass"},
        ],
    }
    docs_results = {
        "args": {"which": "generate"},
        "results": [{"unique_id": "test.phlo.inventory_balances", "status": "pass"}],
    }
    run_calls: list[list[str]] = []

    def fake_run_command(args: list[str], env: dict[str, str] | None = None):
        run_calls.append(args)
        if args[:1] == ["build"]:
            (target_dir / "run_results.json").write_text(
                json.dumps(build_results), encoding="utf-8"
            )
        else:
            assert args[:2] == ["docs", "generate"]
            (target_dir / "run_results.json").write_text(json.dumps(docs_results), encoding="utf-8")
        return subprocess.CompletedProcess(
            args=["dbt"] + args,
            returncode=0,
            stdout="PASS=2 WARN=0 ERROR=0 SKIP=0 TOTAL=2",
            stderr="",
        )

    transformer._run_command = fake_run_command  # type: ignore[method-assign]

    result = transformer.run_transform(parameters={"indirect_selection": "cautious"})

    assert result.tests_passed == 1
    assert transformer.build_run_results == build_results
    assert json.loads((target_dir / "run_results.json").read_text(encoding="utf-8")) == docs_results
    assert ["--indirect-selection", "cautious"] == run_calls[0][-2:]


def test_run_transform_keeps_failed_build_results(tmp_path: Path) -> None:
    """A failing dbt build still exposes its test evidence to the asset runner."""
    transformer = DbtTransformer(
        context=None,
        logger=get_logger("test_dbt_transformer_failed_build_results"),
        project_dir=tmp_path,
        profiles_dir=tmp_path,
    )
    target_dir = tmp_path / "target"
    target_dir.mkdir()
    failed_results = {
        "results": [
            {
                "unique_id": "test.phlo.not_null_product_dimension_sku",
                "status": "fail",
                "failures": 1,
            }
        ]
    }

    def fake_run_command(args: list[str], env: dict[str, str] | None = None):
        assert args[:1] == ["build"]
        (target_dir / "run_results.json").write_text(json.dumps(failed_results), encoding="utf-8")
        return subprocess.CompletedProcess(
            args=["dbt"] + args,
            returncode=1,
            stdout="PASS=0 WARN=0 ERROR=1 SKIP=0 TOTAL=1",
            stderr="test failed",
        )

    transformer._run_command = fake_run_command  # type: ignore[method-assign]

    result = transformer.run_transform(parameters={"generate_docs": False})

    assert result.status == "failure"
    assert transformer.build_run_results == failed_results


def test_run_transform_emits_runtime_correlation(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    class RecordingBus:
        def __init__(self) -> None:
            self.events: list[object] = []

        def emit(self, event: object) -> None:
            self.events.append(event)

    bus = RecordingBus()
    monkeypatch.setattr("phlo.hooks.emitters.get_hook_bus", lambda: bus)

    transformer = DbtTransformer(
        context=SimpleNamespace(
            run_id="run-44",
            partition_key="2026-03-08",
            job_name="daily_transform",
            asset_key="mart_orders",
            tags={"environment": "ci"},
            resources={},
        ),
        logger=get_logger("test_dbt_transformer_correlation"),
        project_dir=tmp_path,
        profiles_dir=tmp_path,
        target="ci",
    )

    def fake_run_command(args: list[str], env: dict[str, str] | None = None):
        return subprocess.CompletedProcess(
            args=["dbt"] + args,
            returncode=0,
            stdout="PASS=1 WARN=0 ERROR=0 SKIP=0 TOTAL=1",
            stderr="",
        )

    transformer._run_command = fake_run_command  # type: ignore[method-assign]

    result = transformer.run_transform(
        partition_key="2026-03-08",
        parameters={"generate_docs": False},
    )

    assert result.status == "success"
    transform_events = [event for event in bus.events if isinstance(event, TransformEvent)]
    telemetry_events = [event for event in bus.events if isinstance(event, TelemetryEvent)]
    assert transform_events
    assert telemetry_events
    assert transform_events[0].correlation.run_id == "run-44"
    assert transform_events[0].correlation.partition_key == "2026-03-08"
    assert transform_events[0].correlation.job_name == "daily_transform"
    assert telemetry_events[0].correlation.run_id == "run-44"
    assert telemetry_events[0].correlation.partition_key == "2026-03-08"


def test_run_transform_falls_back_when_run_results_missing(tmp_path: Path) -> None:
    """Verifies summary parsing fallback when run_results.json is absent."""
    transformer = DbtTransformer(
        context=None,
        logger=get_logger("test_dbt_transformer_missing_run_results"),
        project_dir=tmp_path,
        profiles_dir=tmp_path,
    )

    def fake_run_command(args: list[str], env: dict[str, str] | None = None):
        """Return a synthetic successful dbt run command result."""
        return subprocess.CompletedProcess(
            args=["dbt"] + args,
            returncode=0,
            stdout="PASS=2 WARN=0 ERROR=1 SKIP=0 TOTAL=3",
            stderr="",
        )

    transformer._run_command = fake_run_command  # type: ignore[method-assign]

    result = transformer.run_transform(parameters={"generate_docs": False})

    assert result.status == "success"
    assert result.models_built == -1
    assert result.models_failed == -1
    assert result.tests_passed == -1
    assert result.tests_failed == -1
    assert result.metadata["counts_source"] == "summary_only_combined"


def test_ensure_dbt_manifest_strips_colliding_project_dir_env(tmp_path, monkeypatch) -> None:
    """DBT_PROJECT_DIR must not leak into the child dbt process.

    The variable doubles as phlo-dbt's project-dir setting; when a project
    customizes it, dbt resolves its default --project-dir from the leaked
    value relative to the working directory and parse fails with
    "Path ... does not exist".
    """
    import json

    from phlo_dbt.transformer import ensure_dbt_manifest

    project = tmp_path / "dbt"
    (project / "target").mkdir(parents=True)
    (project / "profiles").mkdir()

    captured: dict = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        captured["env"] = kwargs.get("env") or {}
        manifest = project / "target" / "manifest.json"
        manifest.write_text(json.dumps({"nodes": {}, "sources": {}}))
        from types import SimpleNamespace

        return SimpleNamespace(returncode=0)

    monkeypatch.setattr("phlo_dbt.transformer.subprocess.run", fake_run)
    monkeypatch.setenv("DBT_PROJECT_DIR", "workflows/operational_marts/dbt")

    assert ensure_dbt_manifest(project, project / "profiles") is True
    assert "--project-dir" in captured["cmd"]
    assert str(project) in captured["cmd"]
    assert "DBT_PROJECT_DIR" not in captured["env"]


def test_run_command_strips_colliding_project_dir_env(tmp_path) -> None:
    """Runtime dbt invocations also drop the phlo-owned DBT_PROJECT_DIR."""

    from phlo_dbt.transformer import DbtTransformer

    project = tmp_path / "dbt"
    project.mkdir()

    captured: dict = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        captured["env"] = kwargs.get("env") or {}
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    from phlo.logging import get_logger

    transformer = DbtTransformer(
        context=None,
        logger=get_logger("test_dbt_transformer_run_command"),
        project_dir=project,
        profiles_dir=project / "profiles",
    )
    monkeypatch_env = {
        "DBT_PROJECT_DIR": "workflows/operational_marts/dbt",
        "PATH": "/custom/test-bin",
    }

    import phlo_dbt.transformer as mod

    original_run = mod.subprocess.run
    mod.subprocess.run = fake_run
    try:
        result = transformer._run_command(["build"], env=monkeypatch_env)
    finally:
        mod.subprocess.run = original_run

    assert result.returncode == 0
    assert "DBT_PROJECT_DIR" not in captured["env"]
    assert captured["env"]["PATH"] == "/custom/test-bin"  # explicit env values are preserved
