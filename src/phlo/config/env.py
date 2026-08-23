"""Project environment helpers.

Resolves the project root (explicit argument, per-context override, or
cwd; traversal is rejected) and layers configuration from phlo.yaml
env:, .phlo/.env, and .phlo/.env.local, with later sources winning.
The ContextVar-based use_project_root keeps nested settings
construction correct under async without touching process cwd.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path

import yaml

# Per-context project root, consulted by resolve_project_root when callers
# pass no explicit root. A ContextVar keeps nested settings construction
# correct under async without touching process-wide cwd state.
_PROJECT_ROOT: ContextVar[Path | None] = ContextVar("phlo_project_root", default=None)


def _normalise_project_root(project_root: Path | str) -> Path:
    """Resolve a project root while rejecting traversal components."""
    raw_path = Path(project_root).expanduser()
    if ".." in raw_path.parts:
        raise ValueError(f"project root contains path traversal: {project_root}")
    return raw_path.resolve()


def resolve_project_root(project_root: Path | str | None = None) -> Path:
    """Resolve an explicit project root for configuration loading."""
    if project_root is not None:
        return _normalise_project_root(project_root)

    active_root = _PROJECT_ROOT.get()
    if active_root is not None:
        return active_root

    configured_root = os.environ.get("PHLO_PROJECT_PATH")
    if configured_root:
        return _normalise_project_root(configured_root)
    return Path.cwd().resolve()


def project_env_files(project_root: Path | str | None = None) -> tuple[Path, Path]:
    """Return the generated environment files for a project root."""
    root = resolve_project_root(project_root)
    return root / ".phlo" / ".env", root / ".phlo" / ".env.local"


@contextmanager
def use_project_root(project_root: Path | str | None) -> Iterator[Path]:
    """Make a project root explicit while constructing nested settings."""
    root = resolve_project_root(project_root)
    token = _PROJECT_ROOT.set(root)
    try:
        yield root
    finally:
        _PROJECT_ROOT.reset(token)


def parse_project_env_file(path: Path) -> dict[str, str]:
    """Parse a simple dotenv file into key/value pairs."""
    values: dict[str, str] = {}
    if not path.exists():
        return values

    for raw_line in path.read_text().splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        if key:
            values[key] = value
    return values


def parse_project_config_env(path: Path) -> dict[str, str]:
    """Parse top-level ``env:`` values from ``phlo.yaml``."""
    if not path.exists():
        return {}

    try:
        config = yaml.safe_load(path.read_text()) or {}
    except (OSError, yaml.YAMLError):
        # An unreadable or malformed phlo.yaml counts as "no env block"
        # rather than a load failure.
        return {}

    if not isinstance(config, dict):
        return {}

    values = config.get("env", {})
    if not isinstance(values, dict):
        return {}

    return {str(key): str(value) for key, value in values.items() if isinstance(key, str)}


def load_project_env(
    project_root: Path | str | None = None, *, include_os: bool = True
) -> dict[str, str]:
    """Load ``phlo.yaml env:``, `.phlo/.env`, and `.phlo/.env.local`.

    Later sources override earlier sources, with OS env taking final precedence
    when requested.
    """
    root = resolve_project_root(project_root)
    env: dict[str, str] = parse_project_config_env(root / "phlo.yaml")
    for path in (root / ".phlo" / ".env", root / ".phlo" / ".env.local"):
        env.update(parse_project_env_file(path))
    if include_os:
        env.update(os.environ)
    return env


def project_env_value(
    name: str, default: str | None = None, project_root: Path | str | None = None
) -> str | None:
    """Read a value from project env files or OS environment."""
    return load_project_env(project_root).get(name, default)
