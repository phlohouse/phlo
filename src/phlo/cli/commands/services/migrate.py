"""Move personal state aside while keeping shared lakehouse files in .phlo."""

from __future__ import annotations

import os
import re
from pathlib import Path

import click
import yaml

from phlo.cli.authorization_wrappers import require_mutation_authorization
from phlo.cli.commands.services.utils import ensure_compose_project
from phlo.plugins.compose.artifacts import (
    render_shared_gitignore,
    shared_artifact_files,
    split_host_compose,
)
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


def _regular_path(path: Path, project: Path) -> None:
    for parent in (path, *path.parents):
        if parent == project.parent:
            break
        if parent.is_symlink():
            raise click.ClickException(f"Symlink cannot be migrated: {path.relative_to(project)}")
    if path.exists() and not path.is_file():
        raise click.ClickException(f"Expected a file: {path.relative_to(project)}")


def _rewrite_compose(content: str, *, shared: bool = True) -> str:
    try:
        compose = yaml.safe_load(content)
    except yaml.YAMLError as exc:
        raise click.ClickException("Invalid Compose configuration") from exc
    if not isinstance(compose, dict) or not isinstance(compose.get("services"), dict):
        raise click.ClickException("Compose must contain a services mapping.")
    if shared:
        _check_literals(compose)
    for name, config in compose["services"].items():
        if not isinstance(config, dict):
            raise click.ClickException(f"Invalid Compose service: {name}")

        def rewrite(value):
            if isinstance(value, str):
                return {
                    ".env": "overrides/.env",
                    "./.env": "overrides/.env",
                    ".env.local": "secrets/.env",
                    "./.env.local": "secrets/.env",
                }.get(value, value)
            if isinstance(value, dict):
                return {
                    key: rewrite(item) if key == "path" else item for key, item in value.items()
                }
            return value

        if "env_file" in config:
            value = config["env_file"]
            config["env_file"] = (
                [rewrite(item) for item in value] if isinstance(value, list) else rewrite(value)
            )
    return "# Phlo shared layout v1\n" + yaml.safe_dump(compose, sort_keys=False)


