"""Diagnostics for ambiguous Dagster asset definition failures.

Duplicate asset keys become Phlo discovery errors that name each spec's
provider/module/file origin; Definitions.merge failures are re-inspected so the
same guidance replaces Dagster's opaque duplicate-key error.

Framework-internal phlo_dagster helper: used by definitions, discovery, and the Dagster adapter.
Reports duplicate assets as phlo.capabilities.specs-aware origins instead of opaque Dagster errors.
"""

from __future__ import annotations

import inspect
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

import dagster as dg

from phlo.capabilities.specs import AssetSpec
from phlo.exceptions import PhloDiscoveryError


_PROVIDER_METADATA_KEYS = ("provider", "phlo/provider", "provider_name", "source")
_MODULE_METADATA_KEYS = ("module", "phlo/module", "source_module")
_FILE_METADATA_KEYS = ("file", "phlo/file", "source_file", "path", "source_path")


@dataclass(frozen=True)
class AssetOrigin:
    """Best-effort source details for one asset provider."""

    provider: str | None = None
    module: str | None = None
    file: str | None = None
    object_name: str | None = None

    def describe(self) -> str:
        """Render populated origin fields as a comma-separated key=value string."""
        parts: list[str] = []
        if self.provider:
            parts.append(f"provider={self.provider}")
        if self.module:
            parts.append(f"module={self.module}")
        if self.file:
            parts.append(f"file={self.file}")
        if self.object_name:
            parts.append(f"object={self.object_name}")
        return ", ".join(parts) if parts else "origin unavailable"


def raise_duplicate_asset_specs_if_present(asset_specs: Iterable[AssetSpec]) -> None:
    """Raise a clear discovery error when capability specs repeat an asset key."""
    by_key: dict[str, list[AssetSpec]] = defaultdict(list)
    for spec in asset_specs:
        by_key[spec.key].append(spec)

    for key, duplicates in by_key.items():
        if len(duplicates) < 2:
            continue
        origins = [_origin_from_asset_spec(spec).describe() for spec in duplicates]
        raise _duplicate_asset_error(key=key, origins=origins)


def merge_definitions_with_duplicate_diagnostics(*definitions: dg.Definitions) -> dg.Definitions:
    """Merge Dagster definitions and replace duplicate-key errors with Phlo guidance."""
    duplicate = _find_duplicate_definition_asset(definitions)
    if duplicate is not None:
        key, origins = duplicate
        raise _duplicate_asset_error(key=key, origins=origins)

    try:
        return dg.Definitions.merge(*definitions)
    except Exception as exc:
        duplicate = _find_duplicate_definition_asset(definitions)
        if duplicate is None:
            raise
        key, origins = duplicate
        raise _duplicate_asset_error(key=key, origins=origins, cause=exc) from exc


def _duplicate_asset_error(
    *,
    key: str,
    origins: list[str],
    cause: Exception | None = None,
) -> PhloDiscoveryError:
    origin_lines = "\n".join(f"  - {origin}" for origin in origins)
    message = (
        f"Duplicate Dagster asset key discovered: {key}\n\n"
        f"Asset providers:\n{origin_lines}\n\n"
        "Likely cause: the same asset is being supplied by auto-discovery and by "
        "explicit Dagster Definitions or imported module-level assets."
    )
    return PhloDiscoveryError(
        message=message,
        suggestions=[
            "Rely on Phlo auto-discovery for this asset, or keep it only in explicit Dagster Definitions.",
            "Do not include the same asset through both imported workflow modules and Definitions.merge.",
        ],
        cause=cause,
    )


def _find_duplicate_definition_asset(
    definitions: Iterable[dg.Definitions],
) -> tuple[str, list[str]] | None:
    origins_by_key: dict[str, list[str]] = defaultdict(list)
    for definition in definitions:
        for asset_def in list(definition.assets or []):
            # Dagster stores AssetChecksDefinition instances alongside asset
            # definitions when they are passed through the module collector.
            # They subclass AssetsDefinition but have no asset keys, so reading
            # their singular ``key`` property raises for their check set.
            if isinstance(asset_def, dg.AssetChecksDefinition):
                continue
            if not isinstance(asset_def, dg.AssetsDefinition):
                continue
            for key in _asset_definition_keys(asset_def):
                origins_by_key[key].append(_origin_from_asset_definition(asset_def).describe())

    for key, origins in origins_by_key.items():
        if len(origins) > 1:
            return key, origins
    return None


def _asset_definition_keys(asset_def: Any) -> list[str]:
    keys = getattr(asset_def, "keys", None)
    if keys:
        return [_format_asset_key(key) for key in keys]
    key = getattr(asset_def, "key", None)
    return [_format_asset_key(key)] if key is not None else []


def _format_asset_key(key: Any) -> str:
    path = getattr(key, "path", None)
    if path:
        return ".".join(str(part) for part in path)
    to_user_string = getattr(key, "to_user_string", None)
    if callable(to_user_string):
        return str(to_user_string())
    return str(key)


def _origin_from_asset_spec(spec: AssetSpec) -> AssetOrigin:
    metadata = dict(spec.metadata or {})
    run_fn = spec.run.fn if spec.run else None
    inspected = _origin_from_object(run_fn)
    return AssetOrigin(
        provider=_first_metadata_value(metadata, _PROVIDER_METADATA_KEYS),
        module=_first_metadata_value(metadata, _MODULE_METADATA_KEYS) or inspected.module,
        file=_first_metadata_value(metadata, _FILE_METADATA_KEYS) or inspected.file,
        object_name=inspected.object_name,
    )


def _origin_from_asset_definition(asset_def: Any) -> AssetOrigin:
    node_def = getattr(asset_def, "node_def", None)
    compute_fn = getattr(node_def, "compute_fn", None)
    decorated_fn = getattr(compute_fn, "decorated_fn", None)
    return _origin_from_object(decorated_fn or compute_fn or asset_def)


def _origin_from_object(value: Any) -> AssetOrigin:
    if value is None:
        return AssetOrigin()
    module = getattr(value, "__module__", None)
    object_name = getattr(value, "__qualname__", None) or getattr(value, "__name__", None)
    if object_name:
        object_name = str(object_name).split(".")[-1]
    try:
        file = inspect.getsourcefile(value) or inspect.getfile(value)
    except (TypeError, OSError):
        file = None
    return AssetOrigin(
        module=str(module) if module else None,
        file=_relative_file(file),
        object_name=object_name,
    )


def _first_metadata_value(metadata: dict[str, Any], keys: tuple[str, ...]) -> str | None:
    for key in keys:
        value = metadata.get(key)
        if value is not None:
            return str(value)
    return None


def _relative_file(file: str | None) -> str | None:
    if not file:
        return None
    path = Path(file)
    try:
        return str(path.resolve().relative_to(Path.cwd().resolve()))
    except ValueError:
        return str(path)
