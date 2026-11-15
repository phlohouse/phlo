"""Lifecycle-aware registration helpers for discovered plugins.

The registry lock serializes replacement and publication. Incoming initialization
leaves the existing provider ready. Cleanup first withdraws its registration;
failed cleanup or publication restores the old instance only after recovery.
Imported by phlo.plugins.discovery._plugin_loading.
"""

from __future__ import annotations

from typing import Any

from phlo.logging_context import get_logger, log_event
from phlo.plugins.base.plugin import Plugin
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
    """Publish a ready provider, or recover the old provider before republishing it.

    A failed incoming initialize leaves the old provider untouched. Once cleanup
    starts, its outcome is not assumed reversible: recovery must succeed before
    the old provider becomes available again. Failed recovery leaves it absent.
    Readers and replacements share the registry lock. References already handed
    to callers are not leases and cannot be revoked by this registry.
    """
    registry = get_global_registry()
    with registry._lock:
        name = plugin.metadata.name
        existing_plugin = registry.get(plugin_type, name)
        if existing_plugin and not replace:
            raise ValueError(
                f"Plugin '{name}' of type '{plugin_type}' is already registered. "
                "Use replace=True to overwrite."
            )
        if existing_plugin is plugin:
            return
        try:
            plugin.initialize({})
            _emit_lifecycle_signal(
                event_name="plugin_lifecycle_initialize_succeeded",
                level="debug",
                plugin_type=plugin_type,
                plugin_name=name,
                lifecycle_phase="incoming_plugin_initialize",
                replace=replace,
            )
        except Exception as exc:
            _emit_lifecycle_signal(
                event_name="plugin_lifecycle_initialize_failed",
                level="error",
                plugin_type=plugin_type,
                plugin_name=name,
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
                    plugin_name=name,
                    lifecycle_phase="incoming_plugin_cleanup",
                    replace=replace,
                    reason="initialize_failed",
                )
            except Exception as cleanup_exc:
                _emit_lifecycle_signal(
                    event_name="plugin_lifecycle_cleanup_failed",
                    level="error",
                    plugin_type=plugin_type,
                    plugin_name=name,
                    lifecycle_phase="incoming_plugin_cleanup",
                    replace=replace,
                    reason="initialize_failed",
                    error=cleanup_exc,
                )
                logger.warning(
                    "plugin_cleanup_after_initialize_failed",
                    plugin_type=plugin_type,
                    plugin_name=name,
                    exc_info=True,
                )
            raise

        cleanup_started = False
        try:
            if existing_plugin:
                registry.remove(plugin_type, name)
                # Another family can still own this instance. Do not close its resources.
                if not any(item is existing_plugin for item in registry.iter_plugins()):
                    cleanup_started = True
                    existing_plugin.cleanup()
                    _emit_lifecycle_signal(
                        event_name="plugin_lifecycle_cleanup_succeeded",
                        level="debug",
                        plugin_type=plugin_type,
                        plugin_name=name,
                        lifecycle_phase="existing_plugin_cleanup",
                        replace=replace,
                        reason="replacement",
                        target_plugin_name=name,
                    )
            registry.register(plugin_type, plugin, replace=replace)
        except Exception:
            # A publishing error may have updated an index before raising.
            registry.remove(plugin_type, name)
            if existing_plugin:
                try:
                    if cleanup_started:
                        existing_plugin.initialize({})
                        _emit_lifecycle_signal(
                            event_name="plugin_lifecycle_initialize_succeeded",
                            level="debug",
                            plugin_type=plugin_type,
                            plugin_name=name,
                            lifecycle_phase="existing_plugin_recovery_initialize",
                            replace=replace,
                            reason="registration_failed_rollback",
                            target_plugin_name=name,
                        )
                    registry.register(plugin_type, existing_plugin, replace=True)
                except Exception as recovery_exc:
                    registry.remove(plugin_type, name)
                    _emit_lifecycle_signal(
                        event_name="plugin_lifecycle_initialize_failed",
                        level="error",
                        plugin_type=plugin_type,
                        plugin_name=name,
                        lifecycle_phase="existing_plugin_recovery_initialize",
                        replace=replace,
                        reason="registration_failed_rollback",
                        target_plugin_name=name,
                        error=recovery_exc,
                    )
                    logger.error(
                        "plugin_recovery_initialize_failed",
                        plugin_type=plugin_type,
                        plugin_name=name,
                        exc_info=True,
                    )
            try:
                plugin.cleanup()
            except Exception:
                logger.warning(
                    "plugin_cleanup_after_registration_failed",
                    plugin_type=plugin_type,
                    plugin_name=name,
                    exc_info=True,
                )
            raise
