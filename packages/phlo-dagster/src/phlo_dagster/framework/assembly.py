"""Side-effect-free definitions assembly and plugin registry lifecycle.

Workflow importers supply modules; this owner discovers registered capabilities,
selects the adapter and combines native definitions. Extension loading and cleanup
share the same plugin policy. Importing this module never builds global definitions.
"""

from __future__ import annotations

import importlib.metadata
import warnings
from typing import Any

from phlo.application.discovery import discover_capabilities
from phlo.capabilities.external_refs import validate_external_asset_references
from phlo.capabilities.registry import clear_all_capabilities, get_capability_registry
from phlo.exceptions import PhloConfigError
from phlo.logging import get_logger
from phlo.orchestrators import get_active_orchestrator

from phlo_dagster.asset_diagnostics import merge_definitions_with_duplicate_diagnostics

logger = get_logger(__name__)

# Suppress preview warnings from orchestrators that emit them on import.
warnings.filterwarnings("ignore", message=".*is currently in preview.*", category=UserWarning)


def build_workflow_definitions(imported_modules: list[Any]) -> Any:
    """Combine registered capabilities and native definitions from imported modules."""
    # Discover provider plugins after modules are imported.
    discover_capabilities()

    registry = get_capability_registry()
    assets = registry.list("asset")
    checks = registry.list("check")
    resources = registry.list("resource")
    validate_external_asset_references(assets)
    module_defs = _collect_module_dagster_definitions(imported_modules)
    try:
        adapter = get_active_orchestrator()
    except PhloConfigError as exc:
        logger.warning(
            "Orchestrator adapter not available, using Dagster fallback definitions: %s",
            exc,
        )
        capability_defs = _build_dagster_fallback_definitions(
            assets=assets, checks=checks, resources=resources
        )
    else:
        capability_defs = adapter.build_definitions(
            assets=assets,
            checks=checks,
            resources=resources,
        )

    return _merge_dagster_definitions(
        capability_defs=capability_defs,
        module_defs=module_defs,
    )


def _build_dagster_fallback_definitions(
    *,
    assets: list[Any],
    checks: list[Any],
    resources: list[Any],
) -> Any:
    from phlo_dagster.adapter import DagsterOrchestratorAdapter

    try:
        adapter = DagsterOrchestratorAdapter()
        return adapter.build_definitions(
            assets=assets,
            checks=checks,
            resources=resources,
        )
    except PhloConfigError:
        raise
    except Exception as exc:
        raise PhloConfigError("Dagster fallback also failed") from exc


def collect_dagster_extension_definitions() -> Any:
    """Load and merge allowed extensions, isolating individual plugin failures."""
    try:
        import dagster as dg
    except Exception:
        return None

    definitions: list[dg.Definitions] = []
    for plugin in _discover_dagster_extensions():
        try:
            definitions.append(plugin.get_definitions())
        except Exception as exc:
            logger.error(
                "dagster_extension_definitions_failed",
                plugin_type=type(plugin).__name__,
                error=str(exc),
                exc_info=True,
            )

    return (
        merge_definitions_with_duplicate_diagnostics(*definitions)
        if definitions
        else dg.Definitions()
    )


