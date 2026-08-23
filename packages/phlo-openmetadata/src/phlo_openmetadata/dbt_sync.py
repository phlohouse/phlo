"""dbt manifest parser and synchronizer.

Parses dbt manifest.json and catalog.json to extract model documentation,
column descriptions, and tests for syncing to OpenMetadata.

This module enables bi-directional sync between dbt projects and OpenMetadata,
ensuring documentation and lineage are consistent across both systems.

Example:
    >>> from phlo_openmetadata.dbt_sync import DbtManifestParser
    >>> parser = DbtManifestParser(
    ...     manifest_path="target/manifest.json",
    ...     catalog_path="target/catalog.json",
    ... )
    >>> manifest = parser.load_manifest()
    >>> models = parser.get_models(manifest)

"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

from phlo.logging import get_logger
from phlo_openmetadata.openmetadata import OpenMetadataColumn, OpenMetadataTable
from phlo_openmetadata.settings import OpenMetadataSettings

logger = get_logger(__name__)


class DbtManifestParser:
    """Parses dbt manifest.json for metadata extraction.

    Extracts model descriptions, column-level documentation, tests,
    and freshness policies for syncing to OpenMetadata.

    Example:
        >>> parser = DbtManifestParser("target/manifest.json", "target/catalog.json")
        >>> models = parser.get_models()
        >>> for model_id, model in models.items():
        ...     print(model.get("name"))
    """

    def __init__(self, manifest_path: str, catalog_path: Optional[str] = None):
        """Initialize dbt manifest parser."""
        self.manifest_path = Path(manifest_path)
        self.catalog_path = Path(catalog_path) if catalog_path else None
        self.manifest = None
        self.catalog = None

    @classmethod
    def from_settings(cls, settings: OpenMetadataSettings) -> "DbtManifestParser":
        """Create parser from OpenMetadata-owned config."""
        return cls(
            manifest_path=settings.openmetadata_dbt_manifest_path,
            catalog_path=settings.openmetadata_dbt_catalog_path,
        )

    def load_manifest(self) -> dict[str, Any]:
        """Load and parse dbt manifest.json.

        Raises: FileNotFoundError if manifest file not found.
        Raises: json.JSONDecodeError if manifest is invalid JSON.
        """
        if not self.manifest_path.exists():
            raise FileNotFoundError(f"dbt manifest not found: {self.manifest_path}")

        try:
            with open(self.manifest_path) as f:
                self.manifest = json.load(f)
            logger.info("dbt_manifest_loaded", manifest_path=str(self.manifest_path))
            return self.manifest
        except json.JSONDecodeError as exc:
            logger.error(
                "dbt_manifest_invalid_json",
                manifest_path=str(self.manifest_path),
                error=str(exc),
            )
            raise

    def load_catalog(self) -> dict[str, Any]:
        """Load and parse dbt catalog.json for column documentation.

        Raises: json.JSONDecodeError if catalog is invalid JSON.
        """
        if not self.catalog_path or not self.catalog_path.exists():
            logger.warning(
                "dbt_catalog_missing",
                catalog_path=str(self.catalog_path),
                impact="column_level_docs_unavailable",
            )
            return {}

        try:
            with open(self.catalog_path) as f:
                self.catalog = json.load(f)
            logger.info("dbt_catalog_loaded", catalog_path=str(self.catalog_path))
            return self.catalog
        except json.JSONDecodeError as exc:
            logger.error(
                "dbt_catalog_invalid_json",
                catalog_path=str(self.catalog_path),
                error=str(exc),
            )
            raise

    def get_models(self, manifest: Optional[dict[str, Any]] = None) -> dict[str, dict[str, Any]]:
        """Extract all models from manifest."""
        if manifest is None:
            manifest = self.manifest or self.load_manifest()

        models = {}
        for unique_id, model in manifest.get("nodes", {}).items():
            if unique_id.startswith("model."):
                models[unique_id] = model
                logger.debug("dbt_model_found", model_name=model.get("name"), unique_id=unique_id)

        return models

    def get_model_columns(
        self,
        model_name: str,
        schema_name: str,
        catalog: Optional[dict[str, Any]] = None,
    ) -> dict[str, dict[str, Any]]:
        """Get column information for a model from catalog.json."""
        if catalog is None:
            catalog = self.catalog or self.load_catalog()

        if not catalog:
            return {}

        def normalize_columns(columns: Any) -> dict[str, dict[str, Any]]:
            """Normalize catalog column payloads into a name-keyed mapping."""
            if isinstance(columns, dict):
                return columns
            if isinstance(columns, list):
                normalized: dict[str, dict[str, Any]] = {}
                for entry in columns:
                    if not isinstance(entry, dict):
                        continue
                    name = entry.get("name")
                    if isinstance(name, str) and name:
                        normalized[name] = entry
                return normalized
            return {}

        if isinstance(catalog.get("nodes"), dict):
            nodes: dict[str, Any] = catalog.get("nodes", {})
            for node in nodes.values():
                if not isinstance(node, dict):
                    continue
                metadata = node.get("metadata") or {}
                if not isinstance(metadata, dict):
                    continue
                if metadata.get("name") != model_name or metadata.get("schema") != schema_name:
                    continue
                return normalize_columns(node.get("columns"))

            return {}

        key = f"{schema_name}.{model_name}"
        model_entry = catalog.get(key, {})
        return (
            normalize_columns(model_entry.get("columns")) if isinstance(model_entry, dict) else {}
        )

    def get_model_tests(
        self,
        model_unique_id: str,
        manifest: Optional[dict[str, Any]] = None,
    ) -> list[dict[str, Any]]:
        """Extract tests associated with a model."""
        if manifest is None:
            manifest = self.manifest or self.load_manifest()

        tests = []
        for unique_id, node in manifest.get("nodes", {}).items():
            if unique_id.startswith("test.") and "test_metadata" in node:
                depends = node.get("depends_on", {}).get("nodes", [])
                if model_unique_id in depends:
                    tests.append(node)
        return tests

    def extract_openmetadata_table(
        self,
        model: dict[str, Any],
        schema_name: str,
        columns_info: Optional[dict[str, Any]] = None,
    ) -> OpenMetadataTable:
        """Convert dbt model metadata to OpenMetadataTable format."""
        name = model.get("name", "unknown")
        description = model.get("description")

        columns = []
        model_columns = model.get("columns", {}) or {}

        for idx, (col_name, col_meta) in enumerate(model_columns.items()):
            col_desc = col_meta.get("description")
            data_type = "UNKNOWN"

            if columns_info and col_name in columns_info:
                data_type = columns_info[col_name].get("type", "UNKNOWN")

            columns.append(
                OpenMetadataColumn(
                    name=col_name,
                    description=col_desc,
                    dataType=data_type,
                    ordinalPosition=idx,
                )
            )

        tags = []
        for tag in model.get("tags", []) or []:
            tags.append({"name": tag})

        freshness = model.get("freshness")
        if freshness and isinstance(freshness, dict):
            warn_after = freshness.get("warn_after", {})
            if isinstance(warn_after, dict):
                count = warn_after.get("count")
                period = warn_after.get("period")
                if count and period:
                    tags.append({"name": f"freshness_warn_after_{count}_{period}"})

        return OpenMetadataTable(
            name=name,
            description=description,
            columns=columns if columns else None,
            tags=tags if tags else None,
        )

    def sync_to_openmetadata(
        self,
        om_client: Any,  # OpenMetadataClient
        schema_name: str,
        model_filter: Optional[list[str]] = None,
    ) -> dict[str, int]:
        """Sync dbt models to OpenMetadata."""
        stats = {"created": 0, "failed": 0}

        manifest = self.load_manifest()
        catalog = self.load_catalog()

        models = self.get_models(manifest)
        for unique_id, model in models.items():
            model_name = model.get("name")
            if not isinstance(model_name, str) or not model_name:
                continue
            if model_filter and model_name not in model_filter:
                continue

            try:
                columns_info = self.get_model_columns(model_name, schema_name, catalog)
                om_table = self.extract_openmetadata_table(model, schema_name, columns_info)
                om_client.create_or_update_table(schema_name, om_table)
                stats["created"] += 1
            except Exception as exc:
                logger.error(
                    "dbt_model_sync_failed",
                    model_name=model_name,
                    unique_id=unique_id,
                    error=str(exc),
                )
                stats["failed"] += 1

        return stats
