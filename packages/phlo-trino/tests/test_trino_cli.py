"""Tests for Trino CLI commands.

Covers plugin metadata and query dispatch through the trino executable:
mutating-SQL authorization, backend selection, file input, missing-input and
uninitialized-service rejection, timeout surfacing, and shell grouping.
"""

from __future__ import annotations

from types import SimpleNamespace
from subprocess import CompletedProcess, TimeoutExpired

from click.testing import CliRunner

from phlo_trino.cli import trino_group, trino_query
from phlo_trino.cli_plugin import TrinoCliPlugin


def test_trino_cli_plugin_metadata() -> None:
    """Validate Trino CLI plugin metadata."""
    plugin = TrinoCliPlugin()

    assert plugin.metadata.name == "trino"
    assert plugin.get_cli_commands()[0].name == "trino"


def test_trino_query_runs_trino_cli(monkeypatch) -> None:
    """Query command should execute the Trino CLI in the Trino container."""

    def _run_command(cmd, **_kwargs):
        if cmd[:2] == ["docker", "info"]:
            return CompletedProcess(cmd, 0, stdout="", stderr="")
        assert cmd[-8:] == [
            "trino",
            "trino",
            "--catalog",
            "iceberg",
            "--output-format",
            "JSON",
            "--execute",
            "SELECT 1",
        ]
        return CompletedProcess(cmd, 0, stdout='[{"_col0":1}]\n', stderr="")

    monkeypatch.setitem(
        trino_query.callback.__globals__, "_require_container_backend", lambda: None
    )
    monkeypatch.setitem(
        trino_query.callback.__globals__,
        "_trino_exec_base",
        lambda *, tty: [
            "docker",
            "compose",
            "-p",
            "demo",
            "exec",
            *([] if tty else ["-T"]),
            "trino",
            "trino",
        ],
    )
    monkeypatch.setitem(trino_query.callback.__globals__, "run_command", _run_command)

    result = CliRunner().invoke(
        trino_group,
        ["query", "--catalog", "iceberg", "--output-format", "JSON", "SELECT 1"],
    )

    assert result.exit_code == 0
    assert result.output == '[{"_col0":1}]\n'


def test_trino_query_authorizes_only_mutating_sql(monkeypatch) -> None:
    calls: list[str] = []

    monkeypatch.setitem(
        trino_query.callback.__globals__,
        "_require_container_backend",
        lambda: calls.append("backend"),
    )
    monkeypatch.setitem(
        trino_query.callback.__globals__,
        "_trino_exec_base",
        lambda *, tty: [
            "docker",
            "compose",
            "-p",
            "demo",
            "exec",
            *([] if tty else ["-T"]),
            "trino",
            "trino",
        ],
    )
    monkeypatch.setitem(
        trino_query.callback.__globals__,
        "run_command",
        lambda cmd, **_kwargs: CompletedProcess(cmd, 0, stdout="ok\n", stderr=""),
    )
    monkeypatch.setitem(
        trino_query.callback.__globals__,
        "enforce_surface_mutation_authorization",
        lambda *_args, **_kwargs: calls.append("auth"),
    )

    select_result = CliRunner().invoke(trino_group, ["query", "SELECT 1"])
    insert_result = CliRunner().invoke(trino_group, ["query", "INSERT INTO t VALUES (1)"])

    assert select_result.exit_code == 0
    assert insert_result.exit_code == 0
    assert calls == ["backend", "auth", "backend"]


def test_trino_query_uses_selected_backend(monkeypatch, tmp_path) -> None:
    phlo_dir = tmp_path / ".phlo"
    phlo_dir.mkdir()
    (phlo_dir / "docker-compose.yml").write_text("services:\n  trino: {}\n")
    (phlo_dir / ".env").write_text("")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("PHLO_CONTAINER_BACKEND", "podman")

    calls: list[list[str]] = []
    monkeypatch.setitem(
        trino_query.callback.__globals__,
        "_require_container_backend",
        lambda: None,
    )
    monkeypatch.setitem(
        trino_query.callback.__globals__,
        "run_command",
        lambda cmd, **_kwargs: calls.append(cmd) or CompletedProcess(cmd, 0, stdout="", stderr=""),
    )

    result = CliRunner().invoke(trino_group, ["query", "SELECT 1"])

    assert result.exit_code == 0, result.output
    assert calls[0][:2] == ["podman", "compose"]


