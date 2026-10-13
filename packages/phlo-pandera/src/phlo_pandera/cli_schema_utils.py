"""Shared CLI utilities for schema commands.

Discovers Pandera schema classes from configured search paths, renders Rich
tables for listings, performs basic schema-file syntax checks, and
classifies schema changes as SAFE, WARNING, or BREAKING.
"""

import importlib
from importlib import import_module
import sys
from pathlib import Path
from typing import Optional

from phlo.config.process import get_process_settings
from phlo.logging import get_logger
from phlo_pandera.settings import get_settings
from rich.table import Table

logger = get_logger(__name__)


def _default_schema_search_paths() -> list[str]:
    """Search paths from PHLO_SCHEMA_SEARCH_PATHS, else project or local examples/workflows."""
    env_paths = get_settings().schema_search_paths
    if env_paths is not None:
        return env_paths

    project_root = get_process_settings().phlo_project_path
    if project_root:
        return [
            str(Path(project_root) / "examples"),
            str(Path(project_root) / "workflows"),
        ]

    return ["examples", "workflows"]


def format_table(title: str, columns: list[str], rows: list[tuple]) -> Table:
    """Build a Rich Table with the given title, column headers, and rows."""
    table = Table(title=title)
    for col in columns:
        table.add_column(col)
    for row in rows:
        table.add_row(*[str(item) for item in row])
    return table


def validate_schema_file(schema_path: Path) -> None:
    """Validate basic schema file syntax and structure."""
    if not schema_path.exists():
        raise FileNotFoundError(f"Schema file not found: {schema_path}")

    content = schema_path.read_text()
    checks = {
        "Has imports": "import" in content.lower(),
        "Has class definition": "class " in content,
        "Has docstring": '"""' in content or "'''" in content,
        "Valid Python": True,
    }

    try:
        compile(content, str(schema_path), "exec")
    except SyntaxError as exc:
        checks["Valid Python"] = False
        raise ValueError(f"Syntax error in {schema_path}: {exc}") from exc

    failed = [name for name, passed in checks.items() if not passed]
    if failed:
        raise ValueError(f"Schema validation failed for {schema_path}: {', '.join(failed)}")


def _scan_schema_file(path: Path, py_file: Path, data_frame_model: type, schemas: dict) -> None:
    import inspect

    try:
        parts = py_file.relative_to(path.parent).parts[:-1] + (py_file.stem,)
        module_name = ".".join(parts)
        module_parts = module_name.split(".")
        for index in range(1, len(module_parts) + 1):
            sys.modules.pop(".".join(module_parts[:index]), None)

        try:
            module = import_module(module_name)
        except (ImportError, ModuleNotFoundError):
            logger.debug("schema_discovery_import_failed", module_name=module_name)
            return

        for name, obj in inspect.getmembers(module):
            if (
                inspect.isclass(obj)
                and issubclass(obj, data_frame_model)
                and obj is not data_frame_model
                and obj.__module__ == module.__name__
            ):
                setattr(obj, "__phlo_schema_source_path__", str(py_file.resolve()))
                schemas[name] = obj
    except Exception:
        logger.warning(
            "schema_discovery_file_scan_failed",
            search_path=str(path),
            schema_file=str(py_file),
        )


def discover_pandera_schemas(
    search_paths: Optional[list[str]] = None,
) -> dict[str, type]:
    """Discover DataFrameModel subclasses under the search paths, mapping name to class."""
    from pandera.pandas import DataFrameModel

    if search_paths is None:
        search_paths = _default_schema_search_paths()

    schemas = {}

    for search_path in search_paths:
        path = Path(search_path)
        if not path.exists():
            continue

        import_root = str(path.parent.resolve())
        added_import_root = False
        if import_root not in sys.path:
            sys.path.insert(0, import_root)
            added_import_root = True
            importlib.invalidate_caches()

        # Snapshot the module cache: anything imported here is dropped again
        # in the finally block so repeated discovery re-imports current file
        # contents instead of serving classes cached by an earlier scan.
        old_modules = dict(sys.modules)
        try:
            for py_file in path.glob("**/schemas/*.py"):
                if py_file.name.startswith("_"):
                    continue
                _scan_schema_file(path, py_file, DataFrameModel, schemas)
        finally:
            for module_name in set(sys.modules) - set(old_modules):
                sys.modules.pop(module_name, None)
            for module_name, module in old_modules.items():
                if sys.modules.get(module_name) is not module:
                    sys.modules[module_name] = module
            if added_import_root:
                try:
                    sys.path.remove(import_root)
                except ValueError:
                    pass

    return schemas


def classify_schema_change(old_schema: dict, new_schema: dict) -> tuple[str, list[str]]:
    """Compare column sets and types; returns (SAFE|WARNING|BREAKING, detail messages)."""
    old_cols = set(old_schema.keys())
    new_cols = set(new_schema.keys())

    added = new_cols - old_cols
    removed = old_cols - new_cols
    changed = old_cols & new_cols

    details = []
    severity = "SAFE"

    if removed:
        details.append(f"Removed columns: {', '.join(removed)}")
        severity = "BREAKING"

    type_changes = []
    for col in changed:
        if old_schema[col] != new_schema[col]:
            type_changes.append(f"{col}: {old_schema[col]} -> {new_schema[col]}")

    if type_changes:
        details.append(f"Type changes: {', '.join(type_changes)}")
        severity = "BREAKING"

    if added:
        details.append(f"Added columns: {', '.join(added)}")
        if severity == "SAFE":
            severity = "SAFE"

    if not details:
        details.append("No changes detected")

    return severity, details
