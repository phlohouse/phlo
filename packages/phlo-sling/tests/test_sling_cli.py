"""Tests for Sling CLI commands.

Covers ad-hoc runs (target objects derived from streams, the configured
default mode, explicit objects required for wildcard streams), connection
discovery via sling conns discover, and that subprocess failures surface as
actionable errors without leaking raw Sling stderr.
"""

from __future__ import annotations

import json
import subprocess

from click.testing import CliRunner

from phlo_sling.cli_commands import conns_command, discover_command, run_command


def test_run_command_derives_target_object_from_stream(monkeypatch) -> None:
    """Ad-hoc runs should pass a target object to Sling."""
    captured: dict[str, object] = {}

    class _FakeSling:
        def __init__(self, **kwargs):
            captured.update(kwargs)

        def run(self) -> None:
            captured["ran"] = True

    monkeypatch.setattr("phlo_sling.cli_commands.apply_sling_connection_env", lambda: {})
    monkeypatch.setattr("sling.Sling", _FakeSling)

    result = CliRunner().invoke(
        run_command,
        ["--source", "SRC", "--stream", "public.users", "--target", "TGT"],
    )

    assert result.exit_code == 0
    assert captured["src_conn"] == "SRC"
    assert captured["src_stream"] == "public.users"
    assert captured["tgt_conn"] == "TGT"
    assert captured["tgt_object"] == "public.users"
    assert captured["ran"] is True


def test_run_command_uses_configured_default_mode(monkeypatch) -> None:
    """Ad-hoc runs should honor SLING_DEFAULT_MODE when --mode is omitted."""
    captured: dict[str, object] = {}

    class _FakeSling:
        def __init__(self, **kwargs):
            captured.update(kwargs)

        def run(self) -> None:
            captured["ran"] = True

    monkeypatch.setattr("phlo_sling.cli_commands.apply_sling_connection_env", lambda: {})
    monkeypatch.setattr(
        "phlo_sling.cli_commands.get_settings",
        lambda: type("Settings", (), {"sling_default_mode": "full-refresh"})(),
    )
    monkeypatch.setattr("sling.Sling", _FakeSling)

    result = CliRunner().invoke(
        run_command,
        ["--source", "SRC", "--stream", "public.users", "--target", "TGT"],
    )

    assert result.exit_code == 0
    assert captured["mode"] == "full-refresh"
    assert captured["ran"] is True


def test_run_command_requires_object_for_wildcard_stream(monkeypatch) -> None:
    """Wildcard streams need an explicit target object in ad-hoc mode."""
    monkeypatch.setattr("phlo_sling.cli_commands.apply_sling_connection_env", lambda: {})

    result = CliRunner().invoke(
        run_command,
        ["--source", "SRC", "--stream", "public.*", "--target", "TGT"],
    )

    assert result.exit_code != 0
    assert "Provide --object when --stream uses a wildcard." in result.output


def test_discover_command_uses_sling_conns_discover(monkeypatch) -> None:
    """Discovery should shell out to Sling's connection introspection command."""
    calls: list[list[str]] = []

    def _run_command(args: list[str]) -> subprocess.CompletedProcess[str]:
        calls.append(args)
        return subprocess.CompletedProcess(
            args=args,
            returncode=0,
            stdout="Database | Schema | Table\n-------- | ------ | -----\nwarehouse | public | users\n",
            stderr="",
        )

    monkeypatch.setattr("phlo_sling.cli_commands.apply_sling_connection_env", lambda: {})
    monkeypatch.setattr("phlo_sling.cli_commands._run_sling_cli_command", _run_command)

    result = CliRunner().invoke(
        discover_command,
        ["PHLO_POSTGRES", "--schema", "public", "--format", "json"],
    )

    assert result.exit_code == 0
    assert calls == [["conns", "discover", "PHLO_POSTGRES", "--pattern", "public.*"]]
    payload = json.loads(result.output)
    assert payload == [{"database": "warehouse", "schema": "public", "table": "users"}]


def test_discover_command_returns_empty_json_for_no_matches(monkeypatch) -> None:
    """JSON discovery should return an empty list when no streams match."""

    def _run_command(args: list[str]) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            args=args,
            returncode=0,
            stdout="",
            stderr="",
        )

    monkeypatch.setattr("phlo_sling.cli_commands.apply_sling_connection_env", lambda: {})
    monkeypatch.setattr("phlo_sling.cli_commands._run_sling_cli_command", _run_command)

    result = CliRunner().invoke(
        discover_command,
        ["PHLO_POSTGRES", "--schema", "missing", "--format", "json"],
    )

    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload == []


def test_discover_command_hides_raw_sling_error(monkeypatch) -> None:
    """Discovery failures should be actionable without leaking subprocess internals."""

    def _run_command(args: list[str]) -> subprocess.CompletedProcess[str]:
        raise RuntimeError("binary blew up with secret DSN")

    monkeypatch.setattr("phlo_sling.cli_commands.apply_sling_connection_env", lambda: {})
    monkeypatch.setattr("phlo_sling.cli_commands._run_sling_cli_command", _run_command)

    result = CliRunner().invoke(discover_command, ["PHLO_POSTGRES"])

    assert result.exit_code != 0
    assert "secret DSN" not in result.output
    assert "Error: Sling discovery failed" in result.output
    assert "Connection: PHLO_POSTGRES" in result.output
    assert "Run: phlo sling conns" in result.output


def test_conns_command_hides_raw_native_sling_error(monkeypatch) -> None:
    """Native connection listing should keep raw Sling exceptions in logs."""

    def stub_run_sling_cli_command(_args: list[str]) -> subprocess.CompletedProcess[str]:
        raise RuntimeError("secret DSN")

    monkeypatch.setattr(
        "phlo_sling.cli_commands._run_sling_cli_command",
        stub_run_sling_cli_command,
    )

    result = CliRunner().invoke(conns_command, ["--no-auto"])

    assert result.exit_code == 0
    assert "secret DSN" not in result.output
    assert "Native Sling connections unavailable. Run: sling conns list" in result.output
