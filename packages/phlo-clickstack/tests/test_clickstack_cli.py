"""Tests for ClickStack CLI commands.

Drives the query command through CliRunner with mocked subprocess calls to
verify SQL executes inside the ClickStack container via the project's
configured compose backend, that mutating statements pass through surface
mutation authorization, and that missing input or timeouts surface as CLI
errors.
"""

from __future__ import annotations

from pathlib import Path
from subprocess import CompletedProcess, TimeoutExpired

from click.testing import CliRunner

from phlo_clickstack.cli import clickstack_group
from phlo_clickstack.cli_plugin import ClickStackCliPlugin


def test_clickstack_cli_plugin_metadata() -> None:
    """Validate ClickStack CLI plugin metadata."""
    plugin = ClickStackCliPlugin()

    assert plugin.metadata.name == "clickstack"
    assert plugin.get_cli_commands()[0].name == "clickstack"


def test_clickstack_query_runs_clickhouse_client(monkeypatch) -> None:
    """Query command should execute clickhouse-client in the ClickStack container."""

    def _run_command(cmd, **_kwargs):
        if cmd[:2] == ["docker", "info"]:
            return CompletedProcess(cmd, 0, stdout="", stderr="")
        assert cmd[-7:] == [
            "clickstack",
            "clickhouse-client",
            "--multiquery",
            "--format",
            "JSONEachRow",
            "--query",
            "SELECT 1",
        ]
        return CompletedProcess(cmd, 0, stdout='{"1":1}\n', stderr="")

    monkeypatch.setattr("phlo_clickstack.cli._ensure_phlo_dir", lambda: Path("/tmp/project/.phlo"))
    monkeypatch.setattr("phlo_clickstack.cli._require_container_backend", lambda: None)
    monkeypatch.setattr("phlo_clickstack.cli.get_project_name", lambda: "demo")
    monkeypatch.setattr(
        "phlo_clickstack.cli.compose_base_cmd",
        lambda **_kwargs: ["docker", "compose", "-p", "demo"],
    )
    monkeypatch.setattr("phlo_clickstack.cli.run_command", _run_command)

    result = CliRunner().invoke(clickstack_group, ["query", "--format", "JSONEachRow", "SELECT 1"])

    assert result.exit_code == 0
    assert result.output == '{"1":1}\n'


def test_clickstack_query_authorizes_only_mutating_sql(monkeypatch) -> None:
    calls: list[str] = []

    monkeypatch.setattr("phlo_clickstack.cli._ensure_phlo_dir", lambda: Path("/tmp/project/.phlo"))
    monkeypatch.setattr(
        "phlo_clickstack.cli._require_container_backend",
        lambda: calls.append("backend"),
    )
    monkeypatch.setattr("phlo_clickstack.cli.get_project_name", lambda: "demo")
    monkeypatch.setattr(
        "phlo_clickstack.cli.compose_base_cmd",
        lambda **_kwargs: ["docker", "compose", "-p", "demo"],
    )
    monkeypatch.setattr(
        "phlo_clickstack.cli.run_command",
        lambda cmd, **_kwargs: CompletedProcess(cmd, 0, stdout="ok\n", stderr=""),
    )
    monkeypatch.setattr(
        "phlo_clickstack.cli.enforce_surface_mutation_authorization",
        lambda *_args, **_kwargs: calls.append("auth"),
    )

    select_result = CliRunner().invoke(clickstack_group, ["query", "SELECT 1"])
    insert_result = CliRunner().invoke(clickstack_group, ["query", "INSERT INTO t VALUES (1)"])

    assert select_result.exit_code == 0
    assert insert_result.exit_code == 0
    assert calls == ["backend", "auth", "backend"]


def test_clickstack_query_uses_selected_container_backend(monkeypatch, tmp_path) -> None:
    phlo_dir = tmp_path / ".phlo"
    phlo_dir.mkdir()
    (phlo_dir / "docker-compose.yml").write_text("services:\n  clickstack: {}\n")
    (phlo_dir / ".env").write_text("")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("PHLO_CONTAINER_BACKEND", "podman")
    monkeypatch.setattr("phlo_clickstack.cli.get_project_name", lambda: "demo")

    calls: list[list[str]] = []
    monkeypatch.setattr(
        "phlo.cli.commands.services.utils.require_container_backend",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        "phlo_clickstack.cli.run_command",
        lambda cmd, **_kwargs: calls.append(cmd) or CompletedProcess(cmd, 0, stdout="", stderr=""),
    )

    result = CliRunner().invoke(clickstack_group, ["query", "SELECT 1"])

    assert result.exit_code == 0, result.output
    assert calls
    assert calls[0][:2] == ["podman", "compose"]


