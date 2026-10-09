"""Service definition data model and parsing helpers.

ServiceDefinition loads from service.yaml files, dictionaries, or inline
phlo.yaml config. A declared source_path resolves relative to the phlo package
tree; an undeclared one defaults to the definition's own directory.

Imported by service loading and discovery modules and by the CLI services
command utilities.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from functools import cache
from pathlib import Path
from typing import Any

from phlo.plugins._service_yaml import load_service_yaml
from phlo.plugins._service_yaml import refresh as refresh_yaml


def service_manifest_paths(root: Path) -> tuple[Path, ...]:
    """Scan a manifest root once; explicit discovery refresh finds new files."""
    return _scan_manifest_root(root.resolve())


@cache
def _scan_manifest_root(root: Path) -> tuple[Path, ...]:
    return tuple(sorted(root.rglob("*.yaml")))


def refresh() -> None:
    """Forget manifest scans and parsed YAML."""
    _scan_manifest_root.cache_clear()
    refresh_yaml()


@dataclass(slots=True)
class ServiceDefinition:
    """Represents a parsed service.yaml definition."""

    name: str
    description: str
    category: str = "core"
    version: str = "latest"
    default: bool = False
    profile: str | None = None
    depends_on: list[str] = field(default_factory=list)
    image: str | None = None
    build: dict[str, Any] | None = None
    compose: dict[str, Any] = field(default_factory=dict)
    env_vars: dict[str, dict[str, Any]] = field(default_factory=dict)
    files: list[dict[str, str]] = field(default_factory=list)
    gitignore: list[str] = field(default_factory=list)
    hooks: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    dev: dict[str, Any] = field(default_factory=dict)
    networks: dict[str, dict[str, Any]] = field(default_factory=dict)
    source_path: Path | None = None
    phlo_dev: bool = False
    core: bool = False
    file_validator: Callable[[str, str], None] | None = field(
        default=None, repr=False, compare=False
    )

    @classmethod
    def from_yaml(cls, path: Path) -> ServiceDefinition:
        """Load a service definition from a YAML file."""
        data = load_service_yaml(path)

        # A declared source_path is relative to the phlo package tree, not to
        # the YAML file itself; only an undeclared one defaults to sitting
        # beside the definition.
        if data.get("source_path"):
            phlo_root = Path(__file__).parent.parent.parent.parent
            source_path = phlo_root / data["source_path"]
        else:
            source_path = path.parent

        return cls(
            name=data["name"],
            description=data["description"],
            category=data.get("category", "core"),
            version=data.get("version", "latest"),
            default=data.get("default", False),
            profile=data.get("profile"),
            depends_on=data.get("depends_on", []),
            image=data.get("image"),
            build=data.get("build"),
            compose=data.get("compose", {}),
            env_vars=data.get("env_vars", {}),
            files=data.get("files", []),
            gitignore=data.get("gitignore", []),
            hooks=data.get("hooks", {}),
            dev=data.get("dev", {}),
            networks=data.get("networks") or {},
            source_path=source_path,
            phlo_dev=data.get("phlo_dev", False),
            core=data.get("core", False),
        )

    @classmethod
    def from_dict(cls, data: dict[str, Any], source_path: Path | None) -> ServiceDefinition:
        """Load a service definition from a dictionary."""
        if not isinstance(data, dict):
            raise ValueError("Service definition must be a mapping.")
        return cls(
            name=data["name"],
            description=data.get("description", ""),
            category=data.get("category", "core"),
            version=data.get("version", "latest"),
            default=data.get("default", False),
            profile=data.get("profile"),
            depends_on=data.get("depends_on", []),
            image=data.get("image"),
            build=data.get("build"),
            compose=data.get("compose", {}),
            env_vars=data.get("env_vars", {}),
            files=data.get("files", []),
            gitignore=data.get("gitignore", []),
            hooks=data.get("hooks", {}),
            dev=data.get("dev", {}),
            networks=data.get("networks") or {},
            source_path=source_path,
            phlo_dev=data.get("phlo_dev", False),
            core=data.get("core", False),
        )

    @classmethod
    def from_inline(cls, name: str, config: dict[str, Any]) -> ServiceDefinition:
        """Create a ServiceDefinition from inline config in phlo.yaml."""
        compose_keys = (
            "user",
            "container_name",
            "labels",
            "environment",
            "ports",
            "volumes",
            "command",
            "entrypoint",
            "healthcheck",
            "restart",
            "mem_limit",
            "mem_reservation",
            "cpus",
            "shm_size",
        )
        compose = {key: config[key] for key in compose_keys if key in config}

        return cls(
            name=name,
            description=config.get("description", f"Custom service: {name}"),
            category="custom",
            default=True,
            depends_on=config.get("depends_on", []),
            image=config.get("image"),
            build=config.get("build"),
            compose=compose,
        )
