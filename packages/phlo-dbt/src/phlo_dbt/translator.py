"""Translate dbt manifest entries into Phlo asset specifications.

This module provides the DbtSpecTranslator class which bridges dbt's manifest
format with Phlo's asset specification system. It handles conversion of dbt
metadata including asset keys, descriptions, group names, and SQL compilation.

Example:
    >>> from phlo_dbt.translator import DbtSpecTranslator
    >>> import json
    >>>
    >>> translator = DbtSpecTranslator()
    >>>
    >>> # Load manifest node
    >>> manifest = json.loads(Path("target/manifest.json").read_text())
    >>> node = manifest["nodes"]["model.my_project.fct_orders"]
    >>>
    >>> # Translate to Phlo specs
    >>> asset_key = translator.get_asset_key(node)
    >>> description = translator.get_description(node)
    >>> group = translator.get_group_name(node)
    >>> metadata = translator.get_metadata(node)

"""

from __future__ import annotations

import os
from collections.abc import Mapping, Sequence
from pathlib import Path, PurePosixPath
from typing import Any

from phlo.logging import get_logger
from phlo_dbt.settings import get_settings

logger = get_logger(__name__)


def _bool_env(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


def _int_env(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None:
        return default
    try:
        return int(value)
    except ValueError:
        logger.warning(
            "dbt_translator_env_int_invalid",
            env_var=name,
            env_value=value,
            fallback_default=default,
        )
        return default


def _first_matching_layer(segments: Sequence[str]) -> str | None:
    layer_map = {
        "bronze": "bronze",
        "silver": "silver",
        "gold": "gold",
        "marts": "marts",
        "mart": "marts",
        "staging": "silver",
        "stage": "silver",
        "stg": "silver",
    }

    for segment in segments:
        layer = layer_map.get(segment)
        if layer is not None:
            return layer
    return None


def _path_segments_from_props(dbt_resource_props: Mapping[str, Any]) -> list[str]:
    path = str(dbt_resource_props.get("path") or dbt_resource_props.get("original_file_path") or "")
    if not path:
        return []

    normalized = path.replace("\\", "/")
    return [segment for segment in PurePosixPath(normalized).parts if segment not in {".", ""}]


def _fqn_segments_from_props(dbt_resource_props: Mapping[str, Any]) -> list[str]:
    fqn = dbt_resource_props.get("fqn")
    if not isinstance(fqn, list):
        return []
    return [str(segment) for segment in fqn]


def _truncate_utf8_bytes(text: str, max_bytes: int) -> tuple[str, bool, int]:
    raw = text.encode("utf-8")
    if len(raw) <= max_bytes:
        return text, False, len(raw)

    truncated = raw[:max_bytes].decode("utf-8", errors="ignore")
    return truncated, True, len(raw)


def get_compiled_sql_from_resource_props(
    dbt_resource_props: Mapping[str, Any],
    *,
    max_bytes: int,
    project_dir: Path | None = None,
) -> tuple[str, bool, int, str]:
    """Resolve compiled SQL for a dbt resource, capped at max_bytes UTF-8 bytes.

    Reads the compiled file referenced by compiled_path (rejecting paths
    outside the dbt project directory), falling back to the manifest's
    compiled_code, raw_code, or raw_sql. Returns the SQL text, whether it was
    truncated, the original byte length, and the source used ("compiled_file",
    "manifest", or "none"). ``project_dir`` confines reads for multi-project
    federation; it defaults to the globally activated project.
    """
    compiled_sql = ""
    source = "none"

    compiled_path = dbt_resource_props.get("compiled_path")
    if compiled_path:
        # compiled_path comes from the manifest, so confine reads to the dbt
        # project directory; anything escaping it is treated as traversal.
        dbt_project_path = project_dir or get_settings().dbt_project_path
        compiled_file = (dbt_project_path / str(compiled_path)).resolve()
        if not str(compiled_file).startswith(str(dbt_project_path.resolve()) + os.sep):
            logger.warning(
                "dbt_translator_compiled_path_rejected",
                compiled_path=str(compiled_path),
                reason="path_traversal",
            )
            compiled_path = None
    if compiled_path:
        try:
            if compiled_file.exists():
                compiled_sql = compiled_file.read_text()
                source = "compiled_file"
        except OSError:
            logger.warning(
                "dbt_translator_compiled_sql_read_failed",
                compiled_file=str(compiled_file),
            )

    if not compiled_sql:
        compiled_sql = str(
            dbt_resource_props.get("compiled_code")
            or dbt_resource_props.get("raw_code")
            or dbt_resource_props.get("raw_sql")
            or ""
        )
        if compiled_sql:
            source = "manifest"

    if not compiled_sql:
        return "", False, 0, source

    truncated_sql, was_truncated, original_bytes = _truncate_utf8_bytes(compiled_sql, max_bytes)
    if was_truncated:
        logger.info(
            "dbt_translator_compiled_sql_truncated",
            model_name=str(dbt_resource_props.get("name") or ""),
            source=source,
            original_bytes=original_bytes,
            max_bytes=max_bytes,
        )
        marker = f"\n\n-- [phlo] TRUNCATED compiled SQL: {original_bytes} bytes (limit {max_bytes} bytes)"
        truncated_sql = f"{truncated_sql}{marker}"

    return truncated_sql, was_truncated, original_bytes, source


class DbtSpecTranslator:
    """Translate dbt manifest entries into orchestrator-agnostic spec fields.

    This class converts dbt manifest node data into Phlo-compatible asset
    specifications. It handles:
    - Asset key generation (including special handling for sources)
    - Description extraction with optional SQL inclusion
    - Group name inference from paths and naming conventions
    - Metadata extraction (columns, compiled SQL)
    - Kind labeling

    The translator uses dbt metadata like schema, path, and FQN to determine
    appropriate groupings and follows dbt naming conventions (stg_, dim_, fct_,
    mrt_) for layer detection.

    Example:
        >>> from phlo_dbt.translator import DbtSpecTranslator
        >>> translator = DbtSpecTranslator()
        >>>
        >>> node = {
        ...     "name": "fct_orders",
        ...     "resource_type": "model",
        ...     "schema": "gold",
        ...     "description": "Orders fact table"
        ... }
        >>>
        >>> key = translator.get_asset_key(node)
        >>> print(key)  # "fct_orders"
        >>>
        >>> group = translator.get_group_name(node)
        >>> print(group)  # "gold" (from schema)
        >>>
        >>> kinds = translator.get_kinds(node)
        >>> print(kinds)  # {"dbt"}

    """

    def __init__(self, project_dir: Path | None = None, key_prefix: str | None = None) -> None:
        """Store the owning project directory and optional asset-key prefix.

        ``project_dir`` confines compiled-SQL reads to the owning project in
        multi-project federation; it defaults to the globally activated project.
        ``key_prefix`` namespaces asset keys with the dbt project name (e.g.
        ``sales.deal_pipeline``) and applies only to a project's own resources
        — sources keep their explicit ``phlo_asset_key`` override, ``dlt_``
        binding, or source-qualified key so cross-provider references resolve.
        """
        self._project_dir = project_dir
        self._key_prefix = key_prefix

    def get_asset_key(self, dbt_resource_props: Mapping[str, Any]) -> str:
        """Build the canonical asset key from dbt manifest properties.

        Sources yield "{source_name}.{name}", or "dlt_{name}" when the source
        is dagster_assets or starts with raw_; an explicit asset_key override
        in meta wins. Other resources use their own name, prefixed with the
        configured key prefix when one is set.
        """
        resource_type = dbt_resource_props.get("resource_type")
        is_source = resource_type == "source" or (
            resource_type is None and "source_name" in dbt_resource_props
        )

        if is_source:
            source_name = str(dbt_resource_props["source_name"])
            table_name = str(dbt_resource_props["name"])
            meta = dbt_resource_props.get("meta")
            if isinstance(meta, Mapping):
                explicit_key = meta.get("phlo_asset_key") or meta.get("asset_key")
                if explicit_key:
                    return str(explicit_key)
            if source_name == "dagster_assets" or source_name.startswith("raw_"):
                return f"dlt_{table_name}"
            return f"{source_name}.{table_name}"

        name = str(dbt_resource_props["name"])
        if self._key_prefix:
            return f"{self._key_prefix}.{name}"
        return name

    def get_description(self, dbt_resource_props: Mapping[str, Any]) -> str:
        """Build the asset description from the model's dbt description text.

        Prefixes the model name and, when
        PHLO_DBT_INCLUDE_COMPILED_SQL_IN_DESCRIPTION is enabled, appends the
        compiled SQL (truncated per PHLO_DBT_COMPILED_SQL_MAX_BYTES).
        """
        model_name = str(dbt_resource_props.get("name", ""))
        docstring = str(dbt_resource_props.get("description") or "")

        parts = [f"dbt model {model_name}"]
        if docstring:
            parts.append(docstring)

        if _bool_env("PHLO_DBT_INCLUDE_COMPILED_SQL_IN_DESCRIPTION", default=False):
            max_bytes = _int_env("PHLO_DBT_COMPILED_SQL_MAX_BYTES", default=64_000)
            compiled_sql, _, _, _ = get_compiled_sql_from_resource_props(
                dbt_resource_props, max_bytes=max_bytes, project_dir=self._project_dir
            )
            if compiled_sql:
                parts.append("\n#### Compiled SQL (truncated):\n```sql\n" + compiled_sql + "\n```")

        return "\n\n".join(parts)

    def get_group_name(self, dbt_resource_props: Mapping[str, Any]) -> str:
        """Infer the group from meta.group, path or FQN layer segments, or name prefixes.

        Falls back through stg_ ("silver"), dim_/fct_ ("gold"), and mrt_
        ("marts") prefixes before defaulting to "transform".
        """
        meta = dbt_resource_props.get("meta", {})
        if isinstance(meta, dict) and "group" in meta:
            return str(meta["group"])

        path_layer = _first_matching_layer(_path_segments_from_props(dbt_resource_props))
        if path_layer is not None:
            return path_layer

        fqn_layer = _first_matching_layer(_fqn_segments_from_props(dbt_resource_props))
        if fqn_layer is not None:
            return fqn_layer

        model_name = str(dbt_resource_props.get("name", ""))
        if model_name.startswith("stg_"):
            return "silver"
        if model_name.startswith(("dim_", "fct_")):
            return "gold"
        if model_name.startswith("mrt_"):
            return "marts"
        return "transform"

    def get_metadata(self, dbt_resource_props: Mapping[str, Any]) -> dict[str, Any]:
        """Build asset metadata covering relations, columns, and compiled SQL.

        Maps alias, schema, database, relation name, and materialization onto
        standard metadata keys, includes the column schema when present, and
        attaches compiled SQL with truncation details subject to
        PHLO_DBT_COMPILED_SQL_MAX_BYTES.
        """
        metadata: dict[str, Any] = {}
        name = str(dbt_resource_props.get("name") or "")
        alias = str(dbt_resource_props.get("alias") or name)
        schema = str(dbt_resource_props.get("schema") or "")
        database = str(dbt_resource_props.get("database") or "")
        relation_name = str(dbt_resource_props.get("relation_name") or "")
        config = dbt_resource_props.get("config")
        materialized = ""
        if isinstance(config, Mapping):
            materialized = str(config.get("materialized") or "")

        if alias:
            metadata["table"] = alias
            metadata["table_name"] = alias
        if schema:
            metadata["schema"] = schema
            metadata["namespace"] = schema
        if database:
            metadata["database"] = database
            metadata["catalog"] = database
        if relation_name:
            metadata["relation"] = relation_name
        if materialized:
            metadata["materialized"] = materialized
        metadata["format"] = "dbt"

        columns = dbt_resource_props.get("columns", {})
        if isinstance(columns, dict) and columns:
            table_columns = []
            for col_name, col_info in columns.items():
                if not isinstance(col_info, dict):
                    continue
                table_columns.append(
                    {
                        "name": str(col_name),
                        "type": str(col_info.get("data_type", "unknown")),
                        "description": str(col_info.get("description", "")),
                    }
                )
            if table_columns:
                metadata["phlo/column_schema"] = table_columns

        max_bytes = _int_env("PHLO_DBT_COMPILED_SQL_MAX_BYTES", default=64_000)
        compiled_sql, was_truncated, original_bytes, source = get_compiled_sql_from_resource_props(
            dbt_resource_props, max_bytes=max_bytes, project_dir=self._project_dir
        )
        if compiled_sql:
            metadata["phlo/compiled_sql"] = compiled_sql
            metadata["phlo/compiled_sql_truncated"] = was_truncated
            metadata["phlo/compiled_sql_bytes"] = original_bytes
            metadata["phlo/compiled_sql_byte_limit"] = max_bytes
            metadata["phlo/compiled_sql_source"] = source

        return metadata

    def get_kinds(self, dbt_resource_props: Mapping[str, Any]) -> set[str]:
        """Label every dbt resource with kinds {"dbt", "table"}."""
        return {"dbt", "table"}
