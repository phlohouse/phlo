"""Framework definitions builder for Phlo-Dagster projects.

Primary entry point for building Dagster Definitions in Phlo projects: loads
configuration and logging, discovers user workflows from the configured
``workflows/`` directory, merges in Dagster extensions collected from plugins
(via the ``phlo.plugins.dagster`` entry point group), adds WAP sensors when
the catalog capability supports refs, and ensures core resources such as
Trino. WAP sensors require a ``VersionedCatalog`` provider; incompatible or
unavailable providers log warnings instead of raising.

Executor selection honors the ``PHLO_FORCE_IN_PROCESS_EXECUTOR`` and
``PHLO_FORCE_MULTIPROCESS_EXECUTOR`` overrides first, then falls back to
the ``PHLO_HOST_PLATFORM`` setting and finally ``platform.system()``.
Darwin/macOS hosts default to the in-process executor because multiprocess
execution crashes (SIGBUS) under Docker Desktop/Colima on macOS.

The module exposes ``build_definitions()`` as the programmatic entry point
and a global ``defs`` instance that Dagster loads via ``workspace.yaml``
(``load_from: - python_module: phlo_dagster.framework.definitions``).

Example:
    Basic usage::

        from phlo_dagster.framework.definitions import build_definitions

        defs = build_definitions()

    Custom workflows path::

        defs = build_definitions(workflows_path="custom_workflows")
"""

from __future__ import annotations

import platform
from pathlib import Path
from typing import Any

import dagster as dg

from phlo.capabilities.interfaces import SnapshotPromotionCatalog, VersionedCatalog
from phlo.capabilities.resolver import resolve_capability
from phlo.exceptions import PhloCapabilitySetupError
from phlo.infrastructure import load_wap_config
from phlo_dagster.framework.discovery import (
    _collect_dagster_extension_definitions,
    _ensure_core_resources,
    discover_user_workflows,
)
from phlo_dagster.framework.asset_diagnostics import merge_definitions_with_duplicate_diagnostics
from phlo_dagster.framework.schema_contracts import maybe_refresh_contracts
from phlo_dagster.settings import get_settings
from phlo.logging import get_logger, setup_logging

logger = get_logger(__name__)


def _collect_wap_definitions() -> dg.Definitions | None:
    """Load WAP sensors when a catalog capability matching the strategy is available."""
    wap_config = load_wap_config()
    if not wap_config.enabled:
        logger.info("dagster_wap_definitions_disabled_by_project_policy")
        return None

    resolution = resolve_capability("catalog")
    if resolution is None:
        return None

    if wap_config.strategy == "snapshot":
        # Snapshot promotion catalogs deliberately do not implement branch
        # semantics; they must expose snapshot-based release promotion.
        if not (resolution.support.supports_promote and resolution.support.supports_snapshots):
            return None
        provider_ok = isinstance(resolution.provider, SnapshotPromotionCatalog)
    else:
        if not (resolution.support.supports_refs and resolution.support.supports_promote):
            return None
        provider_ok = isinstance(resolution.provider, VersionedCatalog)

    provider = resolution.provider
    if not provider_ok:
        logger.warning(
            "dagster_wap_catalog_provider_incompatible",
            capability_name=resolution.name,
            provider_type=type(provider).__name__,
            strategy=wap_config.strategy,
        )
        return None

    try:
        from phlo_dagster.wap_sensors import get_wap_definitions
    except Exception:
        logger.warning(
            "dagster_wap_definitions_unavailable",
            capability_name=resolution.name,
            exc_info=True,
        )
        return None

    logger.info(
        "dagster_wap_definitions_enabled",
        capability_name=resolution.name,
    )
    return get_wap_definitions()


