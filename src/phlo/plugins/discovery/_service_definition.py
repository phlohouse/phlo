"""Service definition data model and parsing helpers.

ServiceDefinition loads from service.yaml files, dictionaries, or inline
phlo.yaml config. A declared source_path resolves relative to the phlo package
tree; an undeclared one defaults to the definition's own directory.

Imported by service loading and discovery modules and by the CLI services
command utilities.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from functools import cache
from pathlib import Path
from typing import Any, Literal

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


@dataclass(frozen=True, slots=True)
class ServicePort:
    """A published Compose port, before deployment environment overrides."""

    container_port: int
    host_port: int | None = None
    env_var: str | None = None
    host_ip: str | None = None
    protocol: Literal["tcp", "udp"] = "tcp"

    @classmethod
    def parse(cls, value: str | int | dict[str, Any]) -> ServicePort:
        """Parse Compose short or long syntax without reading process environment.

        Port ranges are retained by Compose but are not single-port topology
        declarations. Providers must declare each published port separately.
        """
        if isinstance(value, dict):
            target = str(value["target"])
            published = str(value["published"]) if "published" in value else None
            host_ip = value.get("host_ip")
            protocol = value.get("protocol", "tcp")
        else:
            normalized = str(value).strip().strip("\"'")
            mapping, _, protocol = normalized.partition("/")
            protocol = protocol or "tcp"
            # Colons inside ${VAR:-default} and bracketed IPv6 are not separators.
            parts = re.split(r":(?![^${}]*})(?![^\[\]]*\])", mapping)
            target = parts[-1]
            published = parts[-2] if len(parts) > 1 else None
            host_ip = ":".join(parts[:-2]) or None
        if protocol != "tcp" and protocol != "udp":
            raise ValueError("Service port protocol must be tcp or udp")
        env_var = None
        if published is not None and published.startswith("${"):
            match = re.fullmatch(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-([0-9]+))?\}", published)
            if match is None:
                raise ValueError("Service port must use ${VAR} or ${VAR:-port}")
            env_var, published = match.groups()
        container_port = int(target)
        host_port = int(published) if published is not None else None
        if not 1 <= container_port <= 65535 or (
            host_port is not None and not 0 <= host_port <= 65535
        ):
            raise ValueError("Container ports must be 1..65535 and host ports 0..65535")
        return cls(
            container_port, host_port, env_var, host_ip, "udp" if protocol == "udp" else "tcp"
        )


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
    source_path: Path | None = None
    phlo_dev: bool = False
    core: bool = False
    file_validator: Callable[[str, str], None] | None = field(
        default=None, repr=False, compare=False
    )

    def __post_init__(self) -> None:
        """Reject conflicting topology declarations at the shared parsing boundary."""
        if not isinstance(self.name, str) or not re.fullmatch(
            r"[a-zA-Z0-9][a-zA-Z0-9_.-]*", self.name
        ):
            raise ValueError("Service identity must be a non-empty Compose service name")
        if not isinstance(self.depends_on, list) or any(
            not isinstance(name, str) or not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_.-]*", name)
            for name in self.depends_on
        ):
            raise ValueError(f"{self.name}: depends_on must be a list of service identities")
        if "depends_on" in self.compose:
            raise ValueError(f"{self.name}: declare dependencies only in top-level depends_on")

    @property
    def ports(self) -> tuple[ServicePort, ...]:
        """Typed published ports from the package-owned Compose declaration."""
        return tuple(ServicePort.parse(value) for value in self.compose.get("ports", []))

    def validate_port_defaults(self) -> None:
        """Require published environment defaults to agree with the Compose projection."""
        for port in self.ports:
            if port.env_var is None:
                continue
            metadata = self.env_vars.get(port.env_var)
            if metadata is None or str(metadata.get("default")) != str(port.host_port):
                raise ValueError(
                    f"{self.name}: {port.env_var} default disagrees with compose.ports"
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
