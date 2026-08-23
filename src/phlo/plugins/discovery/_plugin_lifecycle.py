"""Lifecycle-aware registration helpers for discovered plugins.

Replacement ordering guarantee: the incoming plugin initializes before the
existing one is cleaned up, and any failure path re-raises with the registry
never left holding a half-initialized plugin.
Imported by phlo.plugins.discovery._plugin_loading to register plugins with lifecycle safety.
"""

from __future__ import annotations

from typing import Any

from phlo.logging import get_logger, log_event
from phlo.plugins.base import Plugin
from phlo.plugins.discovery.registry import get_global_registry

logger = get_logger(__name__)


def _emit_lifecycle_signal(
    *,
    event_name: str,
    level: str,
    plugin_type: str,
    plugin_name: str,
    lifecycle_phase: str,
    replace: bool,
    reason: str | None = None,
    error: Exception | None = None,
    target_plugin_name: str | None = None,
) -> None:
    fields: dict[str, Any] = {
        "plugin_type": plugin_type,
        "plugin_name": plugin_name,
        "lifecycle_phase": lifecycle_phase,
        "replace": replace,
    }
    if reason is not None:
        fields["reason"] = reason
    if target_plugin_name is not None:
        fields["target_plugin_name"] = target_plugin_name
    if error is not None:
        fields["error"] = str(error)
        fields["error_type"] = type(error).__name__
    log_event(logger, level, event_name, **fields)


def register_plugin_with_lifecycle(plugin_type: str, plugin: Plugin, replace: bool = True) -> None:
    """Register a plugin, running its lifecycle hooks with rollback safeguards.

    Replacement ordering guarantee: the incoming plugin initializes before the
    existing one is cleaned up, so a failed initialization leaves the previously
    registered instance untouched and still serving. If ``registry.register``
    fails after the old instance was already cleaned up, this attempts to
    restore it via ``initialize({})`` (best effort) and cleans up the incoming
    instance, then re-raises. Either way the caller observes an exception and
    never a registry containing a half-initialized plugin.
    """
    registry = get_global_registry()
    existing_plugin = registry.get(plugin_type, plugin.metadata.name)

    if existing_plugin and not replace:
        raise ValueError(
            f"Plugin '{plugin.metadata.name}' of type '{plugin_type}' is already registered. "
            "Use replace=True to overwrite."
        )

    try:
        plugin.initialize({})
        _emit_lifecycle_signal(
            event_name="plugin_lifecycle_initialize_succeeded",
            level="debug",
            plugin_type=plugin_type,
            plugin_name=plugin.metadata.name,
            lifecycle_phase="incoming_plugin_initialize",
            replace=replace,
        )
    except Exception as exc:
        _emit_lifecycle_signal(
            event_name="plugin_lifecycle_initialize_failed",
            level="error",
            plugin_type=plugin_type,
            plugin_name=plugin.metadata.name,
            lifecycle_phase="incoming_plugin_initialize",
            replace=replace,
            error=exc,
        )
        try:
            plugin.cleanup()
            _emit_lifecycle_signal(
                event_name="plugin_lifecycle_cleanup_succeeded",
                level="info",
                plugin_type=plugin_type,
                plugin_name=plugin.metadata.name,
                lifecycle_phase="incoming_plugin_cleanup",
                replace=replace,
                reason="initialize_failed",
            )
        except Exception as cleanup_exc:
            _emit_lifecycle_signal(
                event_name="plugin_lifecycle_cleanup_failed",
                level="error",
                plugin_type=plugin_type,
                plugin_name=plugin.metadata.name,
                lifecycle_phase="incoming_plugin_cleanup",
                replace=replace,
                reason="initialize_failed",
                error=cleanup_exc,
            )
            logger.warning(
                "plugin_cleanup_after_initialize_failed",
                plugin_type=plugin_type,
                plugin_name=plugin.metadata.name,
                exc_info=True,
            )
        raise

    existing_cleaned = False
    existing_plugin_name = existing_plugin.metadata.name if existing_plugin else None
    try:
        if existing_plugin and replace:
            existing_plugin.cleanup()
            _emit_lifecycle_signal(
                event_name="plugin_lifecycle_cleanup_succeeded",
                level="debug",
                plugin_type=plugin_type,
                plugin_name=existing_plugin_name or plugin.metadata.name,
                lifecycle_phase="existing_plugin_cleanup",
                replace=replace,
                reason="replacement",
                target_plugin_name=plugin.metadata.name,
            )
            existing_cleaned = True
        registry.register(plugin_type, plugin, replace=replace)
    except Exception:
        if existing_cleaned:
            assert existing_plugin is not None
            try:
                existing_plugin.initialize({})
                _emit_lifecycle_signal(
                    event_name="plugin_lifecycle_initialize_succeeded",
                    level="debug",
                    plugin_type=plugin_type,
                    plugin_name=existing_plugin_name or plugin.metadata.name,
                    lifecycle_phase="existing_plugin_recovery_initialize",
                    replace=replace,
                    reason="registration_failed_rollback",
                    target_plugin_name=plugin.metadata.name,
                )
            except Exception as recovery_exc:
                _emit_lifecycle_signal(
                    event_name="plugin_lifecycle_initialize_failed",
                    level="error",
                    plugin_type=plugin_type,
                    plugin_name=existing_plugin_name or plugin.metadata.name,
                    lifecycle_phase="existing_plugin_recovery_initialize",
                    replace=replace,
                    reason="registration_failed_rollback",
                    target_plugin_name=plugin.metadata.name,
                    error=recovery_exc,
                )
                logger.error(
                    "plugin_recovery_initialize_failed",
                    plugin_type=plugin_type,
                    plugin_name=plugin.metadata.name,
                    exc_info=True,
                )
        try:
            plugin.cleanup()
        except Exception:
            logger.warning(
                "plugin_cleanup_after_registration_failed",
                plugin_type=plugin_type,
                plugin_name=plugin.metadata.name,
                exc_info=True,
            )
        raise
