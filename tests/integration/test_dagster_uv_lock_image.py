"""Lock-aware generated Dagster image builds honor the project's uv.lock.

Builds the generated Dagster image the way `phlo services init` produces it for
a uv-managed project: project lock metadata staged into the build context and
the PHLO_UV_LOCKED build arg set. Inside the container, a user workflow asset
importing a locked project dependency must be present in Definitions.

The failure-oriented paths are also covered: a missing staged lockfile and a
stale lockfile must fail the image build instead of silently resolving an
alternative dependency graph. Requires Docker and uv; skips otherwise.
"""

from __future__ import annotations

import shutil
import subprocess
import tomllib
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration

PHLO_DAGSTER_SRC = (
    Path(__file__).resolve().parents[2] / "packages" / "phlo-dagster" / "src" / "phlo_dagster"
)
IMAGE_TAG = "phlo-dagster-uv-lock-integration"

PROJECT_PYPROJECT = """\
[build-system]
requires = ["setuptools>=68"]
build-backend = "setuptools.build_meta"

[project]
name = "uvlock-demo"
version = "0.1.0"
description = "Lock-aware image build demo"
requires-python = ">=3.12"
dependencies = [
    "phlo-dagster>=0.14,<0.15",
    "packaging>=24.0",
]

[tool.setuptools.packages.find]
where = ["src"]
"""

WORKFLOW_MODULE = """\
from dagster import asset
from packaging.version import Version


@asset
def locked_dependency_probe():
    return {"packaging": Version("24.0").public}
"""

DISCOVERY_SCRIPT = """\\
from phlo_dagster.framework.discovery import discover_user_workflows

defs = discover_user_workflows("/app/workflows", clear_registries=True)
keys = sorted(key.to_user_string() for key in defs.resolve_all_asset_keys())
print("ASSET_KEYS:", keys)
assert "locked_dependency_probe" in keys, keys
print("PROBE OK")
"""


def _docker_available() -> bool:
    if shutil.which("docker") is None:
        return False
    return subprocess.run(["docker", "info"], capture_output=True).returncode == 0


def _require_docker_and_uv() -> None:
    if not _docker_available():
        pytest.skip("Docker daemon is unavailable for the lock-aware image build")
    if shutil.which("uv") is None:
        pytest.skip("uv is required to generate the project lockfile")


def _copy_runtime_files(context: Path) -> None:
    dagster_dir = context / "dagster"
    dagster_dir.mkdir(parents=True)
    for name in ("Dockerfile", "entrypoint.sh"):
        shutil.copy2(PHLO_DAGSTER_SRC / name, dagster_dir / name)
    for source, dest in (
        ("templates/workspace.yaml", "workspace.yaml"),
        ("templates/dagster.yaml", "dagster.yaml"),
    ):
        shutil.copy2(PHLO_DAGSTER_SRC / source, dagster_dir / dest)