def test_trino_query_supports_file(monkeypatch, tmp_path) -> None:
    """Query command should read SQL from a file."""
    sql_file = tmp_path / "query.sql"
    sql_file.write_text("SELECT 42", encoding="utf-8")

    def _run_command(cmd, **_kwargs):
        assert cmd[-1] == "SELECT 42"
        return CompletedProcess(cmd, 0, stdout="42\n", stderr="")

    monkeypatch.setitem(
        trino_query.callback.__globals__, "_require_container_backend", lambda: None
    )
    monkeypatch.setitem(
        trino_query.callback.__globals__,
        "_trino_exec_base",
        lambda *, tty: [
            "docker",
            "compose",
            "-p",
            "demo",
            "exec",
            *([] if tty else ["-T"]),
            "trino",
            "trino",
        ],
    )
    monkeypatch.setitem(trino_query.callback.__globals__, "run_command", _run_command)

    result = CliRunner().invoke(trino_group, ["query", "--file", str(sql_file)])

    assert result.exit_code == 0
    assert result.output == "42\n"


def test_trino_query_rejects_missing_input(monkeypatch) -> None:
    """Query command should fail clearly when no SQL is provided."""
    monkeypatch.setitem(
        trino_query.callback.__globals__, "_require_container_backend", lambda: None
    )

    result = CliRunner().invoke(trino_group, ["query"])

    assert result.exit_code != 0
    assert "Error: no SQL query provided" in result.output
    assert "Provide an inline query argument or pass --file." in result.output
    assert 'Run: phlo trino query "SELECT 1"' in result.output


def test_trino_query_requires_initialized_services(monkeypatch, tmp_path) -> None:
    """Query command should hide raw compose errors before services are initialized."""
    phlo_dir = tmp_path / ".phlo"
    phlo_dir.mkdir()
    monkeypatch.chdir(tmp_path)
    monkeypatch.setitem(
        trino_query.callback.__globals__, "_require_container_backend", lambda: None
    )

    result = CliRunner().invoke(trino_group, ["query", "SELECT 1"])

    assert result.exit_code != 0
    assert "Error: Phlo services have not been initialized" in result.output
    assert "Missing: .phlo/docker-compose.yml" in result.output
    assert "Run: phlo services init" in result.output
    assert "couldn't find env file" not in result.output


def test_trino_query_surfaces_timeout(monkeypatch) -> None:
    """Query command should surface timeouts as click exceptions."""

    def _run_command(cmd, **_kwargs):
        raise TimeoutExpired(cmd=cmd, timeout=30)

    monkeypatch.setitem(
        trino_query.callback.__globals__, "_require_container_backend", lambda: None
    )
    monkeypatch.setitem(
        trino_query.callback.__globals__,
        "_trino_exec_base",
        lambda *, tty: [
            "docker",
            "compose",
            "-p",
            "demo",
            "exec",
            *([] if tty else ["-T"]),
            "trino",
            "trino",
        ],
    )
    monkeypatch.setitem(trino_query.callback.__globals__, "run_command", _run_command)

    result = CliRunner().invoke(trino_group, ["query", "SELECT 1"])

    assert result.exit_code != 0
    assert "Query timed out after 30 seconds." in result.output


def test_trino_group_defaults_to_shell(monkeypatch) -> None:
    """Bare trino group invocation should launch the interactive CLI."""
    captured: list[list[str]] = []

    callback_globals = trino_group.callback.__wrapped__.__globals__

    monkeypatch.setitem(callback_globals, "_require_container_backend", lambda: None)
    monkeypatch.setitem(
        callback_globals,
        "_trino_exec_base",
        lambda *, tty: [
            "docker",
            "compose",
            "-p",
            "demo",
            "exec",
            *([] if tty else ["-T"]),
            "trino",
            "trino",
        ],
    )

    def _subprocess_run(cmd, **_kwargs):
        captured.append(cmd)
        return CompletedProcess(cmd, 0, stdout=None, stderr=None)

    monkeypatch.setitem(
        callback_globals,
        "subprocess",
        SimpleNamespace(run=_subprocess_run),
    )

    trino_group.callback.__wrapped__(None, ("--catalog", "iceberg", "--schema", "raw"))
    assert captured == [
        [
            "docker",
            "compose",
            "-p",
            "demo",
            "exec",
            "trino",
            "trino",
            "--catalog",
            "iceberg",
            "--schema",
            "raw",
        ]
    ]
