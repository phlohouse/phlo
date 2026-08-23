"""Tests for "phlo services logs" passthrough to compose logs.

Verifies that service names and log options reach the container backend
unchanged (including package-selector aliases), that a requested backend
is honored, that `phlo logs` is the same command, and that uninitialized
projects get an actionable init hint.
"""

from __future__ import annotations

from pathlib import Path
from subprocess import CompletedProcess

from click.testing import CliRunner

from phlo.cli.commands.services.logs import logs_cmd


def test_services_logs_accepts_multiple_services_and_log_options(monkeypatch) -> None:
    captured: list[list[str]] = []

    monkeypatch.setattr(
        "phlo.cli.commands.services.logs.require_container_backend",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        "phlo.cli.commands.services.logs.ensure_compose_project", lambda: Path("/tmp/.phlo")
    )
    monkeypatch.setattr("phlo.cli.commands.services.logs.get_project_name", lambda: "demo")
    monkeypatch.setattr(
        "phlo.cli.commands.services.logs.compose_base_cmd",
        lambda **_kwargs: ["docker", "compose", "-p", "demo"],
    )
    monkeypatch.setattr(
        "phlo.cli.commands.services.logs.run_compose",
        lambda cmd, check=False, capture_output=False: (
            captured.append(cmd) or CompletedProcess(cmd, 0)
        ),
    )

    result = CliRunner().invoke(
        logs_cmd,
        [
            "--follow",
            "--lines",
            "250",
            "--since",
            "10m",
            "--timestamps",
            "dagster",
            "trino",
        ],
    )

    assert result.exit_code == 0, result.output
    assert captured == [
        [
            "docker",
            "compose",
            "-p",
            "demo",
            "logs",
            "--tail",
            "250",
            "--since",
            "10m",
            "--timestamps",
            "-f",
            "dagster",
            "trino",
        ]
    ]


def test_services_logs_uses_requested_podman_backend(monkeypatch) -> None:
    captured_base_kwargs: dict[str, object] = {}
    captured_cmd: list[str] = []

    monkeypatch.setattr(
        "phlo.cli.commands.services.logs.require_container_backend",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        "phlo.cli.commands.services.logs.ensure_compose_project", lambda: Path("/tmp/.phlo")
    )
    monkeypatch.setattr("phlo.cli.commands.services.logs.get_project_name", lambda: "demo")

    def fake_compose_base_cmd(**kwargs):
        captured_base_kwargs.update(kwargs)
        return ["podman", "compose", "-p", "demo"]

    monkeypatch.setattr("phlo.cli.commands.services.logs.compose_base_cmd", fake_compose_base_cmd)
    monkeypatch.setattr(
        "phlo.cli.commands.services.logs.run_compose",
        lambda cmd, check=False, capture_output=False: (
            captured_cmd.extend(cmd) or CompletedProcess(cmd, 0)
        ),
    )

    result = CliRunner().invoke(logs_cmd, ["--backend", "podman", "postgres"])

    assert result.exit_code == 0, result.output
    assert captured_base_kwargs["backend_name"] == "podman"
    assert captured_cmd[:5] == ["podman", "compose", "-p", "demo", "logs"]
    assert captured_cmd[-1] == "postgres"


def test_services_logs_accepts_package_selector_alias(monkeypatch) -> None:
    captured: list[list[str]] = []

    monkeypatch.setattr(
        "phlo.cli.commands.services.logs.require_container_backend",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        "phlo.cli.commands.services.logs.ensure_compose_project", lambda: Path("/tmp/.phlo")
    )
    monkeypatch.setattr("phlo.cli.commands.services.logs.get_project_name", lambda: "demo")
    monkeypatch.setattr(
        "phlo.cli.commands.services.logs.compose_base_cmd",
        lambda **_kwargs: ["docker", "compose", "-p", "demo"],
    )
    monkeypatch.setattr(
        "phlo.cli.commands.services.logs.run_compose",
        lambda cmd, check=False, capture_output=False: (
            captured.append(cmd) or CompletedProcess(cmd, 0)
        ),
    )

    result = CliRunner().invoke(logs_cmd, ["--package", "dagster,trino", "--package", "postgres"])

    assert result.exit_code == 0, result.output
    assert captured[0][-3:] == ["dagster", "trino", "postgres"]


def test_top_level_logs_is_generic_services_command() -> None:
    from phlo.cli.main import cli

    assert cli.commands["logs"] is logs_cmd


def test_services_logs_requires_initialized_services(monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "phlo.cli.commands.services.logs.require_container_backend",
        lambda *_args, **_kwargs: None,
    )

    result = CliRunner().invoke(logs_cmd, ["--tail", "0", "--no-color"])

    assert result.exit_code == 1
    assert "Phlo services have not been initialized" in result.output
    assert "Run: phlo services init" in result.output
