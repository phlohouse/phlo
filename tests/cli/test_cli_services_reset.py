"""Tests for `phlo services reset` preflight behavior.

Ensures reset refuses to run before services are initialized and goes through
the compose project preflight.
"""

from __future__ import annotations

from subprocess import CompletedProcess

from click.testing import CliRunner

from phlo.cli.commands.services.reset import reset_cmd


def test_services_reset_requires_initialized_services(monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "phlo.cli.commands.services.reset.require_container_backend",
        lambda *_args, **_kwargs: None,
    )

    result = CliRunner().invoke(reset_cmd, ["--yes"])

    assert result.exit_code == 1
    assert "Phlo services have not been initialized" in result.output
    assert "Run: phlo services init" in result.output


def test_services_reset_uses_compose_project_preflight(monkeypatch, tmp_path) -> None:
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
        "phlo.cli.commands.services.reset.require_container_backend",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        "phlo.cli.commands.services.reset.ensure_compose_project",
        lambda: phlo_dir,
    )
    monkeypatch.setattr("phlo.cli.commands.services.reset.get_project_name", lambda: "demo")
    monkeypatch.setattr("phlo.cli.commands.services.reset.run_compose", _fake_run_compose)

    result = CliRunner().invoke(reset_cmd, ["--yes", "--backend", "podman"])

    assert result.exit_code == 0, result.output
    assert calls
    # Reset must take data volumes with the project: the -v flag is the
    # destructive half of the contract.
    assert calls[0][:2] == ["podman", "compose"]
    assert calls[0][-2:] == ["down", "-v"]
