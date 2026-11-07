"""Import user workflow modules before handing them to definitions assembly.

Import failures report every broken module and prevent a partial graph from
loading. Definitions conversion and plugin lifecycle belong to ``assembly``.
"""

from __future__ import annotations

import importlib.util
import sys
import textwrap
import traceback
from pathlib import Path
from typing import Any

from phlo.exceptions import PhloDiscoveryError
from phlo.logging import get_logger
from phlo_dagster.framework import assembly

logger = get_logger(__name__)


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
        assembly.clear_workflow_registries()

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

    return assembly.build_workflow_definitions(imported_modules)


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
            f"  - module={module_name}, path={path}, error={type(error).__name__}: {error}\n"
            f"{textwrap.indent(''.join(traceback.format_exception(error)).rstrip(), '    ')}"
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
