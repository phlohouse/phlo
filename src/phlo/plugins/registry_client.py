"""Explicit registry refresh commands and network-free snapshot queries."""

from __future__ import annotations

import json
from dataclasses import dataclass
from importlib import metadata as importlib_metadata
from importlib import resources
from pathlib import Path
from time import time as _time
from typing import Any

import httpx

from phlo.config import get_settings
from phlo.logging import get_logger
from phlo.plugins.registry_models import RegistryDocument, RegistryPayloadError, parse_registry

logger = get_logger(__name__)


class RegistryUnavailable(RuntimeError):
    """The remote registry could not be refreshed; the last snapshot is unchanged."""


@dataclass(frozen=True)
class RegistryPlugin:
    """Normalized plugin entry from the registry."""

    name: str
    type: str
    package: str
    version: str
    description: str
    author: str
    homepage: str | None
    tags: list[str]
    verified: bool
    core: bool


@dataclass
class _RegistryCache:
    loaded_at: float = 0.0
    data: RegistryDocument | None = None


_REGISTRY_CACHE = _RegistryCache()


def _plugin_version(package: str) -> str:
    try:
        return importlib_metadata.version(package)
    except importlib_metadata.PackageNotFoundError:
        return ""


def _normalize_registry(registry: RegistryDocument) -> list[RegistryPlugin]:
    return [
        RegistryPlugin(
            name=name,
            type=info.type,
            package=info.package,
            version=_plugin_version(info.package),
            description=info.description,
            author=info.author,
            homepage=info.homepage,
            tags=list(info.tags),
            verified=info.verified,
            core=info.core,
        )
        for name, info in registry.plugins.items()
    ]


def _load_registry_from_package() -> dict[str, Any]:
    registry_path = resources.files("phlo.plugins").joinpath("registry_data.json")
    return json.loads(registry_path.read_text(encoding="utf-8"))


def _load_registry_from_repo() -> dict[str, Any] | None:
    for parent in Path(__file__).resolve().parents:
        candidate = parent / "registry" / "plugins.json"
        if candidate.exists():
            return json.loads(candidate.read_text(encoding="utf-8"))
    return None


def _load_registry_from_local() -> dict[str, Any]:
    try:
        return _load_registry_from_package()
    except json.JSONDecodeError as exc:
        raise RegistryPayloadError("Bundled registry must contain JSON.") from exc
    except (OSError, ModuleNotFoundError) as exc:
        logger.debug("plugin_registry_package_load_failed", error=str(exc))
    try:
        registry = _load_registry_from_repo()
    except json.JSONDecodeError as exc:
        raise RegistryPayloadError("Local registry must contain JSON.") from exc
    if registry is not None:
        return registry
    raise FileNotFoundError("No bundled registry data found.")


def clear_registry_cache() -> None:
    """Clear the successfully refreshed snapshot."""
    _REGISTRY_CACHE.loaded_at = 0.0
    _REGISTRY_CACHE.data = None


def refresh_registry() -> RegistryDocument:
    """Refresh the snapshot atomically. Outages never replace a successful snapshot."""
    settings = get_settings()
    if settings.plugin_registry_url:
        try:
            response = httpx.get(
                settings.plugin_registry_url, timeout=settings.plugin_registry_timeout_seconds
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise RegistryUnavailable("Remote plugin registry is unavailable.") from exc
        try:
            payload = response.json()
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise RegistryPayloadError("Remote registry must contain JSON.") from exc
    else:
        payload = _load_registry_from_local()
    document = parse_registry(payload)
    _REGISTRY_CACHE.data = document
    _REGISTRY_CACHE.loaded_at = _time()
    return document


def registry_snapshot() -> RegistryDocument:
    """Query the last successful snapshot, or bundled data. Never performs network I/O."""
    if _REGISTRY_CACHE.data is not None:
        return _REGISTRY_CACHE.data
    return parse_registry(_load_registry_from_local())


def fetch_registry(force_refresh: bool = False) -> dict[str, Any]:
    """Legacy explicit fetch command with an uncached offline fallback on transport outage."""
    settings = get_settings()
    if (
        not force_refresh
        and _REGISTRY_CACHE.data is not None
        and _time() - _REGISTRY_CACHE.loaded_at < settings.plugin_registry_cache_ttl_seconds
    ):
        return _REGISTRY_CACHE.data.model_dump(exclude_unset=True)
    try:
        document = refresh_registry()
    except RegistryUnavailable:
        logger.warning("plugin_registry_fetch_fallback", source="local")
        document = registry_snapshot()
    return document.model_dump(exclude_unset=True)


def list_registry_plugins() -> list[RegistryPlugin]:
    """Query normalized entries without refreshing the registry."""
    return _normalize_registry(registry_snapshot())


def get_registry_data() -> dict[str, Any]:
    """Query a serializable snapshot without refreshing the registry."""
    return registry_snapshot().model_dump(exclude_unset=True)


def get_plugin(name: str) -> RegistryPlugin | None:
    """Query a single entry by name without refreshing the registry."""
    return next((plugin for plugin in list_registry_plugins() if plugin.name == name), None)


def search_plugins(
    query: str | None = None,
    plugin_type: str | None = None,
    tags: list[str] | None = None,
) -> list[RegistryPlugin]:
    """Search the current snapshot by name, description, type, or tags."""
    plugins = list_registry_plugins()
    if plugin_type:
        plugins = [plugin for plugin in plugins if plugin.type == plugin_type]
    if tags:
        tag_set = {tag.lower() for tag in tags}
        plugins = [
            plugin for plugin in plugins if tag_set.issubset({tag.lower() for tag in plugin.tags})
        ]
    if query:
        query_lower = query.lower()
        plugins = [
            p
            for p in plugins
            if any(
                query_lower in text.lower() for text in (p.name, p.description, p.package, *p.tags)
            )
        ]
    return plugins
