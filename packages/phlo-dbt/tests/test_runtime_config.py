"""Tests dbt profile generation from runtime configuration.

DbtRuntimeConfig must render a Trino (or engine-specific) profile payload and
the resolve/write helpers must produce a profiles.yaml the dbt CLI can target,
including target-name resolution and environment overrides.
"""

from __future__ import annotations

import socket
import yaml
from types import SimpleNamespace

import phlo_dbt.runtime_config as runtime_config
from phlo_dbt.runtime_config import (
    DEFAULT_DBT_TARGET,
    DbtRuntimeConfig,
    ensure_dbt_profile,
    render_dbt_profile_yaml,
    resolve_dbt_profile_name,
    resolve_dbt_runtime_config,
    resolve_dbt_target_name,
    write_dbt_profile,
)


def test_dbt_runtime_config_to_profile_payload() -> None:
    config = DbtRuntimeConfig(
        target_name="ci",
        host="trino-ci",
        port=8090,
        catalog="iceberg_dev",
    )

    assert config.to_profile_payload() == {
        "phlo": {
            "target": "ci",
            "outputs": {
                "ci": {
                    "type": "trino",
                    "method": "none",
                    "user": "dagster",
                    "host": "trino-ci",
                    "port": 8090,
                    "catalog": "iceberg_dev",
                    "schema": "raw",
                    "http_scheme": "http",
                    "threads": 2,
                }
            },
        }
    }


def test_dbt_runtime_config_to_profile_payload_uses_engine_type() -> None:
    config = DbtRuntimeConfig(target_name="ci", engine_type="duckdb")

    assert config.to_profile_payload()["phlo"]["outputs"]["ci"]["type"] == "duckdb"


def test_resolve_dbt_target_name_prefers_explicit_target() -> None:
    runtime = SimpleNamespace(tags={"environment": "ci"})

    assert resolve_dbt_target_name(runtime, target="prod") == "prod"


def test_resolve_dbt_target_name_prefers_environment() -> None:
    runtime = SimpleNamespace(
        run_id="run-1",
        partition_key=None,
        tags={"environment": "ci", "dbt_target": "legacy"},
        resources={},
    )

    assert resolve_dbt_target_name(runtime) == "ci"


def test_resolve_dbt_target_name_falls_back_to_legacy_tag() -> None:
    runtime = SimpleNamespace(
        run_id="run-1",
        partition_key=None,
        tags={"dbt_target": "qa"},
        resources={},
    )

    assert resolve_dbt_target_name(runtime) == "qa"


def test_resolve_dbt_runtime_config_uses_ref_aware_catalog() -> None:
    runtime = SimpleNamespace(
        run_id="run-1",
        partition_key=None,
        tags={"environment": "dev", "phlo/ref": "feature_orders"},
        resources={},
    )

    config = resolve_dbt_runtime_config(runtime)

    assert config.target_name == "dev"
    assert config.catalog == "iceberg_feature_orders"


def test_resolve_dbt_runtime_config_prefers_wap_branch_catalog() -> None:
    runtime = SimpleNamespace(
        run_id="run-1",
        partition_key=None,
        tags={
            "environment": "dev",
            "phlo/wap_branch": "pipeline-run-run-1",
            "phlo/ref": "feature_orders",
        },
        resources={},
    )

    config = resolve_dbt_runtime_config(runtime)

    assert config.catalog == "iceberg_pipeline-run-run-1"


def test_resolve_dbt_runtime_config_provisions_wap_catalog_on_query_engine(
    monkeypatch,
    tmp_path,
) -> None:
    class QueryEngine:
        def __init__(self) -> None:
            self.provisioned_refs: list[str] = []

        def provision_ref_query_catalog(self, ref: str) -> str:
            self.provisioned_refs.append(ref)
            return f"provisioned_{ref}"

        def drop_ref_query_catalog(self, ref: str) -> None:
            return None

    query_engine = QueryEngine()
    runtime = SimpleNamespace(
        run_id="run-1",
        partition_key=None,
        tags={"phlo/wap_branch": "pipeline-run-isolated"},
        resources={},
    )
    monkeypatch.setattr(
        runtime_config,
        "resolve_capability",
        lambda capability_type, *, runtime: SimpleNamespace(provider=query_engine),
    )

    profile_path = ensure_dbt_profile(tmp_path / "profiles", runtime=runtime)
    payload = yaml.safe_load(profile_path.read_text(encoding="utf-8"))

    assert payload["phlo"]["outputs"]["dev"]["catalog"] == "provisioned_pipeline-run-isolated"
    assert query_engine.provisioned_refs == ["pipeline-run-isolated"]


