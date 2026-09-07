"""Tests for Docker/Podman backend selection and compose command
construction, including env file wiring and timeouts."""

from __future__ import annotations

import time
from pathlib import Path
from subprocess import CompletedProcess, TimeoutExpired

import pytest

from phlo.cli.commands.services.utils import require_container_backend
from phlo.cli.infrastructure.container_backend import (
    DockerBackend,
    PodmanBackend,
    ServiceStatus,
    select_container_backend,
    select_project_container_backend,
)
from phlo.config_schema import InfrastructureConfig


def test_docker_backend_compose_base_cmd_includes_env_files(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    phlo_dir = tmp_path / ".phlo"
    phlo_dir.mkdir()
    (phlo_dir / "docker-compose.yml").write_text("services: {}\n")
    (phlo_dir / ".env").write_text("POSTGRES_PORT=5432\n")
    (phlo_dir / ".env.local").write_text("POSTGRES_PASSWORD=secret\n")
    monkeypatch.setattr(DockerBackend, "_compose_binary", lambda self: "docker")

    cmd = DockerBackend().compose_base_cmd(
        phlo_dir=phlo_dir,
        project_name="demo",
        profiles=("observability",),
    )

    assert cmd == [
        "docker",
        "compose",
        "-p",
        "demo",
        "-f",
        str(phlo_dir / "docker-compose.yml"),
        "--env-file",
        str(phlo_dir / ".env"),
        "--env-file",
        str(phlo_dir / ".env.local"),
        "--profile",
        "observability",
    ]


def test_docker_backend_compose_base_cmd_supports_standalone_compose(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    phlo_dir = tmp_path / ".phlo"
    phlo_dir.mkdir()
    (phlo_dir / "docker-compose.yml").write_text("services: {}\n")

    monkeypatch.setattr(DockerBackend, "_compose_binary", lambda self: "docker-compose")

    cmd = DockerBackend().compose_base_cmd(
        phlo_dir=phlo_dir,
        project_name="demo",
    )

    assert cmd == [
        "docker-compose",
        "-p",
        "demo",
        "-f",
        str(phlo_dir / "docker-compose.yml"),
        "--env-file",
        str(phlo_dir / ".env"),
    ]


def test_docker_backend_container_exec_cmd_builds_docker_exec() -> None:
    cmd = DockerBackend().container_exec_cmd(
        container_name="demo-dagster-1",
        env={"PHLO_PROJECT_PATH": "/app"},
        workdir="/app",
        user="1001:1001",
        command=["dagster", "asset", "materialize"],
    )

    assert cmd == [
        "docker",
        "exec",
        "--user",
        "1001:1001",
        "-e",
        "PHLO_PROJECT_PATH=/app",
        "-w",
        "/app",
        "demo-dagster-1",
        "dagster",
        "asset",
        "materialize",
    ]


def test_docker_backend_reports_stopped_and_healthy_service_statuses(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _run(cmd: list[str], **_kwargs) -> CompletedProcess:
        if cmd[:3] == ["docker", "ps", "--all"]:
            return CompletedProcess(
                cmd,
                0,
                stdout=(
                    '{"Names":"demo_database_1","State":"running",'
                    '"Labels":"com.docker.compose.service=database"}\n'
                    '{"Names":"demo_worker_1","State":"exited",'
                    '"Labels":"com.docker.compose.service=worker"}\n'
                ),
                stderr="",
            )
        assert cmd[:4] == ["docker", "inspect", "--format", "{{json .State}}"]
        return CompletedProcess(
            cmd,
            0,
            stdout=(
                '{"Status":"running","Health":{"Status":"healthy"}}\n'
                '{"Status":"exited","ExitCode":0}\n'
            ),
            stderr="",
        )

    monkeypatch.setattr("phlo.cli.infrastructure.container_backend.subprocess.run", _run)

    statuses = DockerBackend().project_service_statuses("demo", deadline=time.monotonic() + 1)

    assert statuses == [
        ServiceStatus(service="database", state="running", health="healthy"),
        ServiceStatus(service="worker", state="exited", health=None, exit_code=0),
    ]


def test_batched_inspection_uses_captured_remaining_deadline_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from phlo.cli.infrastructure import container_backend

    time_values = iter([10.0, 11.0])
    observed_timeouts: list[float | None] = []

    def _run(_cmd: list[str], **kwargs) -> CompletedProcess:
        observed_timeouts.append(kwargs["timeout"])
        return CompletedProcess(_cmd, 0, stdout='{"Status":"running"}\n', stderr="")

    monkeypatch.setattr(container_backend.time, "monotonic", lambda: next(time_values))
    monkeypatch.setattr(container_backend.subprocess, "run", _run)

    statuses = container_backend._inspect_container_states(
        "docker", ["demo-service-1"], deadline=10.5
    )

    assert statuses == {"demo-service-1": ("running", None, None)}
    assert observed_timeouts == [0.5]


def test_podman_backend_compose_base_cmd_uses_podman(tmp_path: Path) -> None:
    phlo_dir = tmp_path / ".phlo"
    phlo_dir.mkdir()
    (phlo_dir / "docker-compose.yml").write_text("services: {}\n")
    (phlo_dir / ".env").write_text("")

    cmd = PodmanBackend().compose_base_cmd(
        phlo_dir=phlo_dir,
        project_name="demo",
    )

    assert cmd[:2] == ["podman", "compose"]
    assert str(phlo_dir / "docker-compose.yml") in cmd


def test_podman_backend_container_exec_cmd_builds_podman_exec() -> None:
    cmd = PodmanBackend().container_exec_cmd(
        container_name="demo_dagster_1",
        env={"PHLO_PROJECT_PATH": "/app"},
        workdir="/app",
        user="1001:1001",
        command=["dagster", "asset", "materialize"],
    )

    assert cmd == [
        "podman",
        "exec",
        "--user",
        "1001:1001",
        "-e",
        "PHLO_PROJECT_PATH=/app",
        "-w",
        "/app",
        "demo_dagster_1",
        "dagster",
        "asset",
        "materialize",
    ]


def test_podman_backend_lists_containers_with_podman_compose_labels(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[list[str]] = []

    def _run(cmd: list[str], **_kwargs) -> CompletedProcess:
        calls.append(cmd)
        if "label=com.docker.compose.project=demo" in cmd:
            return CompletedProcess(cmd, 0, stdout="[]", stderr="")
        if "label=io.podman.compose.project=demo" in cmd:
            return CompletedProcess(
                cmd,
                0,
                stdout=(
                    '[{"Names":["demo_postgres_1"],"State":"running",'
                    '"Labels":{"io.podman.compose.project":"demo",'
                    '"io.podman.compose.service":"postgres"},'
                    '"Ports":[{"host_ip":"0.0.0.0","container_port":5432,'
                    '"host_port":15432,"range":1,"protocol":"tcp"}]}]'
                ),
                stderr="",
            )
        raise AssertionError(f"unexpected command: {cmd}")

    monkeypatch.setattr(
        "phlo.cli.infrastructure.container_backend.subprocess.run",
        _run,
    )

    containers = PodmanBackend().list_project_containers("demo")

    assert [container.service for container in containers] == ["postgres"]
    assert containers[0].name == "demo_postgres_1"
    assert containers[0].ports == "0.0.0.0:15432->5432/tcp"
    assert any("label=io.podman.compose.project=demo" in call for call in calls)


def test_podman_backend_reports_healthcheck_and_completion_exit_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _run(cmd: list[str], **_kwargs) -> CompletedProcess:
        if cmd[:3] == ["podman", "ps", "--all"]:
            return CompletedProcess(
                cmd,
                0,
                stdout=(
                    '[{"Names":["demo_database_1"],"State":"running",'
                    '"Labels":{"io.podman.compose.service":"database"}},'
                    '{"Names":["demo_setup_1"],"State":"exited",'
                    '"Labels":{"io.podman.compose.service":"setup"}}]'
                ),
                stderr="",
            )
        assert cmd[:4] == ["podman", "inspect", "--format", "{{json .State}}"]
        return CompletedProcess(
            cmd,
            0,
            stdout=(
                '{"Status":"running","Healthcheck":{"Status":"healthy"}}\n'
                '{"Status":"exited","ExitCode":0}\n'
            ),
            stderr="",
        )

    monkeypatch.setattr("phlo.cli.infrastructure.container_backend.subprocess.run", _run)

    statuses = PodmanBackend().project_service_statuses("demo", deadline=time.monotonic() + 1)

    assert statuses == [
        ServiceStatus(service="database", state="running", health="healthy"),
        ServiceStatus(service="setup", state="exited", health=None, exit_code=0),
    ]


def test_select_backend_defaults_to_docker(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PHLO_CONTAINER_BACKEND", raising=False)

    backend = select_container_backend(cli_backend=None, config_backend=None)

    assert isinstance(backend, DockerBackend)


def test_select_backend_uses_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PHLO_CONTAINER_BACKEND", "podman")

    backend = select_container_backend(cli_backend=None, config_backend=None)

    assert backend.name == "podman"


def test_select_backend_cli_overrides_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PHLO_CONTAINER_BACKEND", "podman")

    backend = select_container_backend(cli_backend="docker", config_backend=None)

    assert backend.name == "docker"


def test_select_project_backend_uses_config(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PHLO_CONTAINER_BACKEND", raising=False)

    class Config:
        container_backend = "podman"

    monkeypatch.setattr(
        "phlo.infrastructure.config.load_infrastructure_config",
        lambda *_args: Config(),
    )

    backend = select_project_container_backend()

    assert backend.name == "podman"


def test_select_backend_rejects_unknown(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PHLO_CONTAINER_BACKEND", "containerd")

    with pytest.raises(ValueError, match="containerd"):
        select_container_backend(cli_backend=None, config_backend=None)


def test_infrastructure_config_accepts_container_backend() -> None:
    config = InfrastructureConfig(container_backend="podman")

    assert config.container_backend == "podman"


def test_require_container_backend_reports_availability_timeout(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    class SlowBackend:
        name = "podman"

        def check_available(self) -> tuple[bool, str | None]:
            raise TimeoutExpired(["podman", "info"], timeout=10)

    monkeypatch.setattr(
        "phlo.cli.commands.services.utils.select_project_container_backend",
        lambda cli_backend=None: SlowBackend(),
    )

    with pytest.raises(SystemExit) as exc_info:
        require_container_backend("podman")

    assert exc_info.value.code == 1
    assert "podman availability check timed out" in capsys.readouterr().err