@pytest.fixture(scope="module")
def locked_project(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """A uv-locked demo project with a workflow asset importing a locked dep."""
    _require_docker_and_uv()
    project = tmp_path_factory.mktemp("uvlock-demo-project")
    (project / "src" / "uvlock_demo").mkdir(parents=True)
    (project / "src" / "uvlock_demo" / "__init__.py").write_text("")
    (project / "workflows").mkdir()
    (project / "workflows" / "assets.py").write_text(WORKFLOW_MODULE)
    (project / "pyproject.toml").write_text(PROJECT_PYPROJECT)

    lock = subprocess.run(
        ["uv", "lock", "--project", str(project)], capture_output=True, text=True, timeout=600
    )
    assert lock.returncode == 0, lock.stderr

    # The container drops privileges to its `phlo` account before discovery;
    # pytest's private tmp directories (mode 0700) would deny traversal.
    project.chmod(0o755)
    for path in project.rglob("*"):
        if path.is_dir():
            path.chmod(0o755)
        else:
            path.chmod(0o644)
    return project


@pytest.fixture(scope="module")
def lock_aware_image(locked_project: Path, tmp_path_factory: pytest.TempPathFactory) -> str:
    """Build the generated Dagster image with staged lock metadata."""
    context = tmp_path_factory.mktemp("build-context")
    _copy_runtime_files(context)
    for name in ("pyproject.toml", "uv.lock"):
        shutil.copy2(locked_project / name, context / name)

    build = subprocess.run(
        [
            "docker",
            "build",
            "--tag",
            IMAGE_TAG,
            "--build-arg",
            "PHLO_UV_LOCKED=true",
            "--file",
            str(context / "dagster" / "Dockerfile"),
            str(context),
        ],
        capture_output=True,
        text=True,
        timeout=1800,
    )
    assert build.returncode == 0, build.stderr
    return IMAGE_TAG


@pytest.fixture(scope="module", autouse=True)
def cleanup_image(lock_aware_image: str):
    yield
    subprocess.run(
        ["docker", "image", "rm", "--force", lock_aware_image], capture_output=True, text=True
    )


def test_generated_image_installs_locked_deps_and_discovers_user_assets(
    locked_project: Path, lock_aware_image: str
) -> None:
    """The image's lock-built environment loads the user asset from /app."""
    run = subprocess.run(
        [
            "docker",
            "run",
            "--rm",
            "--volume",
            f"{locked_project}:/app",
            "--env",
            "DAGSTER_HOME=/tmp/dagster-home",
            lock_aware_image,
            "python",
            "-c",
            DISCOVERY_SCRIPT,
        ],
        capture_output=True,
        text=True,
        timeout=600,
    )
    assert run.returncode == 0, run.stderr
    assert "PROBE OK" in run.stdout
    assert "ASSET_KEYS: ['locked_dependency_probe']" in run.stdout


def test_locked_image_uses_the_lockfile_versions(
    locked_project: Path, lock_aware_image: str
) -> None:
    """Direct and transitive deps match `uv sync --locked` in the repository."""
    run = subprocess.run(
        [
            "docker",
            "run",
            "--rm",
            "--volume",
            f"{locked_project}:/app",
            "--env",
            "DAGSTER_HOME=/tmp/dagster-home",
            lock_aware_image,
            "python",
            "-c",
            "import dagster; print('DAGSTER_VERSION', dagster.__version__)",
        ],
        capture_output=True,
        text=True,
        timeout=600,
    )
    assert run.returncode == 0, run.stderr

    lock_packages = tomllib.loads((locked_project / "uv.lock").read_text())["package"]
    locked_dagster = next(pkg for pkg in lock_packages if pkg["name"] == "dagster")
    assert f"DAGSTER_VERSION {locked_dagster['version']}" in run.stdout


def test_missing_staged_lockfile_fails_the_build_clearly(
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    """PHLO_UV_LOCKED=true without staged lock metadata must fail, not fall back."""
    _require_docker_and_uv()
    context = tmp_path_factory.mktemp("build-context-nolock")
    _copy_runtime_files(context)

    build = subprocess.run(
        [
            "docker",
            "build",
            "--build-arg",
            "PHLO_UV_LOCKED=true",
            "--file",
            str(context / "dagster" / "Dockerfile"),
            str(context),
        ],
        capture_output=True,
        text=True,
        timeout=1800,
    )
    assert build.returncode != 0
    assert "run 'phlo services init' to stage project lock metadata" in build.stderr


def test_stale_lockfile_fails_the_build_clearly(
    locked_project: Path, tmp_path_factory: pytest.TempPathFactory
) -> None:
    """A lockfile out of sync with pyproject.toml must fail under `uv sync --locked`."""
    context = tmp_path_factory.mktemp("build-context-stale")
    _copy_runtime_files(context)
    for name in ("pyproject.toml", "uv.lock"):
        shutil.copy2(locked_project / name, context / name)
    pyproject = context / "pyproject.toml"
    pyproject.write_text(
        pyproject.read_text().replace(
            '    "packaging>=24.0",', '    "packaging>=24.0",\n    "attrs>=25.0",'
        )
    )

    build = subprocess.run(
        [
            "docker",
            "build",
            "--build-arg",
            "PHLO_UV_LOCKED=true",
            "--file",
            str(context / "dagster" / "Dockerfile"),
            str(context),
        ],
        capture_output=True,
        text=True,
        timeout=1800,
    )
    assert build.returncode != 0
    assert "lockfile" in build.stderr
