"""Tests for the dependency refresh plan script: separating routine
patch bumps from risk-managed upgrades across package manifests."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = REPO_ROOT / "scripts" / "dependency_refresh_plan.py"
SPEC = importlib.util.spec_from_file_location("dependency_refresh_plan", SCRIPT_PATH)
assert SPEC is not None
assert SPEC.loader is not None
dependency_refresh_plan = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = dependency_refresh_plan
SPEC.loader.exec_module(dependency_refresh_plan)


def test_collect_plan_separates_patch_and_risk_lanes(tmp_path: Path) -> None:
    root = tmp_path
    package_dir = root / "packages" / "phlo-dbt"
    package_dir.mkdir(parents=True)
    (root / "pyproject.toml").write_text(
        """
[project]
dependencies = ["rich>=13.0"]

[dependency-groups]
dev = ["ruff>=0.14.1", "pytest>=8.4.2"]
""",
        encoding="utf-8",
    )
    (package_dir / "pyproject.toml").write_text(
        """
[project]
dependencies = ["dbt-core>=1.10.8", "pyarrow>=21.0.0"]
""",
        encoding="utf-8",
    )
    (root / "uv.lock").write_text(
        """
[[package]]
name = "ruff"
version = "0.14.1"

[[package]]
name = "dbt-core"
version = "1.10.8"

[[package]]
name = "pyarrow"
version = "21.0.0"

[[package]]
name = "rich"
version = "14.2.0"
""",
        encoding="utf-8",
    )

    plan = dependency_refresh_plan.collect_plan(root)

    assert [entry.name for entry in plan["patch"]] == ["pytest", "ruff"]
    assert [entry.name for entry in plan["risk-managed"]] == ["dbt-core", "pyarrow", "rich"]
    assert plan["risk-managed"][0].locked_version == "1.10.8"
    assert plan["risk-managed"][0].manifest_files == ["packages/phlo-dbt/pyproject.toml"]


def test_json_output_is_machine_readable(tmp_path: Path, capsys) -> None:
    (tmp_path / "pyproject.toml").write_text(
        """
[dependency-groups]
dev = ["ruff>=0.14.1"]
""",
        encoding="utf-8",
    )
    exit_code = dependency_refresh_plan.main(
        ["--repo-root", str(tmp_path), "--lane", "patch", "--format", "json"]
    )

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert [entry["name"] for entry in payload["patch"]] == ["ruff"]
    assert "risk-managed" not in payload


def test_check_validates_full_plan_when_displaying_patch_lane(tmp_path: Path) -> None:
    write_complete_plan_fixture(tmp_path)

    exit_code = dependency_refresh_plan.main(
        ["--repo-root", str(tmp_path), "--lane", "patch", "--check"]
    )

    assert exit_code == 0


def test_check_validates_full_plan_when_displaying_risk_managed_lane(tmp_path: Path) -> None:
    write_complete_plan_fixture(tmp_path)

    exit_code = dependency_refresh_plan.main(
        ["--repo-root", str(tmp_path), "--lane", "risk-managed", "--check"]
    )

    assert exit_code == 0


def test_validate_fails_when_patch_lane_is_missing() -> None:
    errors = dependency_refresh_plan.validate_plan(
        {
            "patch": [],
            "risk-managed": [],
        }
    )

    assert "Patch lane has no discovered dependencies." in errors


def write_complete_plan_fixture(root: Path) -> None:
    (root / "pyproject.toml").write_text(
        """
[project]
dependencies = ["rich>=13.0"]

[dependency-groups]
dev = ["ruff>=0.14.1"]
""",
        encoding="utf-8",
    )
    (root / "uv.lock").write_text(
        """
[[package]]
name = "ruff"
version = "0.14.1"

[[package]]
name = "rich"
version = "14.2.0"
""",
        encoding="utf-8",
    )
