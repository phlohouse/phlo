"""Clean-environment wheel proof for the Retail Files project template.

Builds the `phlo-retail-files` wheel, installs it with its real dependencies
into a throwaway virtual environment, and proves the template is discovered
and rendered by the installed package's entry point — not from the repository.

Marked `integration`: it builds a wheel and resolves dependencies from PyPI.
"""

from __future__ import annotations

import subprocess
import venv
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
BLUEPRINT_DIR = REPO_ROOT / "examples" / "lakehouses" / "retail-files"

pytestmark = pytest.mark.integration


def _run(command: list[str], *, cwd: Path | None = None) -> str:
    result = subprocess.run(command, cwd=cwd, capture_output=True, text=True, check=False)
    assert result.returncode == 0, f"{command} failed:\n{result.stdout}\n{result.stderr}"
    return result.stdout


def _build_wheel(out_dir: Path) -> Path:
    _run(["uv", "build", "--wheel", "--out-dir", str(out_dir)], cwd=BLUEPRINT_DIR)
    wheels = list(out_dir.glob("phlo_retail_files-*.whl"))
    assert len(wheels) == 1, f"expected one wheel, got {wheels}"
    return wheels[0]


@pytest.fixture()
def clean_env(tmp_path: Path) -> tuple[Path, Path]:
    """A throwaway venv with the built blueprint wheel and its dependencies."""
    env_dir = tmp_path / "clean-env"
    venv.create(env_dir, with_pip=False)
    pip_python = env_dir / "bin" / "python"

    dist_dir = tmp_path / "dist"
    wheel = _build_wheel(dist_dir)
    _run(["uv", "pip", "install", "--python", str(pip_python), str(wheel)])
    return env_dir, tmp_path / "generated"


def test_installed_wheel_discovers_and_renders_template(clean_env: tuple[Path, Path]) -> None:
    env_dir, generated_dir = clean_env
    phlo_bin = env_dir / "bin" / "phlo"

    listing = _run([str(phlo_bin), "init", "--list-templates"])
    assert "retail-files" in listing

    result = _run([str(phlo_bin), "init", str(generated_dir), "--template", "retail-files"])
    assert "Successfully initialized" in result

    assert (generated_dir / "phlo.yaml").exists()
    assert (generated_dir / "workflows" / "ingestion" / "retail" / "files.py").exists()
    assert (generated_dir / "scripts" / "generate_fixtures.py").exists()

    rendered = (generated_dir / "pyproject.toml").read_text(encoding="utf-8")
    assert 'name = "phlo-retail-files"' not in rendered  # rendered, not the package metadata
    assert "phlo[defaults]==0.14.0" in rendered
    assert "git+" not in rendered and "file://" not in rendered
