"""Verify deterministic generated references and drift detection."""

from __future__ import annotations

import importlib.util
import json
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

    endpoint_keys = {(item["method"], item["path"]) for item in http["endpoints"]}
    assert http["endpoint_count"] == len(http["endpoints"])
    assert len(endpoint_keys) == len(http["endpoints"])
    assert ("GET", "/health") in endpoint_keys
