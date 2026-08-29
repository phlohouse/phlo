"""Tests for MinIO CLI commands.

Exercises the mc-backed ls/admin/shell commands against a stubbed container
backend, covering target quoting, command timeouts, raw mc stderr hiding,
rejection of a partially initialized .phlo directory, and regulated-mode
mutation authorization for shell passthrough.
"""

from __future__ import annotations

from pathlib import Path
from subprocess import CompletedProcess, TimeoutExpired
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from click.testing import CliRunner

from phlo.cli.infrastructure.command import CommandError
from phlo_minio.cli import minio_group
from phlo_minio.cli_plugin import MinioCliPlugin


@pytest.fixture(autouse=True)
def _skip_backend_availability(monkeypatch) -> None:
    monkeypatch.setattr("phlo_minio.cli._require_container_backend", lambda: None)


def test_minio_cli_plugin_metadata() -> None:
    plugin = MinioCliPlugin()

    assert plugin.metadata.name == "minio"
    assert plugin.get_cli_commands()[0].name == "minio"


def test_minio_ls_runs_mc(monkeypatch) -> None:
    def _run_command(cmd, **_kwargs):
        if cmd[:2] == ["docker", "info"]:
            return CompletedProcess(cmd, 0, stdout="", stderr="")
        assert cmd[-4:-1] == ["minio", "/bin/sh", "-c"]
        assert (
            cmd[-1] == 'mc alias set local http://localhost:9000 "$MINIO_ROOT_USER" '
            '"$MINIO_ROOT_PASSWORD" >/dev/null && mc ls local/warehouse/'
        )
        return CompletedProcess(cmd, 0, stdout="bucket\n", stderr="")

    monkeypatch.setattr("phlo_minio.cli.ensure_phlo_dir", lambda: Path("/tmp/project/.phlo"))
    monkeypatch.setattr("phlo_minio.cli.get_project_name", lambda: "demo")
    monkeypatch.setattr(
        "phlo_minio.cli.compose_base_cmd",
        lambda **_kwargs: ["docker", "compose", "-p", "demo"],
    )
    monkeypatch.setattr("phlo_minio.cli.run_command", _run_command)

    result = CliRunner().invoke(minio_group, ["ls", "local/warehouse/"])

    assert result.exit_code == 0
    assert result.output == "bucket\n"


def test_minio_admin_info_runs_mc(monkeypatch) -> None:
    def _run_command(cmd, **_kwargs):
        if cmd[:2] == ["docker", "info"]:
            return CompletedProcess(cmd, 0, stdout="", stderr="")
        assert cmd[-4:-1] == ["minio", "/bin/sh", "-c"]
        assert (
            cmd[-1] == 'mc alias set local http://localhost:9000 "$MINIO_ROOT_USER" '
            '"$MINIO_ROOT_PASSWORD" >/dev/null && mc admin info --json local/'
        )
        return CompletedProcess(cmd, 0, stdout='{"status":"ok"}\n', stderr="")

    monkeypatch.setattr("phlo_minio.cli.ensure_phlo_dir", lambda: Path("/tmp/project/.phlo"))
    monkeypatch.setattr("phlo_minio.cli.get_project_name", lambda: "demo")
    monkeypatch.setattr(
        "phlo_minio.cli.compose_base_cmd",
        lambda **_kwargs: ["docker", "compose", "-p", "demo"],
    )
    monkeypatch.setattr("phlo_minio.cli.run_command", _run_command)

    result = CliRunner().invoke(minio_group, ["admin", "info", "--json", "local/"])

    assert result.exit_code == 0
    assert result.output == '{"status":"ok"}\n'


def test_minio_ls_rejects_partial_phlo_directory(monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".phlo" / "logs").mkdir(parents=True)

    result = CliRunner().invoke(minio_group, ["ls"])

    assert result.exit_code != 0
    assert "Phlo services have not been initialized" in result.output
    assert "Missing: .phlo/docker-compose.yml" in result.output
    assert "Run: phlo services init" in result.output


def test_minio_admin_info_rejects_partial_phlo_directory(monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".phlo" / "logs").mkdir(parents=True)

    result = CliRunner().invoke(minio_group, ["admin", "info"])

    assert result.exit_code != 0
    assert "Phlo services have not been initialized" in result.output
    assert "Missing: .phlo/docker-compose.yml" in result.output
    assert "Run: phlo services init" in result.output


