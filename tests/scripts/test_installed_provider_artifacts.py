"""Focused contracts for the installed-provider artifact harness.

Loads scripts/verify_installed_provider_artifacts.py directly via importlib
and locks its workspace inventory, external-environment scrubbing, missing
artifact reporting, and healthcheck shard behavior.
"""

import importlib.util
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "installed_provider_artifacts", REPO_ROOT / "scripts" / "verify_installed_provider_artifacts.py"
)
assert SPEC and SPEC.loader
HARNESS = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = HARNESS
SPEC.loader.exec_module(HARNESS)


def test_workspace_inventory_contains_root_and_every_provider() -> None:
    packages = HARNESS.workspace_packages(REPO_ROOT)
    package_dirs = [path.parent for path in (REPO_ROOT / "packages").glob("*/pyproject.toml")]

    assert packages[0].name == "phlo"
    assert len(packages) == len(package_dirs) + 1
    assert {package.name for package in packages} == {"phlo"} | {
        package_dir.name for package_dir in package_dirs
    }


def test_external_environment_removes_workspace_import_fallbacks(monkeypatch) -> None:
    monkeypatch.setenv("PYTHONPATH", "/workspace/source")
    monkeypatch.setenv("PHLO_DEV_SOURCE", "/workspace/source")

    environment = HARNESS.external_environment()

    assert "PYTHONPATH" not in environment
    assert "PHLO_DEV_SOURCE" not in environment


def test_missing_inventory_entries_are_reported(tmp_path: Path) -> None:
    packages = [HARNESS.WorkspacePackage("phlo-example", tmp_path, (), {})]

    checks = HARNESS.assert_installed_artifacts(
        packages=packages, wheelhouse={}, installed={}, repo_root=tmp_path
    )

    assert checks["missing_packages"] == ["phlo-example"]
    assert checks["missing_wheels"] == ["phlo-example"]


def test_health_shard_marks_a_service_without_a_healthcheck_not_applicable(tmp_path: Path) -> None:
    results = HARNESS.health_shard(
        {"services": {"generated": {"build": {"context": "."}}}},
        consumer=tmp_path,
        shard_index=0,
        shard_count=1,
        env={},
    )

    assert results == [
        {"service": "generated", "status": "not_applicable", "detail": "no healthcheck"}
    ]
