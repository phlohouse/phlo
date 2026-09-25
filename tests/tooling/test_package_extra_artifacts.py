"""Exercise optional provider imports from installed wheels without source paths."""

from __future__ import annotations

import os
import subprocess
import sys
import textwrap
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _run(*args: str, cwd: Path) -> None:
    env = {
        key: value
        for key, value in os.environ.items()
        if key not in {"PYTHONPATH", "PHLO_DEV_SOURCE"}
    }
    result = subprocess.run(args, cwd=cwd, env=env, capture_output=True, text=True, check=False)
    assert result.returncode == 0, f"{args}:\n{result.stdout}\n{result.stderr}"


def test_api_and_pandera_wheels_report_missing_extras(tmp_path: Path) -> None:
    wheels = tmp_path / "wheels"
    for package in ("phlo", "phlo-api", "phlo-pandera"):
        _run("uv", "build", "--wheel", "--package", package, "--out-dir", str(wheels), cwd=ROOT)

    for package, provider, extra, code in (
        (
            "phlo-api",
            "phlo-dagster",
            "dagster",
            "from phlo_api.observatory_api.dagster import _dagster_operations\n"
            "_dagster_operations()",
        ),
        (
            "phlo-pandera",
            "phlo-trino",
            "trino",
            "from types import SimpleNamespace\n"
            "from phlo_pandera.decorator_helpers import _resolve_trino_resource\n"
            "_resolve_trino_resource(SimpleNamespace(resources={}, get_resource=lambda name: None))",
        ),
    ):
        environment = tmp_path / package
        _run("uv", "venv", "--python", sys.executable, str(environment), cwd=tmp_path)
        python = environment / "bin/python"
        wheel = next(wheels.glob(f"{package.replace('-', '_')}-*.whl"))
        _run(
            "uv",
            "pip",
            "install",
            "--python",
            str(python),
            "--find-links",
            str(wheels),
            str(wheel),
            cwd=tmp_path,
        )
        script = (
            "import importlib.metadata as metadata\n"
            "import importlib.util\n"
            f"assert importlib.util.find_spec('{provider.replace('-', '_')}') is None\n"
            f"assert any('{provider}' in req and 'extra ==' in req "
            f"for req in metadata.requires('{package}'))\n"
            "try:\n"
            f"{textwrap.indent(code, '    ')}\n"
            "except ImportError as exc:\n"
            f"    assert '{package}[{extra}]' in str(exc), exc\n"
            "else:\n"
            "    raise AssertionError('missing-extra import succeeded')\n"
        )
        _run(
            str(python),
            "-c",
            script,
            cwd=tmp_path,
        )
