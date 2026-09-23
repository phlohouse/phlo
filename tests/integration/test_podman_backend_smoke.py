"""Podman backend smoke test; requires a local Podman installation.

Initializes a minimal project, forces the podman backend, and asserts the CLI
never falls back to docker. Skips when Podman is unavailable.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
from click.testing import CliRunner

from phlo.cli.main import cli

pytestmark = pytest.mark.integration


def test_podman_backend_smoke(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    if shutil.which("podman") is None:
        pytest.skip("podman not installed")

    project_dir = tmp_path / "podman-demo"
    runner = CliRunner()

    init_result = runner.invoke(cli, ["init", str(project_dir), "--template", "minimal"])
    assert init_result.exit_code == 0, init_result.output

    monkeypatch.chdir(project_dir)
    monkeypatch.setenv("PHLO_CONTAINER_BACKEND", "podman")

    services_result = runner.invoke(cli, ["services", "ports", "--json"])
    assert services_result.exit_code == 0, services_result.output
    assert "docker" not in services_result.output.lower()
    assert isinstance(json.loads(services_result.output), list)
