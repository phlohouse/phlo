"""Export an existing development stack into reviewable, shared project files."""

from __future__ import annotations

import re
import shutil
from pathlib import Path

import click
import yaml

from phlo.cli.authorization_wrappers import require_mutation_authorization
from phlo.cli.commands.services.utils import ensure_compose_project
from phlo.plugins.compose.artifacts import shared_artifact_files
from phlo.plugins.discovery import ServiceDiscovery

_SHARED_FILES = "phlo-runtime"


def _check_literals(value: object) -> None:
    """Catch common inline credentials without printing their values."""
    if isinstance(value, dict):
        for key, item in value.items():
            if (
                re.search(r"password|secret|token|credential|private.key", str(key), re.I)
                and isinstance(item, (str, int))
                and str(item)
                and not str(item).startswith("${")
            ):
                raise click.ClickException(
                    f"Inline credential at {key}; replace it with an environment reference first."
                )
            _check_literals(item)
    elif isinstance(value, list):
        for item in value:
            if isinstance(item, str) and "=" in item:
                key, content = item.split("=", 1)
                _check_literals({key: content})
            else:
                _check_literals(item)


@click.command("migrate")
@click.option("--dry-run", is_flag=True, help="List the export without writing files.")
@click.option(
    "--include",
    "includes",
    multiple=True,
    help="Additional Dockerfile/config path relative to .phlo (repeatable).",
)
@require_mutation_authorization("services.migrate", when=lambda kwargs: not kwargs.get("dry_run"))
def migrate_cmd(dry_run: bool, includes: tuple[str, ...]) -> None:
    """Share existing Compose, Dockerfiles, entrypoints, and service configs.

    Copies Compose into compose.phlo.yaml and declared service artifacts into
    phlo-runtime/. Originals are retained. No Git operations are performed.
    Review exported files before committing, especially handwritten configs.
    """
    state = ensure_compose_project()
    project = state.parent
    destination = project / "compose.phlo.yaml"
    assets = project / _SHARED_FILES
    if destination.exists() or destination.is_symlink() or assets.exists() or assets.is_symlink():
        raise click.ClickException(
            "compose.phlo.yaml or phlo-runtime already exists; refusing to overwrite."
        )
    content = (state / "docker-compose.yml").read_text()
    if "# Dev mode: true" in content:
        raise click.ClickException(
            "Dev source mounts are machine-specific. Back up edits, then regenerate with --no-dev first."
        )
    try:
        compose = yaml.safe_load(content)
    except yaml.YAMLError as exc:
        raise click.ClickException("Invalid .phlo/docker-compose.yml") from exc
    if not isinstance(compose, dict) or not isinstance(compose.get("services"), dict):
        raise click.ClickException("Compose must contain a services mapping.")
    _check_literals(compose)
    # Reuse the development-only guard without executing Docker or Podman.
    # Migration itself must not turn a production base into unchecked layers.
    from phlo.cli.infrastructure.container_backend import validate_development_compose_layers

    validate_development_compose_layers(state)
    discovered = ServiceDiscovery().discover()
    paths = set(includes)
    for name, config in compose["services"].items():
        if not isinstance(config, dict):
            raise click.ClickException(f"Invalid Compose service: {name}")
        # Host ownership is regenerated on each teammate's machine. Do not freeze
        # the exporting Linux user's numeric IDs into a shared override.
        if name in {"dagster", "dagster-daemon"}:
            environment = config.get("environment", {})
            if isinstance(environment, dict):
                environment.pop("PHLO_RUNTIME_UID", None)
                environment.pop("PHLO_RUNTIME_GID", None)
            elif isinstance(environment, list):
                config["environment"] = [
                    item
                    for item in environment
                    if not isinstance(item, str)
                    or item.split("=", 1)[0] not in {"PHLO_RUNTIME_UID", "PHLO_RUNTIME_GID"}
                ]
        if name == "phlo-api" and re.fullmatch(r"\d+:\d+", str(config.get("user", ""))):
            config.pop("user")
        service = discovered.get(name)
        if service:
            paths.update(spec["dest"] for spec in service.files)
        else:
            click.echo(f"Custom service {name}: use --include for its Dockerfiles/configs.")
    try:
        files = sorted(
            {path for relative in paths for path in shared_artifact_files(state, relative)}
        )
    except ValueError as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(".phlo/docker-compose.yml -> compose.phlo.yaml")
    for path in files:
        click.echo(
            f".phlo/{path.relative_to(state).as_posix()} -> {_SHARED_FILES}/{path.relative_to(state).as_posix()}"
        )
    click.echo("Secrets, runtime data, and personal overrides stay in .phlo/.")
    click.echo(
        "Review the snapshot for private values and machine-specific paths before committing."
    )
    if dry_run:
        return
    # Stage first: validation or copy failures leave the shared destinations absent.
    import tempfile

    with tempfile.TemporaryDirectory(prefix=".phlo-migrate-", dir=project) as staging:
        staged = Path(staging)
        staged_assets = staged / _SHARED_FILES
        staged_assets.mkdir()
        for path in files:
            target = staged_assets / path.relative_to(state)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target)
        (staged_assets / ".gitattributes").write_text("* text=auto eol=lf\n")
        snapshot = "# Shared Compose snapshot. Review changes when upgrading service packages.\n"
        snapshot += yaml.safe_dump(compose, sort_keys=False)
        # Exclusive creation protects an existing override even if it appeared
        # after the initial preflight. Never remove or edit the source files.
        with destination.open("x") as output:
            output.write(snapshot)
        try:
            staged_assets.rename(assets)
        except OSError:
            destination.unlink()
            raise
    click.echo(
        "Exported. Commit compose.phlo.yaml and phlo-runtime/. Run services init --force after shared artifact edits."
    )