def _collect_module_dagster_definitions(imported_modules: list[Any]) -> Any | None:
    try:
        import dagster as dg
    except Exception:
        return None

    definitions: list[dg.Definitions] = []
    seen_ids: set[int] = set()
    unresolved_job_cls = getattr(dg, "UnresolvedAssetJobDefinition", None)
    job_types: tuple[type[Any], ...]
    if isinstance(unresolved_job_cls, type):
        job_types = (dg.JobDefinition, unresolved_job_cls)
    else:
        job_types = (dg.JobDefinition,)

    def _record(def_obj: object, bucket: list[Any]) -> None:
        obj_id = id(def_obj)
        if obj_id in seen_ids:
            return
        seen_ids.add(obj_id)
        bucket.append(def_obj)

    for module in imported_modules:
        module_assets: list[Any] = []
        module_checks: list[Any] = []
        module_schedules: list[Any] = []
        module_sensors: list[Any] = []
        module_jobs: list[Any] = []

        for value in vars(module).values():
            if isinstance(value, dg.Definitions):
                _record(value, definitions)
                continue
            if isinstance(value, dg.AssetsDefinition):
                _record(value, module_assets)
                continue
            if isinstance(value, dg.AssetChecksDefinition):
                _record(value, module_checks)
                continue
            if isinstance(value, dg.ScheduleDefinition):
                _record(value, module_schedules)
                continue
            if isinstance(value, dg.SensorDefinition):
                _record(value, module_sensors)
                continue
            if isinstance(value, job_types):
                _record(value, module_jobs)

        if any((module_assets, module_checks, module_schedules, module_sensors, module_jobs)):
            definitions.append(
                dg.Definitions(
                    assets=module_assets or None,
                    asset_checks=module_checks or None,
                    schedules=module_schedules or None,
                    sensors=module_sensors or None,
                    jobs=module_jobs or None,
                )
            )

    return dg.Definitions.merge(*definitions) if definitions else dg.Definitions()


def _merge_dagster_definitions(*, capability_defs: Any, module_defs: Any | None) -> Any:
    if module_defs is None:
        return capability_defs

    try:
        import dagster as dg
    except Exception:
        return capability_defs

    if not isinstance(capability_defs, dg.Definitions):
        return capability_defs
    if not isinstance(module_defs, dg.Definitions):
        return capability_defs
    return merge_definitions_with_duplicate_diagnostics(capability_defs, module_defs)


def clear_workflow_registries() -> None:
    """Clear capability registries for testing; logs warnings when plugin cleanup fails."""
    from phlo.plugins.discovery import discover_plugins, get_global_registry

    clear_all_capabilities()
    discover_plugins(plugin_type="asset_provider", auto_register=True)
    registry = get_global_registry()

    for name in registry.list("asset_provider"):
        plugin = registry.get("asset_provider", name)
        if plugin is None:
            continue
        clear_fn = getattr(plugin, "clear_registries", None)
        if callable(clear_fn):
            try:
                clear_fn()
            except Exception as exc:
                logger.warning(
                    "dagster_asset_provider_registry_clear_failed",
                    provider_name=name,
                    error=str(exc),
                    exc_info=True,
                )

    for plugin in _discover_dagster_extensions():
        try:
            plugin.clear_registries()
        except Exception as exc:
            logger.warning(
                "dagster_extension_registry_clear_failed",
                plugin_type=type(plugin).__name__,
                error=str(exc),
                exc_info=True,
            )


def _discover_dagster_extensions() -> list[Any]:
    try:
        from phlo_dagster.dagster_ext import DagsterExtensionPlugin
    except Exception:
        return []

    settings = _get_settings()
    if not settings.plugins_enabled:
        logger.info("dagster_plugin_system_disabled")
        return []

    entry_points = importlib.metadata.entry_points(group="phlo.plugins.dagster")

    extensions: list[DagsterExtensionPlugin] = []
    for entry_point in entry_points:
        if not _is_plugin_allowed(entry_point.name, settings):
            continue
        try:
            plugin_class = entry_point.load()
            plugin = plugin_class() if isinstance(plugin_class, type) else plugin_class
        except Exception as exc:
            logger.warning(
                "dagster_extension_load_failed",
                entry_point_name=entry_point.name,
                error=str(exc),
                exc_info=True,
            )
            continue
        if not isinstance(plugin, DagsterExtensionPlugin):
            logger.warning(
                "dagster_extension_invalid_type",
                entry_point_name=entry_point.name,
                plugin_type=type(plugin).__name__,
            )
            continue
        extensions.append(plugin)
    return extensions


def _get_settings():
    from phlo.config import get_settings

    return get_settings()


def _is_plugin_allowed(plugin_name: str, settings) -> bool:
    if plugin_name in settings.plugins_blacklist:
        logger.debug("dagster_plugin_blacklisted", plugin_name=plugin_name)
        return False
    if settings.plugins_whitelist and plugin_name not in settings.plugins_whitelist:
        logger.debug("dagster_plugin_not_whitelisted", plugin_name=plugin_name)
        return False
    return True
