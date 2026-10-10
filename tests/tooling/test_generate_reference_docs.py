"""Verify deterministic generated references and drift detection."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "generate_reference_docs", ROOT / "scripts/generate_reference_docs.py"
)
assert SPEC is not None and SPEC.loader is not None
generator = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(generator)


def test_write_outputs_detects_drift_and_writes_atomically(tmp_path: Path) -> None:
    target = tmp_path / "nested/reference.md"
    rendered = {target: "current\n"}
    assert generator.write_outputs(rendered, check=True) == [target]
    assert not target.exists()
    assert generator.write_outputs(rendered, check=False) == [target]
    assert target.read_text(encoding="utf-8") == "current\n"
    assert generator.write_outputs(rendered, check=True) == []
    assert list(target.parent.glob(f".{target.name}.*")) == []


def test_support_docs_distinguishes_target_and_current_state() -> None:
    data, markdown = generator.support_docs(ROOT)
    manifest = json.loads((ROOT / "registry/support/v1.json").read_text(encoding="utf-8"))
    expected_count = sum(len(manifest[key]) for key in ("packages", "services", "capabilities"))
    assert data["component_count"] == expected_count
    item = next(item for item in data["components"] if item["component"] == "package:phlo-api")
    assert (item["target_profile"], item["current_maturity"], item["readiness"]) == (
        "blessed_core",
        "alpha",
        "blocked",
    )
    assert "security" in item["blockers"]
    assert "Target profile" in markdown


@pytest.mark.parametrize(
    "builder", [generator.cli_docs, generator.settings_docs, generator.http_docs]
)
def test_live_inventory_is_deterministic(builder) -> None:
    assert builder(ROOT) == builder(ROOT)


def test_live_inventories_are_internally_consistent() -> None:
    cli, _ = generator.cli_docs(ROOT)
    settings, _ = generator.settings_docs(ROOT)
    http, _ = generator.http_docs(ROOT)

    command_paths = [item["command"] for item in cli["commands"]]
    assert cli["command_count"] == len(command_paths)
    assert command_paths[0] == "phlo"
    assert len(command_paths) == len(set(command_paths))

    assert settings["model_count"] == len(settings["models"])
    assert settings["field_count"] == sum(len(model["fields"]) for model in settings["models"])
    assert settings["unavailable_model_count"] == sum(
        model["status"] != "introspected" for model in settings["models"]
    )
    process = next(model for model in settings["models"] if model["class"] == "CoreProcessSettings")
    groups = next(field for field in process["fields"] if field["name"] == "phlo_auth_groups")
    assert groups["default"] == []

    endpoint_keys = {(item["method"], item["path"]) for item in http["endpoints"]}
    assert http["endpoint_count"] == len(http["endpoints"])
    assert len(endpoint_keys) == len(http["endpoints"])
    assert ("GET", "/health") in endpoint_keys


def test_migrated_settings_inventory_is_complete() -> None:
    """Every original supported read and explicitly discovered dynamic key is documented."""
    original = json.loads((ROOT / "tests/tooling/phlo_env_reads_inventory.json").read_text())
    internal = json.loads((ROOT / "tests/tooling/phlo_env_reads_allowlist.json").read_text())
    supported = {key for keys in original.values() for key in keys} - {
        key for keys in internal.values() for key in keys
    }
    supported |= {
        "PHLO_AUTH_JWT_SECRET",
        "PHLO_AUTH_JWT_ISSUER",
        "PHLO_AUTH_JWT_AUDIENCE",
        "PHLO_AUTH_JWT_JWKS_URL",
        "PHLO_AUTH_JWT_GROUPS_CLAIM",
        "PHLO_AUTH_JWT_CA_FILE",
        "PHLO_AUTH_JWT_LEEWAY",
        "PHLO_AUTH_JWT_JWKS_CACHE_TTL_SECONDS",
        "PHLO_AUTH_JWT_REFRESH_MIN_INTERVAL_SECONDS",
        "PHLO_AUTH_JWT_ALLOW_INSECURE_HTTP",
        "PHLO_DAGSTER_OIDC_LEEWAY_SECONDS",
        "PHLO_DAGSTER_OIDC_JWKS_CACHE_TTL_SECONDS",
        "PHLO_DAGSTER_OIDC_REFRESH_MIN_INTERVAL_SECONDS",
        "PHLO_DAGSTER_OIDC_REQUIRED",
        "PHLO_PROMOTION_PROD_WORKTREE",
        "PHLO_PROMOTION_STAGING_WORKTREE",
        "PHLO_PROMOTION_PROD_REF",
        "PHLO_PROMOTION_STAGING_REF",
        "PHLO_PROMOTION_DAGSTER_LOCATION",
        "PHLO_V1_PREVIEW_TRINO_PASSWORD_PROD",
        "PHLO_V1_PREVIEW_TRINO_PASSWORD_STAGING",
        "PHLO_REGISTRY_DB_URL",
        "PHLO_LINEAGE_DB_URL",
        "PHLO_NO_AUTO_DISCOVER",
        "PHLO_DBT_INCLUDE_COMPILED_SQL_IN_DESCRIPTION",
        "PHLO_DBT_COMPILED_SQL_MAX_BYTES",
        "PHLO_MCP_ENABLE_WRITE_TOOLS",
        "PHLO_OBSERVE_PRETTY",
        "PHLO_OBSERVE_PRETTY_VERBOSE",
        "PHLO_OBSERVE_ENABLED",
        "PHLO_LOG_LEVEL",
        "PHLO_SERVICE_NONCE_DB_URL",
    }
    settings, markdown = generator.settings_docs(ROOT)
    aliases = set()
    for model in settings["models"]:
        assert model["status"] == "introspected", model
        for field in model["fields"]:
            aliases.update(field["environment_names"])
    assert not supported - aliases
    markdown_names = {
        name
        for row in markdown.splitlines()
        if row.startswith("| `")
        for name in json.loads(row.split("|")[2].strip().strip("`"))
    }
    assert not supported - markdown_names


def test_supported_phlo_environment_names_have_one_declaring_owner() -> None:
    settings, _ = generator.settings_docs(ROOT)
    owners = {}
    for model in settings["models"]:
        assert model["status"] == "introspected", model
        for field in model["fields"]:
            for name in field["environment_names"]:
                if not name.startswith("PHLO_"):
                    continue
                owner = (model["package"], model["class"], field["name"])
                assert name not in owners, (name, owners.get(name), owner)
                owners[name] = owner
    assert owners["PHLO_ENVIRONMENT"] == ("phlo", "Settings", "phlo_environment")
    assert owners["PHLO_LOG_LEVEL"] == ("phlo", "Settings", "phlo_log_level")
    assert owners["PHLO_LINEAGE_DB_URL"] == ("phlo", "LineageDatabaseSettings", "lineage_db_url")
    assert owners["PHLO_MCP_TRACE_FILE"] == ("phlo-mcp", "McpTraceSettings", "trace_file")


def test_settings_inventory_distinguishes_inheritance_from_duplicate_declarations(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "fixture-owner"\n')
    source = tmp_path / "src"
    source.mkdir()
    module_path = source / "settings_owner_fixture.py"
    module_path.write_text(
        """
from pydantic import Field
from pydantic_settings import BaseSettings

class Owner(BaseSettings):
    flag: bool = Field(False, validation_alias="PHLO_OWNER_FIXTURE")

class Inherited(Owner, BaseSettings):
    pass

class DuplicateOwner(BaseSettings):
    other_flag: bool = Field(True, validation_alias="PHLO_OWNER_FIXTURE")
"""
    )
    spec = importlib.util.spec_from_file_location("settings_owner_fixture", module_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, module)
    monkeypatch.syspath_prepend(str(source))
    spec.loader.exec_module(module)

    settings, _ = generator.settings_docs(tmp_path)
    models = {model["class"]: model for model in settings["models"]}
    assert all(model["status"] == "introspected" for model in models.values())
    assert models["Inherited"]["fields"] == []
    assert models["Inherited"]["inherited_fields"] == {"flag": "settings_owner_fixture.Owner"}
    # A genuine second declaration must remain visible to the uniqueness audit.
    declarations = [
        (model["class"], field["name"], field["default"])
        for model in settings["models"]
        for field in model["fields"]
        if "PHLO_OWNER_FIXTURE" in field["environment_names"]
    ]
    assert declarations == [("DuplicateOwner", "other_flag", True), ("Owner", "flag", False)]
