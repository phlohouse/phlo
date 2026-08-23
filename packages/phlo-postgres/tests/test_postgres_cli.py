"""Tests for PostgreSQL CLI commands.

Commands shell out through the compose project, so every test stubs the
container-backend availability check and fakes subprocess results instead of
talking to a real backend.
"""

from __future__ import annotations

import gzip
from pathlib import Path
from subprocess import CompletedProcess, TimeoutExpired

import pytest
from click.testing import CliRunner

from phlo_postgres.cli import postgres_group
from phlo_postgres.cli_plugin import PostgresCliPlugin


@pytest.fixture(autouse=True)
def _skip_backend_availability(monkeypatch) -> None:
    monkeypatch.setattr("phlo_postgres.cli._require_container_backend", lambda: None)


def test_postgres_cli_plugin_metadata() -> None:
    plugin = PostgresCliPlugin()

    assert plugin.metadata.name == "postgres"
    assert plugin.get_cli_commands()[0].name == "postgres"


def test_postgres_query_runs_psql(monkeypatch) -> None:
    def _run_command(cmd, **_kwargs):
        if cmd[:2] == ["docker", "info"]:
            return CompletedProcess(cmd, 0, stdout="", stderr="")
        assert cmd[-9:] == [
            "psql",
            "-U",
            "phlo",
            "-d",
            "phlo",
            "-v",
            "ON_ERROR_STOP=1",
            "-c",
            "SELECT 1",
        ]
        return CompletedProcess(cmd, 0, stdout="1\n", stderr="")

    monkeypatch.setattr(
        "phlo_postgres.cli.ensure_compose_project", lambda: Path("/tmp/project/.phlo")
    )
    monkeypatch.setattr("phlo_postgres.cli.get_project_name", lambda: "demo")
    monkeypatch.setattr(
        "phlo_postgres.cli.compose_base_cmd",
        lambda **_kwargs: ["docker", "compose", "-p", "demo"],
    )
    monkeypatch.setattr("phlo_postgres.cli.run_command", _run_command)

    result = CliRunner().invoke(postgres_group, ["query", "SELECT 1"])

    assert result.exit_code == 0
    assert result.output == "1\n"


def test_postgres_query_requires_initialized_services(monkeypatch, tmp_path) -> None:
    phlo_dir = tmp_path / ".phlo"
    phlo_dir.mkdir()
    monkeypatch.chdir(tmp_path)

    result = CliRunner().invoke(postgres_group, ["query", "SELECT 1"])

    assert result.exit_code != 0
    assert "Error: Phlo services have not been initialized" in result.output
    assert "Missing: .phlo/docker-compose.yml" in result.output
    assert "Run: phlo services init" in result.output
    assert "couldn't find env file" not in result.output


def test_postgres_dump_writes_gzip_file(monkeypatch, tmp_path) -> None:
    output_file = tmp_path / "backup.sql.gz"

    def _run_command(cmd, **_kwargs):
        if cmd[:2] == ["docker", "info"]:
            return CompletedProcess(cmd, 0, stdout="", stderr="")
        assert cmd[-4:] == ["pg_dump", "-U", "phlo", "phlo"]
        return CompletedProcess(cmd, 0, stdout="CREATE TABLE test ();", stderr="")

    monkeypatch.setattr(
        "phlo_postgres.cli.ensure_compose_project", lambda: Path("/tmp/project/.phlo")
    )
    monkeypatch.setattr("phlo_postgres.cli.get_project_name", lambda: "demo")
    monkeypatch.setattr(
        "phlo_postgres.cli.compose_base_cmd",
        lambda **_kwargs: ["docker", "compose", "-p", "demo"],
    )
    monkeypatch.setattr("phlo_postgres.cli.run_command", _run_command)

    result = CliRunner().invoke(postgres_group, ["dump", "--file", str(output_file)])

    assert result.exit_code == 0
    assert output_file.exists()
    with gzip.open(output_file, "rt", encoding="utf-8") as handle:
        assert handle.read() == "CREATE TABLE test ();"


