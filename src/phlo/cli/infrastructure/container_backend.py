"""Container backend detection and control for Docker and Podman.

Both backends implement a common protocol over their compose tooling. Backend
selection follows CLI override, then environment, then phlo.yaml infrastructure
config; an unsupported choice is an error, never a fallback.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol, cast

BackendName = Literal["docker", "podman", "auto"]


@dataclass(frozen=True)
class ContainerInfo:
    """Describe one container in a compose project."""

    service: str
    name: str
    state: str
    labels: dict[str, str]
    ports: str


class ContainerBackend(Protocol):
    """Contract shared by the Docker and Podman backends."""

    name: str

    def compose_base_cmd(
        self,
        *,
        phlo_dir: Path,
        project_name: str,
        profiles: tuple[str, ...] = (),
    ) -> list[str]:
        """Return base compose command tokens."""

    def check_available(self) -> tuple[bool, str | None]:
        """Return availability and remediation message."""

    def list_project_containers(self, project_name: str) -> list[ContainerInfo]:
        """Return containers for a compose project."""

    def container_exec_cmd(
        self,
        *,
        container_name: str,
        command: list[str],
        env: dict[str, str] | None = None,
        workdir: str | None = None,
        user: str | None = None,
    ) -> list[str]:
        """Return command tokens for executing a process inside a running container."""


def _compose_base_cmd(
    *,
    binary: str,
    phlo_dir: Path,
    project_name: str,
    profiles: tuple[str, ...] = (),
) -> list[str]:
    compose_file = phlo_dir / "docker-compose.yml"
    env_file = phlo_dir / ".env"
    env_local_file = phlo_dir / ".env.local"
    cmd = [binary]
    if binary != "docker-compose":
        cmd.append("compose")
    cmd.extend(
        [
            "-p",
            project_name,
            "-f",
            str(compose_file),
            "--env-file",
            str(env_file),
        ]
    )
    # The local overrides file is appended last on purpose: compose resolves
    # conflicting keys in favor of the later --env-file.
    if env_local_file.exists():
        cmd.extend(["--env-file", str(env_local_file)])
    for profile in profiles:
        cmd.extend(["--profile", profile])
    return cmd


def _parse_docker_labels(labels: str) -> dict[str, str]:
    parsed: dict[str, str] = {}
    for item in labels.split(","):
        if "=" in item:
            key, value = item.split("=", 1)
            parsed[key] = value
    return parsed


def _coerce_podman_name(value: object) -> str:
    if isinstance(value, list):
        return ", ".join(str(item) for item in value)
    return str(value or "")


def _format_podman_ports(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if not isinstance(value, list):
        return str(value)

    formatted: list[str] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        port_info = cast("dict[str, object]", item)
        host_port = port_info.get("host_port")
        container_port = port_info.get("container_port")
        if host_port in (None, "") or container_port in (None, ""):
            continue
        host_ip = str(port_info.get("host_ip") or "0.0.0.0")
        protocol = str(port_info.get("protocol") or "tcp")
        formatted.append(f"{host_ip}:{host_port}->{container_port}/{protocol}")
    return ", ".join(formatted)


def _podman_service_label(labels: dict[str, str]) -> str:
    return labels.get("com.docker.compose.service") or labels.get("io.podman.compose.service") or ""


class DockerBackend:
    """Manage containers through the docker CLI and Docker Compose."""

    name = "docker"

    @staticmethod
    def _compose_binary() -> str | None:
        if shutil.which("docker") is not None:
            result = subprocess.run(
                ["docker", "compose", "version"],
                capture_output=True,
                text=True,
                check=False,
                timeout=10,
            )
            if result.returncode == 0:
                return "docker"
        if shutil.which("docker-compose") is not None:
            result = subprocess.run(
                ["docker-compose", "version"],
                capture_output=True,
                text=True,
                check=False,
                timeout=10,
            )
            if result.returncode == 0:
                return "docker-compose"
        return None

    def compose_base_cmd(
        self,
        *,
        phlo_dir: Path,
        project_name: str,
        profiles: tuple[str, ...] = (),
    ) -> list[str]:
        """Return base compose tokens using the best available docker compose entrypoint."""
        binary = self._compose_binary() or self.name
        return _compose_base_cmd(
            binary=binary,
            phlo_dir=phlo_dir,
            project_name=project_name,
            profiles=profiles,
        )

    def check_available(self) -> tuple[bool, str | None]:
        """Report whether docker and a working compose binary are both installed."""
        if shutil.which("docker") is None:
            return False, "Install Docker Desktop or ensure docker is on PATH."
        if self._compose_binary() is None:
            return False, "Install Docker Compose v2 or update Docker Desktop."
        return True, None

    def list_project_containers(self, project_name: str) -> list[ContainerInfo]:
        """List project containers by filtering docker ps on the compose project label."""
        result = subprocess.run(
            [
                "docker",
                "ps",
                "--filter",
                f"label=com.docker.compose.project={project_name}",
                "--format",
                "{{json .}}",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0 or not result.stdout.strip():
            return []

        containers: list[ContainerInfo] = []
        for line in result.stdout.strip().splitlines():
            info = json.loads(line)
            labels = _parse_docker_labels(info.get("Labels", ""))
            service = labels.get("com.docker.compose.service", "")
            if not service:
                continue
            containers.append(
                ContainerInfo(
                    service=service,
                    name=info.get("Names", ""),
                    state=info.get("State", ""),
                    labels=labels,
                    ports=info.get("Ports", ""),
                )
            )
        return containers

    def container_exec_cmd(
        self,
        *,
        container_name: str,
        command: list[str],
        env: dict[str, str] | None = None,
        workdir: str | None = None,
        user: str | None = None,
    ) -> list[str]:
        """Build docker exec argv, optionally setting user, environment, and workdir."""
        cmd = ["docker", "exec"]
        if user:
            cmd.extend(["--user", user])
        for key, value in (env or {}).items():
            cmd.extend(["-e", f"{key}={value}"])
        if workdir:
            cmd.extend(["-w", workdir])
        cmd.append(container_name)
        cmd.extend(command)
        return cmd


class PodmanBackend:
    """Manage containers through the podman CLI and its compose provider."""

    name = "podman"

    def compose_base_cmd(
        self,
        *,
        phlo_dir: Path,
        project_name: str,
        profiles: tuple[str, ...] = (),
    ) -> list[str]:
        """Return base podman compose command tokens."""
        return _compose_base_cmd(
            binary=self.name,
            phlo_dir=phlo_dir,
            project_name=project_name,
            profiles=profiles,
        )

    def check_available(self) -> tuple[bool, str | None]:
        """Check that podman is installed, its machine runs, and compose works."""
        if shutil.which("podman") is None:
            return False, "Install Podman Desktop or ensure podman is on PATH."
        info = subprocess.run(
            ["podman", "info"],
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
        )
        if info.returncode != 0:
            return False, "Start Podman with `podman machine start`, then retry."
        compose = subprocess.run(
            ["podman", "compose", "version"],
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
        )
        if compose.returncode != 0:
            return False, "Install or configure a Podman compose provider."
        return True, None

    def list_project_containers(self, project_name: str) -> list[ContainerInfo]:
        """List project containers under either compose label scheme, deduplicating by name."""
        # Containers may carry either docker-compose or podman-compose project
        # labels, so both filters run and deduplicate by container name.
        containers_by_name: dict[str, ContainerInfo] = {}
        for label in (
            "com.docker.compose.project",
            "io.podman.compose.project",
        ):
            result = subprocess.run(
                [
                    "podman",
                    "ps",
                    "--filter",
                    f"label={label}={project_name}",
                    "--format",
                    "json",
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            if result.returncode != 0 or not result.stdout.strip():
                continue

            try:
                payload = json.loads(result.stdout)
            except json.JSONDecodeError:
                continue

            for info in payload:
                container = self._container_info_from_ps(info)
                if container is None:
                    continue
                containers_by_name[container.name] = container
        return list(containers_by_name.values())

    def _container_info_from_ps(self, info: dict) -> ContainerInfo | None:
        raw_labels = info.get("Labels", {}) or {}
        labels = {str(key): str(value) for key, value in raw_labels.items()}
        service = _podman_service_label(labels)
        if not service:
            return None
        return ContainerInfo(
            service=service,
            name=_coerce_podman_name(info.get("Names")),
            state=str(info.get("State", "")),
            labels=labels,
            ports=_format_podman_ports(info.get("Ports")),
        )

    def container_exec_cmd(
        self,
        *,
        container_name: str,
        command: list[str],
        env: dict[str, str] | None = None,
        workdir: str | None = None,
        user: str | None = None,
    ) -> list[str]:
        """Build podman exec argv, optionally setting user, environment, and workdir."""
        cmd = ["podman", "exec"]
        if user:
            cmd.extend(["--user", user])
        for key, value in (env or {}).items():
            cmd.extend(["-e", f"{key}={value}"])
        if workdir:
            cmd.extend(["-w", workdir])
        cmd.append(container_name)
        cmd.extend(command)
        return cmd


def select_container_backend(
    *,
    cli_backend: str | None,
    config_backend: str | None,
) -> ContainerBackend:
    """Resolve the backend from CLI flag, PHLO_CONTAINER_BACKEND, config, or auto-detection.

    Raises ValueError when the resolved backend is unsupported.
    """
    selected = (
        cli_backend or os.environ.get("PHLO_CONTAINER_BACKEND") or config_backend or "docker"
    ).strip()
    if selected == "auto":
        selected = "docker" if shutil.which("docker") else "podman"
    if selected == "docker":
        return DockerBackend()
    if selected == "podman":
        return PodmanBackend()
    raise ValueError(f"Unsupported container backend: {selected}")


def select_project_container_backend(*, cli_backend: str | None = None) -> ContainerBackend:
    """Select backend using CLI override, environment, then phlo.yaml infrastructure config."""
    config_backend = None
    try:
        from phlo.infrastructure.config import load_infrastructure_config

        config_backend = load_infrastructure_config(Path.cwd()).container_backend
    except Exception:
        config_backend = None
    return select_container_backend(cli_backend=cli_backend, config_backend=config_backend)
