"""Tests for "phlo services stop".

Pins that stop runs compose down through the backend selected with --backend,
with infrastructure wiring stubbed out.
"""

from __future__ import annotations

from subprocess import CompletedProcess

from click.testing import CliRunner

from phlo.cli.commands.services.stop import stop_cmd


def test_services_stop_uses_podman_backend(monkeypatch, tmp_path) -> None:
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
        "phlo.cli.commands.services.stop.require_container_backend",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr("phlo.cli.commands.services.stop.ensure_compose_project", lambda: phlo_dir)
    monkeypatch.setattr("phlo.cli.commands.services.stop.get_project_name", lambda: "demo")
    monkeypatch.setattr("phlo.cli.commands.services.stop.run_compose", _fake_run_compose)
    monkeypatch.setattr(
        "phlo.cli.commands.services.stop._emit_service_lifecycle_events",
        lambda *args, **kwargs: None,
    )

    result = CliRunner().invoke(stop_cmd, ["--backend", "podman"])

    assert result.exit_code == 0, result.output
    assert calls
    assert calls[0][:2] == ["podman", "compose"]
    assert calls[0][-1] == "down"


def test_services_stop_down_includes_optional_profiles(monkeypatch, tmp_path) -> None:
    phlo_dir = tmp_path / ".phlo"
    phlo_dir.mkdir()
    (phlo_dir / "docker-compose.yml").write_text("services:\n  postgres: {}\n  phlo-api: {}\n")
    (phlo_dir / ".env").write_text("")
    monkeypatch.chdir(tmp_path)

    calls: list[list[str]] = []

    class FakeDiscovery:
        def get_available_profiles(self) -> set[str]:
            return {"api", "observability"}

    def _fake_run_compose(
        cmd: list[str],
        *,
        check: bool = False,
        capture_output: bool = False,
    ) -> CompletedProcess:
        calls.append(cmd)
        return CompletedProcess(cmd, 0)

    monkeypatch.setattr(
        "phlo.cli.commands.services.stop.require_container_backend",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr("phlo.cli.commands.services.stop.ensure_compose_project", lambda: phlo_dir)
    monkeypatch.setattr("phlo.cli.commands.services.stop.get_project_name", lambda: "demo")
    monkeypatch.setattr("phlo.cli.commands.services.stop.run_compose", _fake_run_compose)
    monkeypatch.setattr("phlo.cli.commands.services.stop.ServiceDiscovery", FakeDiscovery)
    monkeypatch.setattr(
        "phlo.cli.commands.services.stop._emit_service_lifecycle_events",
        lambda *args, **kwargs: None,
    )

    result = CliRunner().invoke(stop_cmd)

    assert result.exit_code == 0, result.output
    # Compose only considers default services unless every profile is passed
    # explicitly; stop must enumerate them all so profile services go down too.
    assert "--profile" in calls[0]
    assert calls[0].count("--profile") == 2
    assert "api" in calls[0]
    assert "observability" in calls[0]
    assert calls[0][-1] == "down"


def test_services_stop_profile_with_volumes_uses_down(monkeypatch, tmp_path) -> None:
    phlo_dir = tmp_path / ".phlo"
    phlo_dir.mkdir()
    (phlo_dir / "docker-compose.yml").write_text("services:\n  postgres: {}\n  phlo-api: {}\n")
    (phlo_dir / ".env").write_text("")
    monkeypatch.chdir(tmp_path)

    calls: list[list[str]] = []

    class FakeDiscovery:
        def get_available_profiles(self) -> set[str]:
            return {"api"}

    class FakeBackend:
        def list_project_containers(self, project_name: str):
            return []

    def _fake_run_compose(
        cmd: list[str],
        *,
        check: bool = False,
        capture_output: bool = False,
    ) -> CompletedProcess:
        calls.append(cmd)
        return CompletedProcess(cmd, 0)

    monkeypatch.setattr(
        "phlo.cli.commands.services.stop.require_container_backend",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr("phlo.cli.commands.services.stop.ensure_compose_project", lambda: phlo_dir)
    monkeypatch.setattr("phlo.cli.commands.services.stop.get_project_name", lambda: "demo")
    monkeypatch.setattr("phlo.cli.commands.services.stop.run_compose", _fake_run_compose)
    monkeypatch.setattr("phlo.cli.commands.services.stop.ServiceDiscovery", FakeDiscovery)
    monkeypatch.setattr(
        "phlo.cli.commands.services.stop.select_project_container_backend",
        lambda **_kwargs: FakeBackend(),
    )
    monkeypatch.setattr(
        "phlo.cli.commands.services.stop._emit_service_lifecycle_events",
        lambda *args, **kwargs: None,
    )

    result = CliRunner().invoke(stop_cmd, ["--profile", "api", "--volumes"])

    assert result.exit_code == 0, result.output
    assert calls
    assert calls[0][-2:] == ["down", "-v"]
    assert "--profile" in calls[0]
    assert "api" in calls[0]


def test_services_stop_fails_when_containers_remain(monkeypatch, tmp_path) -> None:
    phlo_dir = tmp_path / ".phlo"
    phlo_dir.mkdir()
    (phlo_dir / "docker-compose.yml").write_text("services:\n  postgres: {}\n")
    (phlo_dir / ".env").write_text("")
    monkeypatch.chdir(tmp_path)

    def _fake_run_compose(
        cmd: list[str],
        *,
        check: bool = False,
        capture_output: bool = False,
    ) -> CompletedProcess:
        return CompletedProcess(cmd, 0)

    class FakeBackend:
        def list_project_containers(self, project_name: str):
            from phlo.cli.infrastructure.container_backend import ContainerInfo

            return [
                ContainerInfo(
                    service="postgres",
                    name=f"{project_name}-postgres-1",
                    state="running",
                    labels={},
                    ports="",
                )
            ]

    monkeypatch.setattr(
        "phlo.cli.commands.services.stop.require_container_backend",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr("phlo.cli.commands.services.stop.ensure_compose_project", lambda: phlo_dir)
    monkeypatch.setattr("phlo.cli.commands.services.stop.get_project_name", lambda: "demo")
    monkeypatch.setattr("phlo.cli.commands.services.stop.run_compose", _fake_run_compose)
    monkeypatch.setattr(
        "phlo.cli.commands.services.stop.select_project_container_backend",
        lambda **_kwargs: FakeBackend(),
    )
    monkeypatch.setattr(
        "phlo.cli.commands.services.stop._emit_service_lifecycle_events",
        lambda *args, **kwargs: None,
    )

    result = CliRunner().invoke(stop_cmd)

    assert result.exit_code != 0
    assert "containers still running" in result.output


def test_services_stop_requires_initialized_services(monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "phlo.cli.commands.services.stop.require_container_backend",
        lambda *_args, **_kwargs: None,
    )

    result = CliRunner().invoke(stop_cmd)

    assert result.exit_code == 1
    assert "Phlo services have not been initialized" in result.output
    assert "Run: phlo services init" in result.output