def test_postgres_restore_reads_file(monkeypatch, tmp_path) -> None:
    input_file = tmp_path / "backup.sql"
    input_file.write_text("SELECT 1;", encoding="utf-8")
    captured: list[tuple[list[str], str]] = []

    monkeypatch.setattr(
        "phlo_postgres.cli.ensure_compose_project", lambda: Path("/tmp/project/.phlo")
    )
    monkeypatch.setattr("phlo_postgres.cli.get_project_name", lambda: "demo")
    monkeypatch.setattr(
        "phlo_postgres.cli.compose_base_cmd",
        lambda **_kwargs: ["docker", "compose", "-p", "demo"],
    )
    monkeypatch.setattr(
        "phlo_postgres.cli.run_command",
        lambda cmd, **_kwargs: CompletedProcess(cmd, 0, stdout="", stderr=""),
    )

    def _subprocess_run(cmd, input, text, capture_output, timeout, check):
        captured.append((cmd, input))
        return CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr("phlo_postgres.cli.subprocess.run", _subprocess_run)

    result = CliRunner().invoke(postgres_group, ["restore", "--file", str(input_file)])

    assert result.exit_code == 0
    assert captured == [
        (
            [
                "docker",
                "compose",
                "-p",
                "demo",
                "exec",
                "-T",
                "postgres",
                "psql",
                "-U",
                "phlo",
                "-d",
                "phlo",
                "-v",
                "ON_ERROR_STOP=1",
            ],
            "SELECT 1;",
        )
    ]


def test_postgres_vacuum_runs_vacuumdb(monkeypatch) -> None:
    def _run_command(cmd, **_kwargs):
        if cmd[:2] == ["docker", "info"]:
            return CompletedProcess(cmd, 0, stdout="", stderr="")
        assert cmd[-5:] == ["vacuumdb", "-U", "phlo", "-z", "phlo"]
        return CompletedProcess(cmd, 0, stdout="VACUUM\n", stderr="")

    monkeypatch.setattr(
        "phlo_postgres.cli.ensure_compose_project", lambda: Path("/tmp/project/.phlo")
    )
    monkeypatch.setattr("phlo_postgres.cli.get_project_name", lambda: "demo")
    monkeypatch.setattr(
        "phlo_postgres.cli.compose_base_cmd",
        lambda **_kwargs: ["docker", "compose", "-p", "demo"],
    )
    monkeypatch.setattr("phlo_postgres.cli.run_command", _run_command)

    result = CliRunner().invoke(postgres_group, ["vacuum"])

    assert result.exit_code == 0
    assert result.output == "VACUUM\n"


def test_postgres_shell_passthrough(monkeypatch) -> None:
    captured: list[list[str]] = []

    monkeypatch.setattr(
        "phlo_postgres.cli.ensure_compose_project", lambda: Path("/tmp/project/.phlo")
    )
    monkeypatch.setattr("phlo_postgres.cli.get_project_name", lambda: "demo")
    monkeypatch.setattr(
        "phlo_postgres.cli.compose_base_cmd",
        lambda **_kwargs: ["docker", "compose", "-p", "demo"],
    )
    monkeypatch.setattr(
        "phlo_postgres.cli.run_command",
        lambda cmd, **_kwargs: CompletedProcess(cmd, 0, stdout="", stderr=""),
    )

    def _subprocess_run(cmd, check):
        captured.append(cmd)
        return CompletedProcess(cmd, 0, stdout=None, stderr=None)

    monkeypatch.setattr("phlo_postgres.cli.subprocess.run", _subprocess_run)

    result = CliRunner().invoke(postgres_group, ["--dbname=analytics"])

    assert result.exit_code == 0
    assert captured == [
        [
            "docker",
            "compose",
            "-p",
            "demo",
            "exec",
            "postgres",
            "psql",
            "-U",
            "phlo",
            "-d",
            "phlo",
            "--dbname=analytics",
        ]
    ]


def test_postgres_query_timeout(monkeypatch) -> None:
    def _run_command(cmd, **_kwargs):
        if cmd[:2] == ["docker", "info"]:
            return CompletedProcess(cmd, 0, stdout="", stderr="")
        raise TimeoutExpired(cmd=cmd, timeout=30)

    monkeypatch.setattr(
        "phlo_postgres.cli.ensure_compose_project", lambda: Path("/tmp/project/.phlo")
    )
    monkeypatch.setattr("phlo_postgres.cli.get_project_name", lambda: "demo")
    monkeypatch.setattr(
        "phlo_postgres.cli.compose_base_cmd",
        lambda **_kwargs: ["docker", "compose", "-p", "demo"],
    )
    monkeypatch.setattr("phlo_postgres.cli.run_command", _run_command)

    result = CliRunner().invoke(postgres_group, ["query", "SELECT 1"])

    assert result.exit_code != 0
    assert "Query timed out after 30 seconds." in result.output
