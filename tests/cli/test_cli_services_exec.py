"""Tests for "phlo services exec": command construction passed through to compose.

Asserts on the exact argv handed to compose rather than execution:
everything after "--" is forwarded verbatim with -T when --no-tty is
set. A missing command or uninitialized services fail fast with exit 1.
"""

from __future__ import annotations

from pathlib import Path
from subprocess import CompletedProcess
from types import SimpleNamespace

from click.testing import CliRunner

from phlo.cli.commands.services.exec import exec_cmd


def test_services_exec_runs_command_in_service_container(monkeypatch) -> None:
    captured: list[list[str]] = []

    monkeypatch.setattr(
        "phlo.cli.commands.services.exec.require_container_backend", lambda *_args, **_kwargs: None
    )
    monkeypatch.setattr(
        "phlo.cli.commands.services.exec.ensure_compose_project", lambda: Path("/tmp/.phlo")
    )
    monkeypatch.setattr("phlo.cli.commands.services.exec.get_project_name", lambda: "demo")
    monkeypatch.setattr(
        "phlo.cli.commands.services.exec.compose_base_cmd",
        lambda **_kwargs: ["docker", "compose", "-p", "demo"],
    )
    monkeypatch.setattr(
        "phlo.cli.commands.services.exec.subprocess",
        SimpleNamespace(
            run=lambda cmd, check=False: captured.append(cmd) or CompletedProcess(cmd, 0)
        ),
    )

    result = CliRunner().invoke(
        exec_cmd,
        ["dagster", "--no-tty", "--", "dbt", "run", "--select", "dim_pokemon"],
    )

    assert result.exit_code == 0
    assert captured == [
        [
            "docker",
            "compose",
            "-p",
            "demo",
            "exec",
            "-T",
            "dagster",
            "dbt",
            "run",
            "--select",
            "dim_pokemon",
        ]
    ]


def test_services_exec_requires_command(monkeypatch) -> None:
    monkeypatch.setattr(
        "phlo.cli.commands.services.exec.require_container_backend", lambda *_args, **_kwargs: None
    )

    result = CliRunner().invoke(exec_cmd, ["dagster"])

    assert result.exit_code != 0
    assert "Provide a command after `--`." in result.output


def test_services_exec_requires_initialized_services(monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "phlo.cli.commands.services.exec.require_container_backend", lambda *_args, **_kwargs: None
    )

    result = CliRunner().invoke(exec_cmd, ["dagster", "--", "echo", "ok"])

    assert result.exit_code == 1
    assert "Phlo services have not been initialized" in result.output
    assert "Run: phlo services init" in result.output