def test_clickstack_query_uses_project_container_backend_config(monkeypatch, tmp_path) -> None:
    phlo_dir = tmp_path / ".phlo"
    phlo_dir.mkdir()
    (phlo_dir / "docker-compose.yml").write_text("services:\n  clickstack: {}\n")
    (phlo_dir / ".env").write_text("")
    (tmp_path / "phlo.yaml").write_text(
        "name: demo\ninfrastructure:\n  container_backend: podman\n"
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("PHLO_CONTAINER_BACKEND", raising=False)
    monkeypatch.setattr("phlo_clickstack.cli.get_project_name", lambda: "demo")
    monkeypatch.setattr(
        "phlo.cli.commands.services.utils.require_container_backend",
        lambda *_args, **_kwargs: None,
    )

    calls: list[list[str]] = []
    monkeypatch.setattr(
        "phlo_clickstack.cli.run_command",
        lambda cmd, **_kwargs: calls.append(cmd) or CompletedProcess(cmd, 0, stdout="", stderr=""),
    )

    result = CliRunner().invoke(clickstack_group, ["query", "SELECT 1"])

    assert result.exit_code == 0, result.output
    assert calls[0][:2] == ["podman", "compose"]


def test_clickstack_query_supports_file(monkeypatch, tmp_path) -> None:
    """Query command should read SQL from a file."""
    sql_file = tmp_path / "query.sql"
    sql_file.write_text("SELECT 42", encoding="utf-8")

    def _run_command(cmd, **_kwargs):
        if cmd[:2] == ["docker", "info"]:
            return CompletedProcess(cmd, 0, stdout="", stderr="")
        assert cmd[-1] == "SELECT 42"
        return CompletedProcess(cmd, 0, stdout="42\n", stderr="")

    monkeypatch.setattr("phlo_clickstack.cli._ensure_phlo_dir", lambda: Path("/tmp/project/.phlo"))
    monkeypatch.setattr("phlo_clickstack.cli._require_container_backend", lambda: None)
    monkeypatch.setattr("phlo_clickstack.cli.get_project_name", lambda: "demo")
    monkeypatch.setattr(
        "phlo_clickstack.cli.compose_base_cmd",
        lambda **_kwargs: ["docker", "compose", "-p", "demo"],
    )
    monkeypatch.setattr("phlo_clickstack.cli.run_command", _run_command)

    result = CliRunner().invoke(clickstack_group, ["query", "--file", str(sql_file)])

    assert result.exit_code == 0
    assert result.output == "42\n"


def test_clickstack_query_rejects_missing_input(monkeypatch) -> None:
    """Query command should fail clearly when no SQL is provided."""
    monkeypatch.setattr("phlo_clickstack.cli._ensure_phlo_dir", lambda: Path("/tmp/project/.phlo"))
    monkeypatch.setattr("phlo_clickstack.cli._require_container_backend", lambda: None)
    monkeypatch.setattr("phlo_clickstack.cli.get_project_name", lambda: "demo")
    monkeypatch.setattr(
        "phlo_clickstack.cli.run_command",
        lambda cmd, **_kwargs: (
            CompletedProcess(cmd, 0, stdout="", stderr="")
            if cmd[:2] == ["docker", "info"]
            else CompletedProcess(cmd, 0, stdout="", stderr="")
        ),
    )

    result = CliRunner().invoke(clickstack_group, ["query"])

    assert result.exit_code != 0
    assert "Error: no SQL query provided" in result.output
    assert "Provide an inline query argument or pass --file." in result.output
    assert 'Run: phlo clickstack query "SELECT 1"' in result.output


def test_clickstack_query_surfaces_timeout(monkeypatch) -> None:
    """Query command should surface timeouts as click exceptions."""

    def _run_command(cmd, **_kwargs):
        if cmd[:2] == ["docker", "info"]:
            return CompletedProcess(cmd, 0, stdout="", stderr="")
        raise TimeoutExpired(cmd=cmd, timeout=30)

    monkeypatch.setattr("phlo_clickstack.cli._ensure_phlo_dir", lambda: Path("/tmp/project/.phlo"))
    monkeypatch.setattr("phlo_clickstack.cli._require_container_backend", lambda: None)
    monkeypatch.setattr("phlo_clickstack.cli.get_project_name", lambda: "demo")
    monkeypatch.setattr(
        "phlo_clickstack.cli.compose_base_cmd",
        lambda **_kwargs: ["docker", "compose", "-p", "demo"],
    )
    monkeypatch.setattr("phlo_clickstack.cli.run_command", _run_command)

    result = CliRunner().invoke(clickstack_group, ["query", "SELECT 1"])

    assert result.exit_code != 0
    assert "Query timed out after 30 seconds." in result.output