def _default_executor() -> dg.ExecutorDefinition | None:
    """Choose an executor suited to the current environment.

    Explicit force-in-process and force-multiprocess overrides win, followed
    by the configured host platform and finally ``platform.system()``.
    Defaults to the in-process executor on Darwin/macOS because DuckDB has
    been crashing (SIGBUS) when multiprocessing runs under Docker
    Desktop/Colima on macOS.
    """
    settings = get_settings()

    # Priority 1: Explicit force in-process
    if settings.phlo_force_in_process_executor:
        logger.info("Using in-process executor (forced via PHLO_FORCE_IN_PROCESS_EXECUTOR)")
        return dg.in_process_executor

    # Priority 2: Explicit force multiprocess
    if settings.phlo_force_multiprocess_executor:
        logger.info("Using multiprocess executor (forced via PHLO_FORCE_MULTIPROCESS_EXECUTOR)")
        return dg.multiprocess_executor.configured({"max_concurrent": 4})

    # Priority 3: Check host platform (for Docker on macOS detection)
    host_platform = settings.phlo_host_platform
    if host_platform is None:
        # Priority 4: Fall back to container/local platform
        host_platform = platform.system()
        logger.debug("phlo_host_platform_detected", host_platform=host_platform)
    else:
        logger.info("using_phlo_host_platform", host_platform=host_platform)

    # Use in-process executor if host is macOS
    if host_platform == "Darwin":
        logger.info("Using in-process executor (host platform: Darwin/macOS)")
        return dg.in_process_executor

    # Default: multiprocess executor for Linux
    logger.info("using_multiprocess_executor", host_platform=host_platform)
    return dg.multiprocess_executor.configured({"max_concurrent": 4})


def build_definitions(
    workflows_path: Path | str | None = None,
) -> Any:
    """Build Dagster definitions by merging user workflows with framework resources.

    Main entry point for user projects: loads configuration, discovers user
    workflows from ``workflows_path`` (falling back to the configured default
    of "workflows"), loads core Phlo resources, and merges everything together.

    Example:
        ```python
        # In your project's workspace.yaml:
        # load_from:
        #   - python_module:
        #       module_name: phlo_dagster.framework.definitions

        # Basic usage (loads workflows from ./workflows)
        defs = build_definitions()

        # Custom workflows path
        defs = build_definitions(workflows_path="custom_workflows")

        defs = build_definitions()
        ```
    """
    setup_logging()
    settings = get_settings()

    # Determine workflows path
    if workflows_path is None:
        workflows_path = Path(settings.workflows_path)
    else:
        workflows_path = Path(workflows_path)

    logger.info("building_phlo_definitions", workflows_path=str(workflows_path))

    # Discover user workflows
    try:
        user_defs = discover_user_workflows(workflows_path, clear_registries=True)
        maybe_refresh_contracts(workflows_path, logger)
        user_assets = list(getattr(user_defs, "assets", []) or [])
        user_checks = list(getattr(user_defs, "asset_checks", []) or [])
        logger.info("Discovered %d user assets, %d checks", len(user_assets), len(user_checks))
    except PhloCapabilitySetupError as exc:
        if exc.required:
            logger.error(
                "required_capability_setup_failed",
                capability=exc.capability,
                error=str(exc),
                workflows_path=str(workflows_path),
                exc_info=True,
            )
            raise
        logger.warning(
            "optional_capability_degraded",
            capability=exc.capability,
            error=str(exc),
            workflows_path=str(workflows_path),
        )
        user_defs = dg.Definitions()
    dagster_defs = _collect_dagster_extension_definitions()
    definitions_to_merge = [user_defs]
    if dagster_defs is not None:
        definitions_to_merge.append(dagster_defs)
    wap_defs = _collect_wap_definitions()
    if wap_defs is not None:
        definitions_to_merge.append(wap_defs)

    merged = merge_definitions_with_duplicate_diagnostics(*definitions_to_merge)
    merged = _ensure_core_resources(merged)

    executor = _default_executor()
    final_defs = dg.Definitions(
        assets=merged.assets,
        asset_checks=merged.asset_checks,
        schedules=merged.schedules,
        sensors=merged.sensors,
        resources=merged.resources,
        jobs=merged.jobs,
        executor=executor,
    )

    final_assets = list(final_defs.assets or [])
    final_checks = list(final_defs.asset_checks or [])
    final_jobs = list(final_defs.jobs or [])
    final_schedules = list(final_defs.schedules or [])
    logger.info(
        "Built Phlo definitions: %d assets, %d checks, %d jobs, %d schedules",
        len(final_assets),
        len(final_checks),
        len(final_jobs),
        len(final_schedules),
    )

    return final_defs


# Global definitions instance for Dagster to load.
defs = build_definitions()
