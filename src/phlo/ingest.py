"""Provider-neutral ingestion public API.

Plugin discovery is re-run on each public call, so ingestion providers
installed after this process started are still visible without a restart.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any


def _discover_ingestion_providers() -> None:
    """Load installed ingestion providers into the plugin registry."""
    from phlo.plugins.discovery import discover_plugins

    discover_plugins(plugin_type="ingestion_provider", auto_register=True)


def providers() -> list[str]:
    """Return installed ingestion provider names."""
    from phlo.plugins.discovery import get_global_registry

    _discover_ingestion_providers()
    return get_global_registry().list("ingestion_provider")


def _missing_provider_error(name: str) -> ModuleNotFoundError:
    installed = providers()
    installed_text = ", ".join(installed) if installed else "none"
    return ModuleNotFoundError(
        f"Ingestion provider '{name}' is not installed. "
        f"Installed ingestion providers: {installed_text}. "
        f"Install phlo-{name} or choose one of the installed providers."
    )


def _provider_or_raise(name: str) -> Any:
    """Resolve a provider plugin or raise the public missing-provider error."""
    from phlo.plugins.discovery import get_global_registry

    plugin = get_global_registry().get("ingestion_provider", name)
    if plugin is None:
        raise _missing_provider_error(name)
    return plugin


def provider(name: str) -> Callable[..., Callable[..., Any]]:
    """Return the decorator factory for a named ingestion provider."""
    _discover_ingestion_providers()
    return _provider_or_raise(name).get_decorator()


def assets(provider_name: str | None = None) -> list[Any]:
    """Return registered ingestion assets for one provider or all providers."""
    _discover_ingestion_providers()
    if provider_name is not None:
        plugin = _provider_or_raise(provider_name)
        return list(plugin.get_asset_retriever()())

    from phlo.plugins.discovery import get_global_registry

    collected: list[Any] = []
    for name in get_global_registry().list("ingestion_provider"):
        plugin = _provider_or_raise(name)
        collected.extend(plugin.get_asset_retriever()())
    return collected


def dlt(*args: Any, **kwargs: Any) -> Callable[..., Any]:
    """Return the DLT ingestion decorator factory."""
    return provider("dlt")(*args, **kwargs)


def sling(*args: Any, **kwargs: Any) -> Callable[..., Any]:
    """Return the Sling ingestion decorator factory."""
    return provider("sling")(*args, **kwargs)


__all__ = ["assets", "dlt", "provider", "providers", "sling"]
