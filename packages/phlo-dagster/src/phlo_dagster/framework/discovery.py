"""User workflow discovery for Phlo-Dagster framework.

This module discovers and loads user workflow files from project directories,
dynamically importing Python modules which triggers capability registration,
then building Dagster definitions through the active orchestrator adapter.

Discovery Process:
    1. Locate workflows directory (default: ./workflows)
    2. Import all Python modules (excluding __init__.py and private files)
    3. Trigger capability discovery from registered specs
    4. Collect Dagster definitions from modules
    5. Merge with core framework resources
    6. Apply WAP sensors if versioned catalog available
    7. Select appropriate executor

Module Import:
    Files are imported with paths converted to module names:
    workflows/ingestion/orders.py → workflows.ingestion.orders

Capability Integration:
    Imported modules register capabilities via @asset/@check decorators.
    The orchestrator adapter (typically DagsterOrchestratorAdapter) converts
these specs into Dagster definitions.

Extension Discovery:
    Additional DagsterExtensionPlugins are discovered via entry_points
    (phlo.plugins.dagster) and merged into definitions.

Executor Selection:
    Platform-aware selection:
    - Darwin/macOS: in_process_executor (Docker Desktop SIGBUS workaround)
    - Linux: multiprocess_executor
    - Overridable via PHLO_FORCE_*_EXECUTOR

Example:
    Framework entry point::

        from phlo_dagster.framework.discovery import discover_user_workflows

        defs = discover_user_workflows("workflows")

    Getting workflows path::

        from phlo_dagster.framework.discovery import get_workflows_path_from_config

        path = get_workflows_path_from_config()

Sits in the phlo-dagster framework layer, building on phlo.capabilities.discovery and
phlo.plugins.discovery to turn user workflow modules into Dagster definitions.
"""

from __future__ import annotations

import importlib.metadata
import importlib.util
import sys
import warnings
from pathlib import Path
from typing import Any

from phlo.capabilities.discovery import discover_capabilities
from phlo.capabilities.external_refs import validate_external_asset_references
from phlo.capabilities.registry import clear_all_capabilities, get_capability_registry
from phlo.exceptions import PhloConfigError, PhloDiscoveryError
from phlo.logging import get_logger
from phlo.orchestrators import get_active_orchestrator
from phlo_dagster.framework.asset_diagnostics import merge_definitions_with_duplicate_diagnostics

logger = get_logger(__name__)

# Suppress preview warnings from orchestrators that emit them on import.
warnings.filterwarnings("ignore", message=".*is currently in preview.*", category=UserWarning)


def discover_user_workflows(
    workflows_path: Path | str,
    clear_registries: bool = False,
) -> Any:
    """Import workflow modules and build orchestrator definitions from them.

    Imports every module under ``workflows_path`` (which registers
    capability specs), then builds definitions via the active adapter.
    Raises ValueError when the path is not a directory.

    """
    workflows_path = Path(workflows_path)

    if clear_registries:
        _clear_capability_registries()

    if not workflows_path.exists():
        logger.warning(
            "Workflows directory not found: %s. No user workflows will be loaded.",
            workflows_path,
        )
        imported_modules: list[Any] = []
    elif not workflows_path.is_dir():
        raise ValueError(f"Workflows path must be a directory, got: {workflows_path}")
    else:
        logger.info("discovering_user_workflows", workflows_path=str(workflows_path))
        parent_dir = workflows_path.parent.resolve()
        if str(parent_dir) not in sys.path:
            sys.path.insert(0, str(parent_dir))
            logger.debug("added_parent_dir_to_python_path", parent_dir=str(parent_dir))
        imported_modules = _import_workflow_modules(workflows_path)
        logger.info(
            "imported_workflow_modules",
            module_count=len(imported_modules),
            workflows_path=str(workflows_path),
        )

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
    except Exception as exc:  # noqa: BLE001 - normalize fallback failure shape
        raise PhloConfigError("Dagster fallback also failed") from exc