def test_minio_shell_passthrough(monkeypatch) -> None:
    captured: list[list[str]] = []

    monkeypatch.setattr("phlo_minio.cli.ensure_phlo_dir", lambda: Path("/tmp/project/.phlo"))
    monkeypatch.setattr("phlo_minio.cli.get_project_name", lambda: "demo")
    monkeypatch.setattr(
        "phlo_minio.cli.compose_base_cmd",
        lambda **_kwargs: ["docker", "compose", "-p", "demo"],
    )
    monkeypatch.setattr(
        "phlo_minio.cli.run_command",
        lambda cmd, **_kwargs: CompletedProcess(cmd, 0, stdout="", stderr=""),
    )

    def _subprocess_run(cmd, check):
        captured.append(cmd)
        return CompletedProcess(cmd, 0, stdout=None, stderr=None)

    monkeypatch.setattr("phlo_minio.cli.subprocess.run", _subprocess_run)

    result = CliRunner().invoke(minio_group, ["cp", "local/a.txt", "local/bucket/a.txt"])

    assert result.exit_code == 0
    assert captured == [
        [
            "docker",
            "compose",
            "-p",
            "demo",
            "exec",
            "minio",
            "/bin/sh",
            "-c",
            'mc alias set local http://localhost:9000 "$MINIO_ROOT_USER" '
            '"$MINIO_ROOT_PASSWORD" >/dev/null && mc cp local/a.txt local/bucket/a.txt',
        ]
    ]


def test_minio_shell_passthrough_quotes_targets_with_spaces(monkeypatch) -> None:
    captured: list[list[str]] = []

    monkeypatch.setattr("phlo_minio.cli.ensure_phlo_dir", lambda: Path("/tmp/project/.phlo"))
    monkeypatch.setattr("phlo_minio.cli.get_project_name", lambda: "demo")
    monkeypatch.setattr(
        "phlo_minio.cli.compose_base_cmd",
        lambda **_kwargs: ["docker", "compose", "-p", "demo"],
    )
    monkeypatch.setattr(
        "phlo_minio.cli.run_command",
        lambda cmd, **_kwargs: CompletedProcess(cmd, 0, stdout="", stderr=""),
    )

    def _subprocess_run(cmd, check):
        captured.append(cmd)
        return CompletedProcess(cmd, 0, stdout=None, stderr=None)

    monkeypatch.setattr("phlo_minio.cli.subprocess.run", _subprocess_run)

    result = CliRunner().invoke(minio_group, ["cp", "warehouse with space/", "local/bucket/"])

    assert result.exit_code == 0
    shell_payload = captured[0][-1]
    assert "mc alias set local http://localhost:9000" in shell_payload
    assert "mc cp 'warehouse with space/' local/bucket/" in shell_payload


def test_minio_shell_passthrough_enforces_regulated_authorization(monkeypatch) -> None:
    adapter = MagicMock()
    adapter.enforce_mutation.return_value = SimpleNamespace(
        allowed=False,
        reason_code="forbidden",
        explanation="no",
    )

    monkeypatch.setattr(
        "phlo.cli.authorization_wrappers.check_cli_surface_active",
        lambda: True,
    )
    monkeypatch.setattr("phlo_minio.cli.get_minio_cli_adapter", lambda: adapter)
    monkeypatch.setattr("phlo_minio.cli.subprocess.run", MagicMock())

    result = CliRunner().invoke(minio_group, ["cp", "local/a.txt", "local/bucket/a.txt"])

    assert result.exit_code == 1
    adapter.enforce_mutation.assert_called_once_with("minio", None)
    assert result.output == "Error: Authorization denied for 'minio': no\n"


def test_minio_ls_timeout(monkeypatch) -> None:
    def _run_command(cmd, **_kwargs):
        if cmd[:2] == ["docker", "info"]:
            return CompletedProcess(cmd, 0, stdout="", stderr="")
        raise TimeoutExpired(cmd=cmd, timeout=30)

    monkeypatch.setattr("phlo_minio.cli.ensure_phlo_dir", lambda: Path("/tmp/project/.phlo"))
    monkeypatch.setattr("phlo_minio.cli.get_project_name", lambda: "demo")
    monkeypatch.setattr(
        "phlo_minio.cli.compose_base_cmd",
        lambda **_kwargs: ["docker", "compose", "-p", "demo"],
    )
    monkeypatch.setattr("phlo_minio.cli.run_command", _run_command)

    result = CliRunner().invoke(minio_group, ["ls", "local/warehouse/"])

    assert result.exit_code != 0
    assert "List timed out after 30 seconds." in result.output


def test_minio_ls_hides_raw_mc_stderr(monkeypatch) -> None:
    def _run_command(cmd, **_kwargs):
        raise CommandError(cmd=cmd, returncode=1, stdout="", stderr="secret endpoint failed")

    monkeypatch.setattr("phlo_minio.cli.ensure_phlo_dir", lambda: Path("/tmp/project/.phlo"))
    monkeypatch.setattr("phlo_minio.cli.get_project_name", lambda: "demo")
    monkeypatch.setattr(
        "phlo_minio.cli.compose_base_cmd",
        lambda **_kwargs: ["docker", "compose", "-p", "demo"],
    )
    monkeypatch.setattr("phlo_minio.cli.run_command", _run_command)

    result = CliRunner().invoke(minio_group, ["ls", "local/warehouse/"])

    assert result.exit_code != 0
    assert "secret endpoint failed" not in result.output
    assert "Error: MinIO list failed" in result.output
    assert "Target: local/warehouse/" in result.output
    assert "Run: phlo services status" in result.output
