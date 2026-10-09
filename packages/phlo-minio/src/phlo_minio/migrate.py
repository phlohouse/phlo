"""Opt-in image migration that keeps an existing lakehouse's storage layout."""

from __future__ import annotations

import json
from pathlib import Path
from subprocess import TimeoutExpired
from typing import Any

import click
import yaml

from phlo.cli.authorization_wrappers import enforce_surface_mutation_authorization
from phlo.cli.commands.services.utils import ensure_compose_project, require_container_backend
from phlo.cli.infrastructure.command import CommandError, run_command
from phlo.cli.infrastructure.compose import compose_base_cmd
from phlo.cli.infrastructure.utils import get_project_name
from phlo_minio.authorization import get_minio_cli_adapter


def _storage(config: dict[str, Any]) -> tuple[str, str]:
    command = config.get("command", [])
    if not isinstance(command, list) or len(command) < 2 or command[0] != "server":
        raise click.ClickException("Expected a single-directory MinIO server command.")
    if command[2:] not in ([], ["--console-address", ":9001"]):
        raise click.ClickException(
            "Custom or distributed MinIO commands require a manual migration."
        )
    directory = command[1]
    if directory not in {"/data", "/bitnami/minio/data"}:
        raise click.ClickException(
            "Unsupported MinIO data directory; migrate this deployment manually."
        )
    for mount in config.get("volumes", []):
        if isinstance(mount, str):
            parts = mount.split(":")
            if len(parts) >= 2 and parts[1] == directory and parts[2:] in ([], ["rw"]):
                return parts[0], directory
        elif isinstance(mount, dict) and mount.get("target") == directory:
            if mount.get("type") == "volume" and not mount.get("read_only") and mount.get("source"):
                return mount["source"], directory
    raise click.ClickException("Expected a writable named MinIO data volume.")


def _prepare(document: dict[str, Any], effective: dict[str, Any], image: str) -> str:
    services = document.get("services", {})
    if not all(name in services for name in ("minio", "minio-setup")):
        raise click.ClickException("Both minio and minio-setup must be configured.")
    volume, directory = _storage(services["minio"])
    if volume not in document.get("volumes", {}):
        raise click.ClickException(
            "MinIO storage must be a declared named volume, not a bind mount."
        )
    storage = effective["volumes"][volume]
    if storage.get("driver", "local") != "local" or storage.get("driver_opts"):
        raise click.ClickException("Custom volume drivers require a manual migration.")
    if services["minio"].get("entrypoint") not in (None, "minio", ["minio"]):
        raise click.ClickException("Custom MinIO entrypoints require a manual migration.")
    effective_volume, effective_directory = _storage(effective["services"]["minio"])
    if (effective_volume, effective_directory) != (volume, directory):
        raise click.ClickException("A Compose layer changes MinIO storage; reconcile it first.")
    for name in ("minio", "minio-setup"):
        for field in ("image", "user"):
            if effective["services"][name].get(field) != services[name].get(field):
                raise click.ClickException(
                    f"A Compose layer changes {name}.{field}; reconcile it first."
                )
        services[name]["image"] = image
        services[name].pop("build", None)
    services["minio"]["user"] = "1001:0"
    return directory


def _check_layers(base: list[str], path: Path) -> None:
    for index, argument in enumerate(base[:-1]):
        if argument != "-f" or Path(base[index + 1]) == path:
            continue
        layer = yaml.safe_load(Path(base[index + 1]).read_text()) or {}
        for name in ("minio", "minio-setup"):
            service = layer.get("services", {}).get(name, {})
            if set(service) & {"image", "build", "user", "command", "entrypoint", "volumes"}:
                raise click.ClickException(
                    f"Reconcile the {name} override in {base[index + 1]} first."
                )


@click.command(name="migrate-image")
@click.option("--apply", is_flag=True, help="Pull, stop MinIO, repair ownership, and restart it.")
def migrate_image(apply: bool) -> None:
    """Preview or apply the published Phlo image to existing MinIO services.

    Back up object storage first and pause lakehouse writers before --apply.
    This command never removes a volume or changes credentials or data paths.
    """
    phlo_dir = ensure_compose_project()
    base = compose_base_cmd(phlo_dir=phlo_dir, project_name=get_project_name())
    if base[:2] != ["docker", "compose"]:
        raise click.ClickException(
            "Automatic image migration requires Docker Compose v2; "
            "migrate other container backends manually."
        )
    path = phlo_dir / "docker-compose.yml"
    original = path.read_text(encoding="utf-8")
    document = yaml.safe_load(original)
    image = yaml.safe_load(Path(__file__).with_name("service.yaml").read_text())["image"]
    try:
        _check_layers(base, path)
        effective = json.loads(run_command([*base, "config", "--format", "json"]).stdout)
        directory = _prepare(document, effective, image)
        volume, _ = _storage(effective["services"]["minio"])
        volume_name = effective["volumes"][volume]["name"]
        click.echo(
            f"Use {image} for minio and minio-setup; retain volume {volume_name} at {directory}."
        )
        if not apply:
            click.echo("Preview only. Back up storage, pause writers, then rerun with --apply.")
            return
        require_container_backend()
        enforce_surface_mutation_authorization("minio", get_minio_cli_adapter)
        # Refuse to create an empty volume and mistake it for existing storage.
        run_command([base[0], "volume", "inspect", volume_name])
        backup = path.with_name("docker-compose.pre-minio-image.yml")
        if not backup.exists():
            backup.touch(mode=0o600, exist_ok=False)
            backup.write_text(original, encoding="utf-8")
        path.write_text(yaml.safe_dump(document, sort_keys=False), encoding="utf-8")
        try:
            run_command([*base, "pull", "minio", "minio-setup"], timeout_seconds=600)
        except (CommandError, TimeoutExpired):
            path.write_text(original, encoding="utf-8")
            raise
        run_command([*base, "stop", "minio-setup", "minio"], timeout_seconds=120)
        run_command(
            [
                *base,
                "run",
                "--rm",
                "--no-deps",
                "--user",
                "0:0",
                "--entrypoint",
                "/bin/sh",
                "minio",
                "-ec",
                f"chown -R 1001:0 {directory}",
            ],
            timeout_seconds=600,
        )
        run_command(
            [
                *base,
                "up",
                "-d",
                "--no-build",
                "--force-recreate",
                "--wait",
                "--wait-timeout",
                "120",
                "minio",
            ],
            timeout_seconds=180,
        )
        run_command([*base, "run", "--rm", "--no-deps", "minio-setup"], timeout_seconds=180)
    except (CommandError, TimeoutExpired) as exc:
        raise click.ClickException(
            f"MinIO image migration failed: {exc}. Inspect container logs before resuming writers. "
            "Storage was not removed; the original Compose backup is "
            f"{path.with_name('docker-compose.pre-minio-image.yml')}."
        ) from exc
    click.echo(
        "MinIO is ready and bucket setup succeeded. Verify existing objects before resuming writers."
    )
