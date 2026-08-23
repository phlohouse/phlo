"""Compatibility epoch enforcement between provider wheels and phlo core.

Builds real wheels and verifies providers declare the current minor epoch,
older cores reject newer providers before import, and declared minimums
install and import cleanly.
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
import tomllib
import zipfile
from pathlib import Path

ROOT = Path(__file__).parents[2]
VALIDATOR_PATH = ROOT / "scripts/validate_support_manifest.py"
SPEC = importlib.util.spec_from_file_location("support_manifest_validator", VALIDATOR_PATH)
assert SPEC and SPEC.loader
VALIDATOR = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(VALIDATOR)


def _run(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, check=check, capture_output=True, text=True)


def _providers() -> list[tuple[str, Path]]:
    providers = []
    for manifest_path in sorted((ROOT / "packages").glob("*/pyproject.toml")):
        with manifest_path.open("rb") as handle:
            providers.append((tomllib.load(handle)["project"]["name"], manifest_path))
    return providers


def _wheel_for(wheelhouse: Path, name: str) -> Path:
    normalized = name.replace("-", "_")
    return next(wheelhouse.glob(f"{normalized}-*.whl"))


def _top_level_modules(wheel: Path) -> list[str]:
    with zipfile.ZipFile(wheel) as archive:
        return sorted(
            {
                path.parts[0]
                for filename in archive.namelist()
                if (path := Path(filename)).name == "__init__.py"
                and not path.parts[0].endswith(".dist-info")
            }
        )


def _build_older_core_fixture(directory: Path) -> None:
    (directory / "src" / "phlo").mkdir(parents=True)
    (directory / "src" / "phlo" / "__init__.py").touch()
    (directory / "pyproject.toml").write_text(
        """\
[build-system]
build-backend = "hatchling.build"
requires = ["hatchling"]

[project]
name = "phlo"
version = "0.12.0"
""",
        encoding="utf-8",
    )
    _run("uv", "build", "--wheel", "--out-dir", str(directory / "dist"), str(directory))


def test_every_provider_declares_the_dynamic_current_minor_epoch() -> None:
    assert _providers()
    assert VALIDATOR.provider_core_compatibility_errors(ROOT) == []


def test_provider_compatibility_validator_rejects_a_new_unbounded_provider(tmp_path: Path) -> None:
    project = tmp_path / "packages" / "phlo-new-provider"
    project.mkdir(parents=True)
    (tmp_path / "pyproject.toml").write_text(
        (ROOT / "pyproject.toml").read_text(encoding="utf-8"), encoding="utf-8"
    )
    (project / "pyproject.toml").write_text(
        "[project]\nname = 'phlo-new-provider'\nversion = '0.1.0'\ndependencies = ['phlo>=0.1.0']\n",
        encoding="utf-8",
    )

    errors = VALIDATOR.provider_core_compatibility_errors(tmp_path)

    assert errors == [
        "provider 'phlo-new-provider' must declare "
        f"{VALIDATOR.provider_core_requirement(ROOT)!r}; found ['phlo>=0.1.0']"
    ]


def test_built_provider_wheels_install_and_import_with_the_declared_core_minimum(
    tmp_path: Path,
) -> None:
    """Use built artifacts and resolved runtime dependencies in fresh environments."""
    wheelhouse = tmp_path / "wheelhouse"
    _run("uv", "build", "--all-packages", "--wheel", "--out-dir", str(wheelhouse))
    core_wheel = _wheel_for(wheelhouse, "phlo")

    for name, _manifest_path in _providers():
        provider_wheel = _wheel_for(wheelhouse, name)
        environment = tmp_path / name
        _run("uv", "venv", "--seed", "--python", sys.executable, str(environment))
        python = environment / "bin" / "python"
        _run(
            "uv",
            "pip",
            "install",
            "--python",
            str(python),
            "--find-links",
            str(wheelhouse),
            str(core_wheel),
            str(provider_wheel),
        )
        for module in _top_level_modules(provider_wheel):
            _run(str(python), "-c", f"import {module}")


def test_older_core_resolution_is_rejected_before_provider_import(tmp_path: Path) -> None:
    wheelhouse = tmp_path / "wheelhouse"
    _run("uv", "build", "--all-packages", "--wheel", "--out-dir", str(wheelhouse))
    fixture = tmp_path / "older-core"
    _build_older_core_fixture(fixture)
    provider_wheel = _wheel_for(wheelhouse, _providers()[0][0])

    result = _run(
        "uv",
        "pip",
        "install",
        "--dry-run",
        "--no-index",
        "--find-links",
        str(fixture / "dist"),
        "phlo==0.12.0",
        str(provider_wheel),
        check=False,
    )

    assert result.returncode != 0
    assert "phlo" in result.stderr.lower()
