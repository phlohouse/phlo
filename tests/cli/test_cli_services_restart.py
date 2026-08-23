"""Tests for "phlo services restart": refuses to run before services are initialized.

Verifies the uninitialized-project failure and that an initialized compose
project restarts through the compose preflight path.
"""

from __future__ import annotations

from subprocess import CompletedProcess

from click.testing import CliRunner

from phlo.cli.commands.services.restart import restart_cmd


def test_services_restart_requires_initialized_services(monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "phlo.cli.commands.services.restart.require_container_backend",
        lambda *_args, **_kwargs: None,
    )

    result = CliRunner().invoke(restart_cmd)

    assert result.exit_code == 1
    assert "Phlo services have not been initialized" in result.output
    assert "Run: phlo services init" in result.output


def test_services_restart_uses_compose_project_preflight(monkeypatch, tmp_path) -> None:
    phlo_dir = tmp_path / ".phlo"
    phlo_dir.mkdir()
    (phlo_dir / "docker-compose.yml").write_text("services:\n  postgres: {}\n")
    (phlo_dir / ".env").write_text("")
    monkeypatch.chdir(tmp_path)

    calls: list[list[str]] = []

    def _fake_run_compose(
        cmd: list[str],
        *,
        check: bool = False,
        capture_output: bool = False,
    ) -> CompletedProcess:
        calls.append(cmd)
        return CompletedProcess(cmd, 0)

    monkeypatch.setattr(
        "phlo.cli.commands.services.restart.require_container_backend",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        "phlo.cli.commands.services.restart.ensure_compose_project",
        lambda: phlo_dir,
    )
    monkeypatch.setattr("phlo.cli.commands.services.restart.get_project_name", lambda: "demo")
    monkeypatch.setattr("phlo.cli.commands.services.restart.run_compose", _fake_run_compose)

    result = CliRunner().invoke(restart_cmd, ["--backend", "podman"])

    assert result.exit_code == 0, result.output
    # Restart is composed of exactly two backend invocations: a full project
    # down, then up -d. The chosen backend prefixes both commands.
    assert len(calls) == 2
    assert calls[0][:2] == ["podman", "compose"]
    assert calls[0][-1] == "down"
    assert calls[1][:2] == ["podman", "compose"]
    assert calls[1][-2:] == ["up", "-d"]
