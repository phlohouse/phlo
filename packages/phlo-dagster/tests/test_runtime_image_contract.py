"""Contract tests for the Dagster runtime image.

Text mirroring of the Dockerfile and entrypoint is deliberately avoided:
behavioral guarantees are exercised by building an image and running the
real entrypoint, and structural facts are asserted against parsed
instructions or the parsed service definition.
"""

from __future__ import annotations

import shutil
import subprocess
from importlib import resources
from pathlib import Path

import pytest
import yaml


def _runtime_resource(name: str) -> str:
    return resources.files("phlo_dagster").joinpath(name).read_text()


def _dockerfile_instruction_lines(text: str) -> list[str]:
    return [
        line.strip()
        for line in text.splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]


def test_dagster_runtime_image_is_glibc_based_for_external_binaries() -> None:
    """Sling publishes no musl build; an Alpine base would break ingest assets."""
    lines = _dockerfile_instruction_lines(_runtime_resource("Dockerfile"))
    first_from = next(line for line in lines if line.startswith("FROM "))
    assert first_from.split()[1] == "python:3.12-slim"


def test_dagster_service_uses_the_image_bootstrap_script() -> None:
    """The DAGSTER_HOME bind mount must not hide the image bootstrap script."""
    definition = yaml.safe_load(_runtime_resource("service.yaml"))
    assert definition["compose"]["entrypoint"] == ["/usr/local/bin/phlo-dagster-entrypoint.sh"]


def test_dagster_runtime_entrypoint_gives_an_unmapped_uid_an_isolated_writable_home(
    tmp_path: Path,
) -> None:
    """Root bootstrap must not leave runtime telemetry or logs owned by root."""
    if shutil.which("docker") is None:
        pytest.skip("Docker is required for the runtime ownership contract")

    docker_info = subprocess.run(["docker", "info"], capture_output=True, text=True)
    if docker_info.returncode:
        pytest.skip("Docker daemon is unavailable for the runtime ownership contract")

    entrypoint = resources.files("phlo_dagster").joinpath("entrypoint.sh")
    (tmp_path / "entrypoint.sh").write_text(entrypoint.read_text())
    (tmp_path / "Dockerfile").write_text(
        "\n".join(
            [
                "FROM python:3.12-slim",
                "RUN apt-get update && apt-get install --yes --no-install-recommends bash gosu",
                "RUN mkdir -p /opt/dagster",
                "COPY entrypoint.sh /usr/local/bin/phlo-dagster-entrypoint.sh",
                "RUN chmod +x /usr/local/bin/phlo-dagster-entrypoint.sh",
                'ENTRYPOINT ["/usr/local/bin/phlo-dagster-entrypoint.sh"]',
            ]
        )
    )
    image_tag = f"phlo-dagster-runtime-contract-{tmp_path.name}"
    build = subprocess.run(
        ["docker", "build", "--quiet", "--tag", image_tag, str(tmp_path)],
        capture_output=True,
        text=True,
    )
    assert build.returncode == 0, build.stderr

    try:
        runtime = subprocess.run(
            [
                "docker",
                "run",
                "--rm",
                "-e",
                "PHLO_RUNTIME_UID=12345",
                "-e",
                "PHLO_RUNTIME_GID=23456",
                image_tag,
                "sh",
                "-ec",
                'test "$(id -u)" = 12345; '
                'test "$HOME" = /var/lib/phlo-runtime; '
                'mkdir -p "$HOME/.dagster"; '
                'touch "$HOME/.dagster/telemetry"; '
                "test -f /tmp/phlo-dagster-ready; "
                "printf runtime-log >> /tmp/phlo-20260819.log; "
                "test -w /tmp/phlo-20260819.log",
            ],
            capture_output=True,
            text=True,
        )
        assert runtime.returncode == 0, runtime.stderr
    finally:
        subprocess.run(["docker", "image", "rm", "--force", image_tag], capture_output=True)
