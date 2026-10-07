"""Prepare provider files and project overrides before writing generated outputs.

Only declared files, including bundled leaves of declared directories, are targets.
Validation never expands environment references. Writes replace individual files atomically.
"""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from tempfile import NamedTemporaryFile
from typing import Any

import yaml
from pydantic import TypeAdapter, ValidationError

from phlo.config_schema import ServiceFileOverride
from phlo.logging import get_logger
from phlo.plugins.discovery._service_definition import ServiceDefinition

logger = get_logger(__name__)
_OVERRIDES = TypeAdapter(dict[str, ServiceFileOverride])


def _relative_path(value: str) -> Path:
    path = PurePosixPath(value)
    if not value or path.is_absolute() or ".." in path.parts or "\\" in value or ":" in value:
        raise ValueError(f"Invalid generated file destination: {value!r}")
    if path.as_posix() != value or value == ".":
        raise ValueError(f"Use a normalized relative file destination: {value!r}")
    return Path(value)


def _check_output_path(output_dir: Path, relative: Path) -> None:
    destination = output_dir / relative
    if not destination.resolve().is_relative_to(output_dir.resolve()):
        raise ValueError(f"Generated destination escapes output directory: {relative}")
    if any(path.is_symlink() for path in (destination, *destination.parents)):
        raise ValueError(f"Generated destination cannot use symlinks: {relative}")


def _merge(default: Any, overlay: Any, path: str = "root") -> Any:
    if isinstance(overlay, dict) and "$replace" in overlay:
        if len(overlay) != 1:
            raise ValueError(f"{path}: $replace must be the only key in its mapping")
        return overlay["$replace"]
    if (
        isinstance(default, dict)
        and "module" in default
        and "class" in default
        and (
            not isinstance(overlay, dict)
            or any(key in overlay and overlay[key] != default[key] for key in ("module", "class"))
        )
    ):
        raise ValueError(f"{path}: changing a provider class requires $replace or mode: replace")
    if isinstance(overlay, dict):
        result = dict(default) if isinstance(default, dict) else {}
        for key, value in overlay.items():
            result[key] = _merge(result.get(key), value, f"{path}.{key}")
        return result
    return overlay


def _parse(content: str, destination: str) -> Any:
    try:
        return json.loads(content) if destination.endswith(".json") else yaml.safe_load(content)
    except (ValueError, yaml.YAMLError) as exc:
        # Parser exceptions can include entire lines containing credentials.
        raise ValueError(f"{destination}: invalid YAML/JSON syntax") from exc


def _render_override(
    source: Path, destination: str, override: ServiceFileOverride, root: Path
) -> bytes:
    project_source = root / _relative_path(override.source)
    if not project_source.resolve().is_relative_to(root.resolve()) or not project_source.is_file():
        raise ValueError(f"{destination}: source must be an existing file within the project root")
    try:
        content = project_source.read_bytes().decode("utf-8")
    except UnicodeError as exc:
        raise ValueError(f"{destination}: source must be UTF-8 text") from exc
    structured = destination.endswith((".yaml", ".yml", ".json"))
    if override.mode == "merge":
        if not structured:
            raise ValueError(f"{destination}: native text files require mode: replace")
        overlay = _parse(content, destination)
        if not isinstance(overlay, dict):
            raise ValueError(f"{destination}: merge overlay must be a mapping")
        effective = _merge(_parse(source.read_text(encoding="utf-8"), destination), overlay)
        content = (
            json.dumps(effective, indent=2) + "\n"
            if destination.endswith(".json")
            else yaml.safe_dump(effective, sort_keys=False)
        )
    elif structured:
        _parse(content, destination)
    if not content.strip() or "\x00" in content:
        raise ValueError(f"{destination}: replacement must be nonempty text without NUL bytes")
    return content.encode("utf-8")


