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


def _reset_fixture(monkeypatch, tmp_path, returncode=0):
    phlo_dir = tmp_path / ".phlo"
    volume = phlo_dir / "volumes" / "postgres"
    volume.mkdir(parents=True)
    (volume / "data").write_text("keep")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("phlo.cli.commands.services.reset.ensure_compose_project", lambda: phlo_dir)
    monkeypatch.setattr(
        "phlo.cli.commands.services.reset.require_container_backend", lambda *_: None
    )
    monkeypatch.setattr("phlo.cli.commands.services.reset.get_project_name", lambda: "demo")
    monkeypatch.setattr(
        "phlo.cli.commands.services.reset.compose_base_cmd", lambda **_: ["docker", "compose"]
    )
    calls = []

    def run(cmd, **kwargs):
        calls.append(cmd)
        return CompletedProcess(cmd, returncode)

    monkeypatch.setattr("phlo.cli.commands.services.reset.run_compose", run)
    return volume, calls


def test_reset_preview_does_not_mutate(monkeypatch, tmp_path):
    import json

    volume, calls = _reset_fixture(monkeypatch, tmp_path)
    result = CliRunner().invoke(reset_cmd, ["--dry-run", "--json", "--service", "postgres"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["status"] == "planned"
    assert payload["data"]["volume_paths"] == [str(volume)]
    assert not calls
    assert (volume / "data").read_text() == "keep"


def test_reset_stop_failure_preserves_local_data(monkeypatch, tmp_path):
    import json

    volume, calls = _reset_fixture(monkeypatch, tmp_path, returncode=1)
    result = CliRunner().invoke(reset_cmd, ["--yes", "--json"])
    assert result.exit_code == 1
    assert json.loads(result.output)["reason_code"] == "service_stop_failed"
    assert calls
    assert (volume / "data").exists()


def test_reset_noninteractive_requires_confirmation(monkeypatch, tmp_path):
    volume, calls = _reset_fixture(monkeypatch, tmp_path)
    result = CliRunner().invoke(reset_cmd, ["--non-interactive"])
    assert result.exit_code != 0
    assert not calls
    assert (volume / "data").exists()


def test_reset_symlink_is_partial_failure(monkeypatch, tmp_path):
    import json

    volume, calls = _reset_fixture(monkeypatch, tmp_path)
    (volume.parent / "linked").symlink_to(volume, target_is_directory=True)
    result = CliRunner().invoke(reset_cmd, ["--yes", "--json", "--service", "linked"])
    assert result.exit_code == 1
    assert json.loads(result.output)["status"] == "partial"
    assert (volume / "data").exists()


def test_reset_interactive_decline_is_nonzero(monkeypatch, tmp_path):
    from phlo.cli import output

    volume, calls = _reset_fixture(monkeypatch, tmp_path)
    from click.testing import _NamedTextIOWrapper

    monkeypatch.setattr(_NamedTextIOWrapper, "isatty", lambda _: True)
    monkeypatch.setattr(output.click, "confirm", lambda *args, **kwargs: False)
    result = CliRunner().invoke(reset_cmd)
    assert result.exit_code == 1
    assert "Cancelled. No services or files changed." in result.output
    assert not calls
    assert (volume / "data").exists()


def test_reset_preview_preserves_backend(monkeypatch, tmp_path):
    import json
    import shlex

    _reset_fixture(monkeypatch, tmp_path)
    result = CliRunner().invoke(
        reset_cmd, ["--dry-run", "--json", "--backend", "podman", "--service", "postgres"]
    )
    assert result.exit_code == 0
    step = json.loads(result.output)["next_steps"][0]
    assert shlex.split(step["command"]) == [
        "phlo",
        "services",
        "reset",
        "--yes",
        "--backend",
        "podman",
        "--service",
        "postgres",
    ]
    assert str(tmp_path) in step["when"]
