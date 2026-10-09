"""Existing-volume migration through the public CLI, including real Docker."""

import copy
import json
import subprocess
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml
from click.testing import CliRunner

from phlo.cli.infrastructure.command import CommandError
from phlo_minio.cli import minio_group

IMAGE = "ghcr.io/phlohouse/phlo-minio:0.17.0"


def _project(root: Path, directory: str = "/data") -> Path:
    state = root / ".phlo"
    state.mkdir()
    (state / ".env").write_text("MINIO_ROOT_USER=migration-user\n")
    (state / ".env.local").write_text("MINIO_ROOT_PASSWORD=migration-password\n")
    config = {
        "services": {
            "minio": {
                "image": IMAGE,
                "user": "0:0",
                "command": ["server", directory, "--console-address", ":9001"],
                "environment": {
                    "MINIO_ROOT_USER": "${MINIO_ROOT_USER}",
                    "MINIO_ROOT_PASSWORD": "${MINIO_ROOT_PASSWORD}",
                },
                "volumes": [f"minio-data:{directory}"],
                "healthcheck": {
                    "test": ["CMD", "curl", "-f", "http://localhost:9000/minio/health/ready"],
                    "interval": "1s",
                    "timeout": "2s",
                    "retries": 30,
                },
            },
            "minio-setup": {
                "image": IMAGE,
                "entrypoint": ["/bin/sh", "-ec"],
                "command": [
                    "mc alias set local http://minio:9000 migration-user migration-password && mc mb --ignore-existing local/lake"
                ],
            },
        },
        "volumes": {"minio-data": {}},
    }
    path = state / "docker-compose.yml"
    path.write_text(yaml.safe_dump(config, sort_keys=False))
    return path


@pytest.mark.parametrize(
    "outcome",
    [
        "preview",
        "apply",
        "apply-rw",
        "anonymous-volume",
        "read-only",
        "pull-failure",
        "missing-volume",
        "bind-mount",
        "distributed",
        "denied",
        "override",
        "volume-driver",
        "podman",
    ],
)
def test_migration_preserves_config_and_secrets(tmp_path, monkeypatch, outcome):
    path = _project(tmp_path)
    upstream = yaml.safe_load(path.read_text())
    upstream["services"]["minio"]["image"] = "quay.io/minio/minio:old-server"
    upstream["services"]["minio-setup"]["image"] = "quay.io/minio/mc:old-client"
    if outcome == "bind-mount":
        upstream["services"]["minio"]["volumes"] = ["./data:/data"]
    if outcome == "apply-rw":
        upstream["services"]["minio"]["volumes"] = ["minio-data:/data:rw"]
    if outcome == "anonymous-volume":
        upstream["services"]["minio"]["volumes"] = [{"type": "volume", "target": "/data"}]
    if outcome == "read-only":
        upstream["services"]["minio"]["volumes"] = ["minio-data:/data:ro"]
    if outcome == "distributed":
        upstream["services"]["minio"]["command"] = ["server", "/data", "/other-data"]
    path.write_text(yaml.safe_dump(upstream, sort_keys=False))
    original = path.read_text()
    original_config = yaml.safe_load(original)
    effective = copy.deepcopy(original_config)
    effective["volumes"]["minio-data"]["name"] = "existing-lakehouse_minio-data"
    if outcome == "volume-driver":
        effective["volumes"]["minio-data"]["driver"] = "external-storage"
    if outcome == "override":
        overrides = path.parent / "overrides"
        overrides.mkdir()
        (overrides / "compose.yaml").write_text(
            "services:\n  minio:\n    image: custom/minio:latest\n"
        )
    if outcome == "podman":
        monkeypatch.setenv("PHLO_CONTAINER_BACKEND", "podman")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("phlo_minio.migrate.require_container_backend", lambda: None)
    if outcome == "denied":
        monkeypatch.setattr(
            "phlo.cli.authorization_wrappers.check_cli_surface_active", lambda: True
        )
        adapter = SimpleNamespace(
            enforce_mutation=lambda *args: SimpleNamespace(
                allowed=False, explanation="migration forbidden", reason_code="forbidden"
            )
        )
        monkeypatch.setattr("phlo_minio.migrate.get_minio_cli_adapter", lambda: adapter)

    def execute(command, **kwargs):
        if "config" in command:
            return subprocess.CompletedProcess(command, 0, json.dumps(effective), "")
        if (outcome == "pull-failure" and "pull" in command) or (
            outcome == "missing-volume" and "inspect" in command
        ):
            raise CommandError(tuple(command), 1, "", "unavailable")
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr("phlo_minio.migrate.run_command", execute)
    result = CliRunner().invoke(
        minio_group, ["migrate-image", *([] if outcome == "preview" else ["--apply"])]
    )
    assert (result.exit_code == 0) == (outcome in {"preview", "apply", "apply-rw"}), result.output
    if outcome == "denied":
        assert "Authorization denied" in result.output
    if outcome == "podman":
        assert "requires Docker Compose v2" in result.output
    if outcome in {"anonymous-volume", "read-only"}:
        assert "Expected a writable named MinIO data volume" in result.output
    assert (path.parent / ".env.local").read_text() == "MINIO_ROOT_PASSWORD=migration-password\n"
    if outcome not in {"apply", "apply-rw"}:
        assert path.read_text() == original
    else:
        updated = yaml.safe_load(path.read_text())
        assert (
            updated["services"]["minio"]["volumes"]
            == original_config["services"]["minio"]["volumes"]
        )
        assert (
            updated["services"]["minio"]["environment"]
            == original_config["services"]["minio"]["environment"]
        )
        assert updated["services"]["minio"]["user"] == "1001:0"
        assert updated["services"]["minio"]["image"] == IMAGE
        assert updated["services"]["minio-setup"]["image"] == IMAGE
        assert (
            updated["services"]["minio-setup"]["entrypoint"]
            == original_config["services"]["minio-setup"]["entrypoint"]
        )
        backup = path.with_name("docker-compose.pre-minio-image.yml")
        assert backup.read_text() == original
        assert backup.stat().st_mode & 0o777 == 0o600