def test_resolve_dbt_runtime_config_ignores_blank_wap_branch() -> None:
    runtime = SimpleNamespace(
        run_id="run-1",
        partition_key=None,
        tags={"phlo/wap_branch": "  ", "phlo/ref": "feature_orders"},
        resources={},
    )

    config = resolve_dbt_runtime_config(runtime)

    assert config.catalog == "iceberg_feature_orders"


def test_resolve_dbt_runtime_config_defaults_to_main_catalog() -> None:
    config = resolve_dbt_runtime_config()

    assert config.target_name == DEFAULT_DBT_TARGET
    assert config.engine_type == "trino"
    assert config.catalog == "iceberg"


def test_resolve_dbt_runtime_config_uses_project_profile_name(tmp_path, monkeypatch) -> None:
    project_dir = tmp_path / "workflows" / "transforms" / "dbt"
    project_dir.mkdir(parents=True)
    (project_dir / "dbt_project.yml").write_text(
        'name: "workshop_transforms"\nprofile: "workshop_transforms"\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(
        runtime_config,
        "get_dbt_settings",
        lambda: SimpleNamespace(
            dbt_project_path=project_dir,
            dbt_query_catalog="iceberg",
            dbt_query_engine_type="trino",
            dbt_query_user="dagster",
            dbt_query_host="trino",
            dbt_query_port=8080,
            dbt_query_schema="raw",
            dbt_query_threads=2,
            dbt_query_http_scheme="http",
            dbt_query_auth_method="none",
        ),
    )

    config = resolve_dbt_runtime_config()

    assert config.profile_name == "workshop_transforms"


def test_resolve_dbt_runtime_config_resolves_unreachable_trino_host(tmp_path, monkeypatch) -> None:
    phlo_dir = tmp_path / ".phlo"
    phlo_dir.mkdir()
    (phlo_dir / ".env.local").write_text("TRINO_PORT=18080\n")
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("TRINO_PORT", raising=False)
    runtime_config.get_dbt_settings.cache_clear()

    def raise_unresolvable(_host: str) -> str:
        raise socket.gaierror()

    monkeypatch.setattr("phlo.config.network.socket.gethostbyname", raise_unresolvable)

    config = resolve_dbt_runtime_config()

    assert config.host == "localhost"
    assert config.port == 18080
    runtime_config.get_dbt_settings.cache_clear()


def test_resolve_dbt_target_name_defaults_to_canonical_default() -> None:
    assert resolve_dbt_target_name() == DEFAULT_DBT_TARGET


def test_render_dbt_profile_yaml_returns_expected_yaml() -> None:
    config = DbtRuntimeConfig(target_name="ci", catalog="iceberg_feature_orders")

    payload = yaml.safe_load(render_dbt_profile_yaml(config))

    assert payload["phlo"]["target"] == "ci"
    assert payload["phlo"]["outputs"]["ci"]["catalog"] == "iceberg_feature_orders"


def test_write_dbt_profile_writes_profiles_file(tmp_path) -> None:
    config = DbtRuntimeConfig(target_name="dev")

    profile_path = write_dbt_profile(config, tmp_path / "profiles")

    assert profile_path.name == "profiles.yml"
    assert profile_path.exists()
    payload = yaml.safe_load(profile_path.read_text(encoding="utf-8"))
    assert payload["phlo"]["outputs"]["dev"]["host"] == "trino"


def test_ensure_dbt_profile_uses_runtime_target_and_ref(tmp_path) -> None:
    runtime = SimpleNamespace(
        run_id="run-1",
        partition_key=None,
        tags={"environment": "ci", "phlo/ref": "feature_orders"},
        resources={},
    )

    profile_path = ensure_dbt_profile(tmp_path / "profiles", runtime=runtime)

    payload = yaml.safe_load(profile_path.read_text(encoding="utf-8"))
    assert payload["phlo"]["target"] == "ci"
    assert payload["phlo"]["outputs"]["ci"]["catalog"] == "iceberg_feature_orders"


def test_resolve_dbt_profile_name_prefers_project_declaration(tmp_path) -> None:
    project_dir = tmp_path / "workflows" / "transforms" / "dbt"
    project_dir.mkdir(parents=True)
    (project_dir / "dbt_project.yml").write_text(
        'name: "workshop_transforms"\nprofile: "workshop_transforms"\n',
        encoding="utf-8",
    )

    assert resolve_dbt_profile_name(project_dir) == "workshop_transforms"


def test_resolve_dbt_profile_name_falls_back_when_project_missing(tmp_path) -> None:
    assert resolve_dbt_profile_name(tmp_path / "missing") == "phlo"