@click.command("migrate")
@click.option("--dry-run", is_flag=True, help="Preview moves and Git exclusions without writing.")
@click.option(
    "--include",
    "includes",
    multiple=True,
    help="Additional shared Dockerfile/config path relative to .phlo (repeatable).",
)
@require_mutation_authorization("services.migrate", when=lambda kwargs: not kwargs.get("dry_run"))
def migrate_cmd(dry_run: bool, includes: tuple[str, ...]) -> None:
    """Move personal files aside and make existing .phlo configuration shareable.

    Shared files remain in .phlo. Secrets and overrides remain ignored. Review
    shared files for private values before committing. No Git staging is performed.
    """
    state = ensure_compose_project()
    project = state.parent
    base = state / "docker-compose.yml"
    _regular_path(base, project)
    from phlo.cli.infrastructure.container_backend import validate_development_compose_layers

    validate_development_compose_layers(state)
    rewritten = _rewrite_compose(base.read_text())
    compose, host_compose = split_host_compose(yaml.safe_load(rewritten))
    rewritten = "# Phlo shared layout v1\n" + yaml.safe_dump(compose, sort_keys=False)
    moves = []
    for source, target in [
        (state / ".env", state / "overrides/.env"),
        (state / ".env.local", state / "secrets/.env"),
        (state / "compose.local.yaml", state / "overrides/compose.yaml"),
        (project / "compose.phlo.yaml", state / "compose.shared.yaml"),
    ]:
        if source.exists() or source.is_symlink():
            moves.append((source, target))
    for os_name in ("windows", "linux", "macos"):
        source = project / f"compose.phlo.{os_name}.yaml"
        if source.exists() or source.is_symlink():
            moves.append((source, state / f"compose.{os_name}.yaml"))
    legacy = project / "phlo-runtime"
    if legacy.is_symlink():
        raise click.ClickException("Symlink cannot be migrated: phlo-runtime")
    if legacy.exists():
        for source in sorted(legacy.rglob("*")):
            if source.is_symlink():
                raise click.ClickException("Symlink cannot be migrated in phlo-runtime")
            if source.is_file():
                if source.name != ".gitattributes":
                    try:
                        shared_artifact_files(legacy, source.relative_to(legacy).as_posix())
                    except ValueError as exc:
                        raise click.ClickException(str(exc)) from exc
                moves.append((source, state / source.relative_to(legacy)))
    for source, target in moves:
        _regular_path(source, project)
        _regular_path(target, project)
        if target.exists() and target.read_bytes() != source.read_bytes():
            raise click.ClickException(
                f"Conflicting destination {target.relative_to(project)}; reconcile the files before migrating."
            )
    shared = {"docker-compose.yml", ".gitignore", ".gitattributes"}
    shared.update(
        target.relative_to(state).as_posix()
        for _, target in moves
        if target.parent not in {state / "overrides", state / "secrets"}
    )
    for candidate in state.glob("compose.*.yaml"):
        if candidate.name != "compose.local.yaml":
            _regular_path(candidate, project)
            shared.add(candidate.name)
    discovered = ServiceDiscovery().discover()
    paths = set(includes)
    for name in compose["services"]:
        service = discovered.get(name)
        if service:
            paths.update(spec["dest"] for spec in service.files or [])
    moved_targets = {target for _, target in moves}
    for relative in paths:
        if not (state / relative).exists() and any(
            target == state / relative or target.is_relative_to(state / relative)
            for target in moved_targets
        ):
            continue
        try:
            shared.update(
                path.relative_to(state).as_posix()
                for path in shared_artifact_files(state, relative)
            )
        except ValueError as exc:
            raise click.ClickException(str(exc)) from exc
    writes = {base: rewritten.encode()}
    for source, target in moves:
        if target.name.startswith("compose.") and target.suffix == ".yaml":
            writes[target] = _rewrite_compose(
                source.read_text(), shared=target.parent != state / "overrides"
            ).encode()
    root_ignore, inner_ignore = project / ".gitignore", state / ".gitignore"
    for path in (root_ignore, inner_ignore, state / ".gitattributes"):
        _regular_path(path, project)
    root_text = root_ignore.read_text() if root_ignore.exists() else ""
    lines = [
        line
        for line in root_text.splitlines()
        if line.strip() not in {".phlo", ".phlo/", "/.phlo", "/.phlo/"}
    ]
    if "!/.phlo/" not in lines:
        lines.append("!/.phlo/")
    writes[root_ignore] = ("\n".join(lines) + "\n").encode()
    old = inner_ignore.read_text() if inner_ignore.exists() else ""
    writes[inner_ignore] = render_shared_gitignore(sorted(shared), old).encode()
    attrs = state / ".gitattributes"
    if not attrs.exists() and attrs not in moved_targets:
        writes[attrs] = b"* text=auto eol=lf\n"
    if host_compose.get("services"):
        host_path = state / "overrides/compose.host.yaml"
        _regular_path(host_path, project)
        host_bytes = yaml.safe_dump(host_compose, sort_keys=False).encode()
        if host_path.exists() and host_path.read_bytes() != host_bytes:
            raise click.ClickException(
                "Conflicting overrides/compose.host.yaml; reconcile before migrating."
            )
        writes[host_path] = host_bytes
    for source, target in moves:
        click.echo(f"Move {source.relative_to(project)} -> {target.relative_to(project)}")
    for relative in sorted(shared):
        click.echo(f"Share .phlo/{relative}")
    for path in sorted(writes):
        if path.parent == state / "overrides":
            click.echo(f"Write {path.relative_to(project)}")
    click.echo(
        "Update .gitignore and .phlo/.gitignore; ignore secrets, overrides, and runtime state."
    )
    if dry_run:
        return
    # Keep a byte-for-byte rollback journal, including permissions. Never overwrite
    # conflicts; originals disappear only after the complete preflight succeeds.
    affected = set(writes) | {path for pair in moves for path in pair}
    originals = {
        path: (path.read_bytes(), path.stat().st_mode) if path.exists() else None
        for path in affected
    }
    created_dirs = []
    try:
        for path in affected:
            missing = []
            parent = path.parent
            while not parent.exists():
                missing.append(parent)
                parent = parent.parent
            for directory in reversed(missing):
                directory.mkdir()
                created_dirs.append(directory)
        for source, target in moves:
            if target.exists():
                source.unlink()
            else:
                source.rename(target)
        for path, content in writes.items():
            path.write_bytes(content)
    except OSError as exc:
        for path, original in originals.items():
            if original is None:
                path.unlink(missing_ok=True)
            elif not path.exists() or path.read_bytes() != original[0]:
                # Create restored files with their original mode before exposing
                # any bytes, including secrets that were moved earlier.
                descriptor = os.open(
                    path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, original[1] & 0o777
                )
                with os.fdopen(descriptor, "wb") as output:
                    output.write(original[0])
                path.chmod(original[1])
        for directory in reversed(created_dirs):
            directory.rmdir()
        raise click.ClickException("Migration failed; original files restored.") from exc
    if legacy.exists():
        for directory in sorted(
            (p for p in legacy.rglob("*") if p.is_dir()), key=lambda p: len(p.parts), reverse=True
        ):
            directory.rmdir()
        legacy.rmdir()
    click.echo("Migrated. Review and commit shared .phlo files and .gitignore.")
