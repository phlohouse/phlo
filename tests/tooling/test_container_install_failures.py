"""Execute container install blocks with uv and an offline, minimal wheelhouse."""

import os
import re
import shlex
import shutil
import subprocess
import venv
import zipfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]


def _install_block(package: str) -> str:
    dockerfile = REPO_ROOT / "packages" / package / "src" / package.replace("-", "_") / "Dockerfile"
    blocks = re.findall(r"^RUN (.*)$", dockerfile.read_text().replace("\\\n", " "), re.MULTILINE)
    return next(
        block
        for block in blocks
        if "PHLO_REQUIREMENT=" in block or "PHLO_DBT_REQUIREMENT=" in block
    )


def _wheel(wheelhouse: Path, name: str, version: str, requirements: tuple[str, ...] = ()) -> None:
    normalized = name.replace("-", "_")
    dist_info = f"{normalized}-{version}.dist-info"
    with zipfile.ZipFile(wheelhouse / f"{normalized}-{version}-py3-none-any.whl", "w") as wheel:
        wheel.writestr(f"{normalized}/__init__.py", "")
        wheel.writestr(f"{normalized}/__main__.py", "print('installed dependency')\n")
        metadata = f"Metadata-Version: 2.1\nName: {name}\nVersion: {version}\n"
        if name == "phlo":
            metadata += "Provides-Extra: defaults\n"
        metadata += "".join(f"Requires-Dist: {requirement}\n" for requirement in requirements)
        wheel.writestr(f"{dist_info}/METADATA", metadata)
        wheel.writestr(
            f"{dist_info}/WHEEL",
            "Wheel-Version: 1.0\nRoot-Is-Purelib: true\nTag: py3-none-any\n",
        )
        wheel.writestr(f"{dist_info}/RECORD", "")


def _install(
    tmp_path: Path,
    package: str,
    *,
    compatible: bool,
    cryptography_version: str = "48.0.1",
) -> subprocess.CompletedProcess[str]:
    uv = shutil.which("uv")
    assert uv, "container install regression tests require uv"
    environment = tmp_path / "environment"
    venv.EnvBuilder(symlinks=True).create(environment)
    python = environment / "bin" / "python"
    wheelhouse = tmp_path / "wheelhouse"
    wheelhouse.mkdir()
    _wheel(wheelhouse, "phlo", "0.18.0", ('phlo-api==0.18.0; extra == "defaults"',))
    _wheel(
        wheelhouse,
        "phlo-api",
        "0.18.0",
        ("phlo>=0.18,<0.19", "phlo-postgres<0.17", "uvicorn>=0.38"),
    )
    _wheel(
        wheelhouse,
        "phlo-postgres",
        "0.16.0",
        ("phlo>=0.18,<0.19" if compatible else "phlo>=0.17,<0.18",),
    )
    for name, version in (
        ("uvicorn", "0.38.0"),
        ("phlo-dbt", "0.18.0"),
        ("phlo-dagster", "0.18.0"),
        ("dagster-webserver", "1.13.0"),
        ("dagster-postgres", "0.29.0"),
        ("psycopg", "3.3.0"),
        ("sqlalchemy", "2.0.0"),
        ("PyJWT", "2.13.0"),
        ("cryptography", cryptography_version),
    ):
        _wheel(wheelhouse, name, version)
    # Only redirect image paths/interpreters and the Alpine cleanup command.
    # Keep the production shell conditionals and uv arguments intact.
    script = _install_block(package).replace("/opt/phlo-wheelhouse", shlex.quote(str(wheelhouse)))
    script = script.replace(
        "/tmp/phlo-url-requirements.txt", shlex.quote(str(tmp_path / "urls.txt"))
    )
    script = script.replace("--system", f"--python {shlex.quote(str(python))}")
    script = script.replace("python -c", f"{shlex.quote(str(python))} -c")
    script = script.replace("apk del .build-deps", "echo build-deps-cleaned")
    env = {
        **os.environ,
        "PATH": f"{Path(uv).parent}:{os.environ['PATH']}",
        "UV_OFFLINE": "true",
        "UV_NO_INDEX": "true",
        "UV_FIND_LINKS": str(wheelhouse),
        "UV_CACHE_DIR": str(tmp_path / "cache"),
        "PHLO_VERSION": "0.18.0",
        "PHLO_API_VERSION": "0.18.0",
        "PHLO_DBT_VERSION": "0.18.0",
        "PHLO_DAGSTER_VERSION": "0.18.0",
        "PHLO_WHEELHOUSE": "wheelhouse",
        "PHLO_UV_LOCKED": "",
    }
    shell = "/bin/sh" if package == "phlo-api" else "/bin/bash"
    return subprocess.run([shell, "-c", script], env=env, capture_output=True, text=True)


@pytest.mark.parametrize("package", ["phlo-api", "phlo-dagster"])
def test_container_install_rejects_unsatisfiable_wheel_dependencies(
    tmp_path: Path, package: str
) -> None:
    result = _install(tmp_path, package, compatible=False)
    assert "No solution found" in result.stderr
    assert result.returncode != 0, result.stdout + result.stderr


def test_dagster_install_rejects_unsatisfied_security_requirement(tmp_path: Path) -> None:
    result = _install(tmp_path, "phlo-dagster", compatible=True, cryptography_version="47.0.0")
    assert "No solution found" in result.stderr
    assert "cryptography>=48.0.1" in result.stderr
    assert result.returncode != 0, result.stdout + result.stderr


@pytest.mark.parametrize("package", ["phlo-api", "phlo-dagster"])
def test_container_install_includes_runtime_dependencies(tmp_path: Path, package: str) -> None:
    result = _install(tmp_path, package, compatible=True)
    assert result.returncode == 0, result.stdout + result.stderr
    python = tmp_path / "environment" / "bin" / "python"
    assert (
        subprocess.check_output([str(python), "-I", "-m", "uvicorn"], text=True).strip()
        == "installed dependency"
    )
    subprocess.run(["uv", "pip", "check", "--python", str(python)], check=True)
