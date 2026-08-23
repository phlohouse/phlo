"""Runtime contract checks for observatory service packaging.

The bundled container must ship the docker CLI for status discovery while
never mounting the host Docker socket.
"""

from importlib import resources


def test_observatory_service_does_not_mount_docker_socket() -> None:
    """Bundled defaults should not expose host Docker daemon control."""
    service_yaml = resources.files("phlo_observatory").joinpath("service.yaml").read_text()
    assert "/var/run/docker.sock" not in service_yaml


def test_observatory_dockerfile_installs_docker_cli() -> None:
    """Container image should include docker CLI for service status discovery."""
    dockerfile = resources.files("phlo_observatory").joinpath("Dockerfile").read_text()
    assert "apk add --no-cache docker-cli" in dockerfile