def _import_workflow_modules(workflows_path: Path) -> list[Any]:
    """Import every workflow module or raise diagnostics for all import failures."""
    imported_modules: list[Any] = []
    failures: list[tuple[str, Path, Exception]] = []

    # Find all Python files
    py_files = list(workflows_path.rglob("*.py"))

    for py_file in py_files:
        # Skip __init__.py and files starting with underscore
        if py_file.name.startswith("_"):
            continue

        try:
            # Convert file path to module name
            # e.g., workflows/ingestion/weather/observations.py
            #    -> workflows.ingestion.weather.observations
            relative_path = py_file.relative_to(workflows_path.parent)
            module_name = str(relative_path.with_suffix("")).replace("/", ".")

            logger.debug("importing_workflow_module", module_name=module_name, path=str(py_file))

            # Import the module
            spec = importlib.util.spec_from_file_location(module_name, py_file)
            if spec is None or spec.loader is None:
                failure = ImportError("Python could not create an import loader")
                logger.error(
                    "workflow_module_spec_load_failed",
                    module_name=module_name,
                    path=str(py_file),
                )
                failures.append((module_name, py_file, failure))
                continue

            module = importlib.util.module_from_spec(spec)
            sys.modules[module_name] = module
            spec.loader.exec_module(module)

            imported_modules.append(module)
            logger.debug("workflow_module_import_succeeded", module_name=module_name)

        except Exception as exc:
            logger.error(
                "workflow_module_import_failed",
                module_name=module_name,
                path=str(py_file),
                error=str(exc),
                exc_info=True,
            )
            sys.modules.pop(module_name, None)
            failures.append((module_name, py_file, exc))
            continue

    if failures:
        details = "\n".join(
            f"  - module={module_name}, path={path}, error={type(error).__name__}: {error}"
            for module_name, path, error in failures
        )
        raise PhloDiscoveryError(
            message=(
                "Workflow discovery failed; the code location was not loaded because one or more "
                f"workflow modules could not be imported:\n{details}"
            ),
            suggestions=[
                "Fix each listed workflow import error and reload the code location.",
                "Do not rely on a partial asset graph after workflow discovery fails.",
            ],
            cause=failures[0][2],
        )

    return imported_modules


def _collect_dagster_extension_definitions() -> Any:
    try:
        import dagster as dg
    except Exception:  # noqa: BLE001 - optional dependency
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
    except Exception:  # noqa: BLE001 - optional dependency
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
    except Exception:  # noqa: BLE001 - optional dependency
        return capability_defs

    if not isinstance(capability_defs, dg.Definitions):
        return capability_defs
    if not isinstance(module_defs, dg.Definitions):
        return capability_defs
    return merge_definitions_with_duplicate_diagnostics(capability_defs, module_defs)


def _ensure_core_resources(definitions: Any) -> Any:
    try:
        import dagster as dg
    except Exception:  # noqa: BLE001 - optional dependency
        return definitions

    resources = dict(definitions.resources or {})
    if "trino" not in resources:
        trino_resource = _default_trino_resource()
        if trino_resource is not None:
            resources["trino"] = trino_resource
    if resources == (definitions.resources or {}):
        return definitions
    return dg.Definitions.merge(definitions, dg.Definitions(resources=resources))


def _default_trino_resource() -> Any | None:
    # Only auto-wire a trino resource if a provider has already registered one.
    registry = get_capability_registry()
    for resource in registry.list("resource"):
        if resource.name == "trino":
            return resource.resource
    return None


def _clear_capability_registries() -> None:
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

    try:
        entry_points = importlib.metadata.entry_points(group="phlo.plugins.dagster")
    except TypeError:
        entry_points = importlib.metadata.entry_points().get("phlo.plugins.dagster", [])

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


def get_workflows_path_from_config() -> Path:
    """Return the workflows path from configuration, defaulting to "workflows".

    Example:
        ```python
        workflows_path = get_workflows_path_from_config()
        defs = discover_user_workflows(workflows_path)
        ```

    """
    try:
        from phlo_dagster.settings import get_settings

        settings = get_settings()
        return Path(settings.workflows_path)
    except Exception as exc:
        logger.warning("workflows_path_resolution_failed", error=str(exc))

    # Default fallback
    return Path("workflows")