@pytest.mark.integration
@pytest.mark.parametrize("directory", ["/data", "/bitnami/minio/data"])
def test_migration_keeps_root_owned_objects_readable(tmp_path, monkeypatch, directory):
    # The suite harness owns a different MinIO server; use this fixture's env files.
    for variable in ("MINIO_ROOT_USER", "MINIO_ROOT_PASSWORD"):
        monkeypatch.delenv(variable, raising=False)
    path = _project(tmp_path, directory)
    name = "phlo-minio-migrate-" + uuid.uuid4().hex[:10]
    (tmp_path / "phlo.yaml").write_text(f"name: {name}\n")
    monkeypatch.chdir(tmp_path)
    base = [
        "docker",
        "compose",
        "-p",
        name,
        "-f",
        str(path),
        "--env-file",
        str(path.parent / ".env"),
        "--env-file",
        str(path.parent / ".env.local"),
    ]

    def compose(*args, input=None):
        return subprocess.run(
            [*base, *args], input=input, text=True, capture_output=True, check=True
        ).stdout

    try:
        compose("up", "-d", "--wait", "minio")
        compose(
            "exec",
            "-T",
            "minio",
            "mc",
            "alias",
            "set",
            "local",
            "http://localhost:9000",
            "migration-user",
            "migration-password",
        )
        compose("exec", "-T", "minio", "mc", "mb", "local/preexisting")
        compose(
            "exec",
            "-T",
            "minio",
            "mc",
            "pipe",
            "local/preexisting/probe",
            input="existing-object-before-upgrade",
        )
        original_volume = json.loads(compose("config", "--format", "json"))["volumes"][
            "minio-data"
        ]["name"]
        compose("exec", "-T", "minio", "chmod", "700", directory)
        for _ in range(2):
            result = CliRunner().invoke(minio_group, ["migrate-image", "--apply"])
            assert result.exit_code == 0, result.output
            assert (
                json.loads(compose("config", "--format", "json"))["volumes"]["minio-data"]["name"]
                == original_volume
            )
            assert compose("exec", "-T", "minio", "id", "-u").strip() == "1001"
            compose(
                "exec",
                "-T",
                "minio",
                "mc",
                "alias",
                "set",
                "local",
                "http://localhost:9000",
                "migration-user",
                "migration-password",
            )
            assert (
                compose("exec", "-T", "minio", "mc", "cat", "local/preexisting/probe")
                == "existing-object-before-upgrade"
            )
            compose(
                "exec",
                "-T",
                "minio",
                "mc",
                "pipe",
                "local/preexisting/new",
                input="new-object-after-upgrade",
            )
            assert (
                compose("exec", "-T", "minio", "mc", "cat", "local/preexisting/new")
                == "new-object-after-upgrade"
            )
    finally:
        # This test alone owns this disposable fixture volume.
        compose("down", "--volumes")
