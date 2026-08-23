"""Trino catalog generator from discovered plugins.

This module generates Trino catalog configuration files (.properties)
from discovered catalog plugins. It supports both modern plugin discovery
and legacy entry-point based catalog loading.

Functions:
    discover_trino_catalogs: Discover Trino-compatible catalog plugins.
    generate_catalog_files: Generate catalog .properties files.
    _load_entry_points: Load catalog plugins from entry points.
    _filter_catalogs: Filter catalogs by target runtime.
    _to_properties_file: Serialize properties to Java format.

Example:
    >>> from phlo_trino.catalog_generator import generate_catalog_files
    >>> files = generate_catalog_files("./trino/catalog")
    >>> print(files)
    {'iceberg': PosixPath('.../iceberg.properties')}

"""

from __future__ import annotations

import os
import importlib.metadata
from pathlib import Path

from phlo.plugins.discovery import discover_plugins
from phlo.logging import get_logger, setup_logging
from phlo.plugins.base import CatalogPlugin

logger = get_logger(__name__)


def _load_entry_points(group: str) -> list[CatalogPlugin]:
    """Load catalog plugins from a Python entry-point group."""
    try:
        entry_points = importlib.metadata.entry_points(group=group)
    except TypeError:
        # Python < 3.10 has no selectable entry_points(group=...) API.
        all_entry_points = importlib.metadata.entry_points()
        entry_points = all_entry_points.get(group, [])

    catalogs: list[CatalogPlugin] = []
    for entry_point in entry_points:
        try:
            plugin_class = entry_point.load()
            plugin = plugin_class() if isinstance(plugin_class, type) else plugin_class
            if isinstance(plugin, CatalogPlugin):
                catalogs.append(plugin)
            else:
                logger.error(
                    "trino_catalog_plugin_invalid_type",
                    entry_point_name=entry_point.name,
                    expected_type="CatalogPlugin",
                )
        except Exception as exc:
            logger.error(
                "trino_catalog_plugin_instantiation_failed",
                entry_point_name=entry_point.name,
                error=str(exc),
                exc_info=True,
            )

    return catalogs


def _filter_catalogs(catalogs: list[CatalogPlugin], target: str) -> list[CatalogPlugin]:
    """Filter catalogs to those that support a target runtime."""
    filtered: list[CatalogPlugin] = []
    for catalog in catalogs:
        if catalog.supports_target(target):
            filtered.append(catalog)
            logger.info(
                "trino_catalog_discovered",
                target=target,
                catalog_name=catalog.catalog_name,
            )
        else:
            logger.debug(
                "trino_catalog_skipped_unsupported_target",
                catalog_name=catalog.catalog_name,
                supported_targets=catalog.targets,
                target=target,
            )
    return filtered


def discover_trino_catalogs() -> list[CatalogPlugin]:
    """Discover Trino-compatible catalog plugins via entry points."""
    plugins = discover_plugins(plugin_type="catalogs", auto_register=False)
    catalogs: list[CatalogPlugin] = []
    for plugin in plugins.get("catalogs", []):
        if isinstance(plugin, CatalogPlugin):
            catalogs.append(plugin)

    legacy_catalogs = _load_entry_points("phlo.plugins.trino_catalogs")
    if legacy_catalogs:
        logger.warning(
            "trino_legacy_catalog_entry_points_detected",
            legacy_group="phlo.plugins.trino_catalogs",
            replacement_group="phlo.plugins.catalogs",
        )

    combined = catalogs + legacy_catalogs
    # First registration of a catalog name wins, so a modern plugin shadows a
    # legacy entry point with the same name.
    unique: dict[str, CatalogPlugin] = {}
    for catalog in combined:
        if catalog.catalog_name not in unique:
            unique[catalog.catalog_name] = catalog

    return _filter_catalogs(list(unique.values()), "trino")


def _to_properties_file(properties: dict[str, object]) -> str:
    """Serialize catalog properties to Java ``.properties`` text."""

    def escape_value(value: object) -> str:
        """Escape a value for Java ``.properties`` output."""
        text = str(value)
        text = text.replace("\\", "\\\\")
        text = text.replace("\t", "\\t")
        text = text.replace("\n", "\\n")
        text = text.replace("\r", "\\r")
        text = text.replace("\f", "\\f")
        if text and text[0] in (" ", "\t", "#", "!"):
            text = f"\\{text}"
        text = text.replace("=", "\\=")
        text = text.replace(":", "\\:")
        return text

    lines = [f"{escape_value(key)}={escape_value(value)}" for key, value in properties.items()]
    return "\n".join(lines) + "\n"


def generate_catalog_files(output_dir: str | Path | None = None) -> dict[str, Path]:
    """Generate Trino catalog .properties files from discovered plugins."""
    if output_dir is None:
        output_dir = Path(os.environ.get("TRINO_CATALOG_DIR", "./trino/catalog"))
    else:
        output_dir = Path(output_dir)

    output_dir.mkdir(parents=True, exist_ok=True)

    catalogs = discover_trino_catalogs()
    generated = {}

    for catalog in catalogs:
        try:
            filename = f"{catalog.catalog_name}.properties"
            filepath = output_dir / filename
            content = _to_properties_file(catalog.get_properties())

            filepath.write_text(content)
            generated[catalog.catalog_name] = filepath
            logger.info(
                "trino_catalog_file_generated",
                catalog_name=catalog.catalog_name,
                path=str(filepath),
            )
        except Exception as exc:
            logger.error(
                "trino_catalog_file_generation_failed",
                catalog_name=catalog.catalog_name,
                output_dir=str(output_dir),
                error=str(exc),
                exc_info=True,
            )

    return generated


if __name__ == "__main__":
    import sys

    setup_logging()

    output = sys.argv[1] if len(sys.argv) > 1 else None
    result = generate_catalog_files(output)
    logger.info("trino_catalog_generation_completed", catalog_count=len(result))
    for name, path in result.items():
        logger.info("trino_catalog_generation_result", catalog_name=name, path=str(path))
