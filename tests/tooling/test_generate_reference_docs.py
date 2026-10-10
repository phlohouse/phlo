"""Verify deterministic generated references and drift detection."""

from __future__ import annotations

import importlib.util
import json
import shutil
import sys
from pathlib import Path

import pytest
import yaml

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
    "builder",
    [generator.cli_docs, generator.settings_docs, generator.http_docs, generator.topology_docs],
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

    endpoint_keys = {(item["method"], item["path"]) for item in http["endpoints"]}
    assert http["endpoint_count"] == len(http["endpoints"])
    assert len(endpoint_keys) == len(http["endpoints"])
    assert ("GET", "/health") in endpoint_keys


@pytest.mark.parametrize("mutation", ["identity", "port", "dependency"])
def test_service_manifest_disagreement_fails_read_only_projection_check(
    tmp_path: Path,
    mutation: str,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Mutate a real service.yaml, not the expected output or a mocked parser."""
    for source in (ROOT / "packages").glob("*/src/*/*.yaml"):
        destination = tmp_path / source.relative_to(ROOT)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, destination)
    support = tmp_path / "registry/support/v1.json"
    support.parent.mkdir(parents=True)
    shutil.copyfile(ROOT / "registry/support/v1.json", support)
    monkeypatch.setattr(sys, "path", list(sys.path))
    monkeypatch.setattr(generator, "ROOT", tmp_path)
    assert generator.main([]) == 0
    assert generator.main(["--check"]) == 0
    target = tmp_path / "docs/reference/generated/service-topology.json"
    original = target.read_text(encoding="utf-8")
    manifest_path = tmp_path / "packages/phlo-api/src/phlo_api/service.yaml"
    manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    if mutation == "identity":
        # Rename both ends so the graph remains valid. Only projection drift fails.
        manifest["name"] = "phlo-api-renamed"
        dependent = tmp_path / "packages/phlo-observatory/src/phlo_observatory/service.yaml"
        dependent.write_text(
            dependent.read_text(encoding="utf-8").replace("- phlo-api", "- phlo-api-renamed"),
            encoding="utf-8",
        )
    elif mutation == "port":
        manifest["compose"]["ports"] = ["${PHLO_API_PORT:-4000}:4001"]
    else:
        manifest["depends_on"].append("trino")
    manifest_path.write_text(yaml.safe_dump(manifest), encoding="utf-8")
    with pytest.raises(SystemExit) as failure:
        generator.main(["--check"])
    assert failure.value.code == 1
    assert "docs/reference/generated/service-topology.json" in capsys.readouterr().err
    assert target.read_text(encoding="utf-8") == original


def test_service_port_default_disagreement_is_not_generated_away(tmp_path: Path) -> None:
    manifest_path = tmp_path / "packages/provider/src/provider/service.yaml"
    manifest_path.parent.mkdir(parents=True)
    manifest_path.write_text(
        "name: example\ncompose:\n  ports: ['${EXAMPLE_PORT:-4100}:80']\n"
        "env_vars:\n  EXAMPLE_PORT:\n    default: 4200\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="EXAMPLE_PORT default disagrees"):
        generator.topology_docs(tmp_path)
