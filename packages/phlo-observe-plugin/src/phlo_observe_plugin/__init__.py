"""phlo-observe integration for Phlo.

- ``ObserveHookPlugin`` translates hook-bus events into canonical
  ``phlo-observe`` events.
- ``ObserveDagsterExtension`` contributes a run-status sensor that closes each
  physical Dagster run with a terminal ``pipeline.run`` event.
- ``PhloObserverServicePlugin`` / ``PhloObserverDbSetupPlugin`` declare the
  observer compose service and its database provisioning step.
- ``presentation`` carries Phlo's ``PrettyRenderer`` rules and the ``pretty``
  drain — imported lazily since it needs a PrettyRenderer-capable SDK.
"""

from __future__ import annotations

__all__ = [
    "ObserveDagsterExtension",
    "ObserveHookPlugin",
    "PhloObserverDbSetupPlugin",
    "PhloObserverServicePlugin",
    "PrettyDrain",
    "pretty_renderer",
]


def __getattr__(name: str) -> object:
    if name == "ObserveHookPlugin":
        from phlo_observe_plugin.hooks_plugin import ObserveHookPlugin

        return ObserveHookPlugin
    if name == "ObserveDagsterExtension":
        from phlo_observe_plugin.dagster_ext import ObserveDagsterExtension

        return ObserveDagsterExtension
    if name in {"PhloObserverServicePlugin", "PhloObserverDbSetupPlugin"}:
        from phlo_observe_plugin import plugin as _plugin

        return getattr(_plugin, name)
    if name in {"PrettyDrain", "pretty_renderer"}:
        from phlo_observe_plugin import presentation as _presentation

        return getattr(_presentation, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
