"""Tests for the version-drift check.

Each distribution's pyproject is the single version authority; source
``__version__`` literals, hand-maintained registry version columns, and
support-manifest release pins that disagree with metadata must fail.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

SCRIPT_PATH = Path(__file__).parents[2] / "scripts" / "check_version_drift.py"
SPEC = importlib.util.spec_from_file_location("check_version_drift", SCRIPT_PATH)
assert SPEC and SPEC.loader
check_version_drift = importlib.util.module_from_spec(SPEC)
sys.modules["check_version_drift"] = check_version_drift
SPEC.loader.exec_module(check_version_drift)


def test_workspace_distributions_covers_root_and_packages():
    distributions = check_version_drift.workspace_distributions()

    assert "phlo" in distributions
    assert "phlo-dagster" in distributions
    assert all(value for value in distributions.values())


def test_version_literal_detection_distinguishes_dynamic_from_literal():
    dynamic = check_version_drift.DYNAMIC_VERSION_RE
    literal = '__version__ = "0.14.0"'
    derived = '__version__ = version("phlo")'

    assert dynamic.match(derived + "\n")
    assert not dynamic.match(literal + "\n")


def test_registry_version_column_detection(tmp_path: Path):
    registry = {"plugins": {"alpha": {"type": "service", "version": "0.1.0"}}}
    errors = []
    for name, entry in registry["plugins"].items():
        if "version" in entry:
            errors.append(name)
    assert errors == ["alpha"]

    clean = {"plugins": {"alpha": {"type": "service"}}}
    assert [n for n, e in clean["plugins"].items() if "version" in e] == []


def test_support_manifest_release_set_matches_package_metadata():
    distributions = check_version_drift.workspace_distributions()
    manifest = json.loads((check_version_drift.SUPPORT_MANIFEST_PATH).read_text(encoding="utf-8"))

    for pinned in manifest["release_set"]["packages"]:
        if pinned["name"] in distributions:
            assert pinned["version"] == distributions[pinned["name"]], pinned["name"]
    assert manifest["current_release"]["version"] == distributions["phlo"]


@pytest.mark.parametrize("registry_path", check_version_drift.REGISTRY_PATHS)
def test_plugin_registries_carry_no_version_column(registry_path: str):
    registry = json.loads((check_version_drift.ROOT / registry_path).read_text(encoding="utf-8"))

    offending = [name for name, entry in registry["plugins"].items() if "version" in entry]
    assert offending == []


def test_main_returns_zero_on_current_tree(monkeypatch: pytest.MonkeyPatch, capsys):
    monkeypatch.setattr(check_version_drift.sys, "argv", ["check_version_drift.py"])

    exit_code = check_version_drift.main()

    assert exit_code == 0
    assert "none across" in capsys.readouterr().out