@dataclass
class ServiceFilePlan:
    """Write a prevalidated snapshot of service files into the generated directory."""

    output_dir: Path
    files: dict[str, tuple[Path, bytes | None]] = field(default_factory=dict)
    directories: list[str] = field(default_factory=list)
    overridden: set[str] = field(default_factory=set)

    def write(self, *, overwrite: bool = True) -> list[str]:
        """Replace generated files after all overrides have passed validation.

        Preserve existing defaults when overwrite is false; explicit project overrides
        still regenerate. Validation failure writes nothing; I/O failure is not a
        multi-file transaction.
        """
        for relative in [*self.directories, *self.files]:
            _check_output_path(self.output_dir, Path(relative))
        preserved_directories = [
            Path(relative)
            for relative in self.directories
            if not overwrite and (self.output_dir / relative).exists()
        ]
        for relative in self.directories:
            dest = self.output_dir / relative
            if overwrite and dest.exists():
                shutil.rmtree(dest)
            dest.mkdir(parents=True, exist_ok=True)
        copied = []
        for relative, (source, content) in self.files.items():
            dest = self.output_dir / relative
            preserved = dest.exists() or any(
                Path(relative).is_relative_to(directory) for directory in preserved_directories
            )
            if preserved and not overwrite and relative not in self.overridden:
                continue
            dest.parent.mkdir(parents=True, exist_ok=True)
            with NamedTemporaryFile(dir=dest.parent, delete=False) as handle:
                temporary = Path(handle.name)
            try:
                if content is None:
                    shutil.copy2(source, temporary)
                else:
                    temporary.write_bytes(content)
                    temporary.chmod(source.stat().st_mode & 0o777)
                temporary.replace(dest)
            finally:
                temporary.unlink(missing_ok=True)
            copied.append(relative)
        return copied


def _declared_files(service: ServiceDefinition, plan: ServiceFilePlan) -> dict[str, Path]:
    files: dict[str, Path] = {}
    if service.source_path is None:
        return files
    for spec in service.files or []:
        relative = _relative_path(spec["dest"])
        _check_output_path(plan.output_dir, relative)
        source = service.source_path / spec["source"]
        if not source.exists():
            logger.warning(
                "compose_service_file_source_missing",
                service_name=service.name,
                source=str(source),
                destination=str(relative),
            )
            continue
        if source.is_dir():
            plan.directories.append(relative.as_posix())
            leaves = sorted(path for path in source.rglob("*") if path.is_file())
            files.update(
                {(relative / leaf.relative_to(source)).as_posix(): leaf for leaf in leaves}
            )
        else:
            files[relative.as_posix()] = source
    for relative, source in files.items():
        _check_output_path(plan.output_dir, Path(relative))
        if not source.resolve().is_relative_to(service.source_path.resolve()):
            raise ValueError(
                f"{service.name}/{relative}: provider source escapes package directory"
            )
    return files


def prepare_service_files(
    services: list[ServiceDefinition],
    output_dir: Path,
    *,
    user_overrides: dict[str, Any] | None = None,
    project_root: Path | None = None,
) -> ServiceFilePlan:
    """Validate every affected file before returning a write plan.

    Provider defaults precede project files. Providers optionally validate the
    resulting native config; core validates format and destination ownership.
    """
    plan = ServiceFilePlan(output_dir)
    root = project_root or output_dir.parent
    for service in services:
        files = _declared_files(service, plan)
        raw = (user_overrides or {}).get(service.name, {}).get("files", {})
        try:
            overrides = _OVERRIDES.validate_python(raw)
        except ValidationError as exc:
            locations = ", ".join(".".join(map(str, error["loc"])) for error in exc.errors())
            raise ValueError(f"{service.name}.files: invalid override at {locations}") from exc
        for destination, override in overrides.items():
            _relative_path(destination)
            if destination not in files:
                raise ValueError(f"{service.name}.files.{destination}: not a declared bundled file")
            content = _render_override(files[destination], destination, override, root)
            if service.file_validator is not None:
                service.file_validator(destination, content.decode("utf-8"))
            plan.files[destination] = (files[destination], content)
            plan.overridden.add(destination)
        for destination, source in files.items():
            if destination not in overrides:
                plan.files[destination] = (source, None)
    return plan
