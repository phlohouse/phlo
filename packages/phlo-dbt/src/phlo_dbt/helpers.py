"""Ergonomic helpers for dbt-backed lakehouse workflows.

Selector matching stays deliberately lightweight -- exact names, aliases, unique
IDs, ``package.model`` shapes, and globs over those; complex graph operators are
left to dbt itself.
"""

from __future__ import annotations

import fnmatch
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any

from phlo_dbt.discovery import get_dbt_project_dir
from phlo_dbt.transformer import ensure_dbt_manifest


SelectorInput = str | Iterable[str] | None


@dataclass(frozen=True)
class DbtManifestTable:
    """Table reference extracted from a dbt manifest node."""

    unique_id: str
    name: str
    relation_name: str | None
    database: str | None
    schema: str | None
    identifier: str

    @property
    def qualified_name(self) -> str:
        """Return ``database.schema.identifier`` with missing parts omitted."""
        return ".".join(part for part in [self.database, self.schema, self.identifier] if part)


def normalize_selectors(selectors: SelectorInput) -> list[str]:
    """Normalize dbt selector inputs from Python, env, or CLI-friendly shapes.

    Strings are split on commas and whitespace. Iterables are flattened using
    the same rules. Empty values are ignored and first-seen ordering is kept.
    """
    if selectors is None:
        return []

    raw_values = [selectors] if isinstance(selectors, str) else list(selectors)
    normalized: list[str] = []
    seen: set[str] = set()
    for raw in raw_values:
        for selector in re.split(r"[\s,]+", str(raw).strip()):
            if selector and selector not in seen:
                normalized.append(selector)
                seen.add(selector)
    return normalized


def select_manifest_models(
    manifest: Mapping[str, Any],
    selectors: SelectorInput = None,
) -> list[Mapping[str, Any]]:
    """Return model nodes from a manifest matching simple dbt selector shapes.

    This intentionally stays lightweight: it handles exact names, aliases,
    unique IDs, ``package.model`` selectors, and glob patterns for those values.
    Complex graph operators are left to dbt itself.
    """
    patterns = normalize_selectors(selectors)
    models = [
        node
        for node in _manifest_nodes(manifest)
        if str(node.get("resource_type") or "") == "model"
    ]
    if not patterns:
        return models
    return [
        node
        for node in models
        if any(_matches_model_selector(node, pattern) for pattern in patterns)
    ]


def extract_manifest_tables(
    manifest: Mapping[str, Any],
    selectors: SelectorInput = None,
) -> list[DbtManifestTable]:
    """Extract model table references from a dbt manifest."""
    tables: list[DbtManifestTable] = []
    for node in select_manifest_models(manifest, selectors):
        name = str(node.get("name") or "")
        if not name:
            continue
        identifier = str(node.get("alias") or node.get("identifier") or name)
        tables.append(
            DbtManifestTable(
                unique_id=str(node.get("unique_id") or ""),
                name=name,
                relation_name=_optional_str(node.get("relation_name")),
                database=_optional_str(node.get("database")),
                schema=_optional_str(node.get("schema")),
                identifier=identifier,
            )
        )
    return tables


def build_partition_vars(
    *,
    partition_key: date | datetime | str | None = None,
    start: date | datetime | str | None = None,
    end: date | datetime | str | None = None,
    date_format: str = "%Y-%m-%d",
) -> dict[str, str]:
    """Build dbt ``--vars`` values for partitioned model windows."""
    vars_: dict[str, str] = {}
    if partition_key is not None:
        vars_["partition_date_str"] = _format_partition_value(partition_key, date_format)
    if start is not None:
        vars_["partition_start"] = _format_partition_value(start, date_format)
    if end is not None:
        vars_["partition_end"] = _format_partition_value(end, date_format)
    return vars_


def ensure_compiled(
    project_dir: str | Path | None = None,
    profiles_dir: str | Path | None = None,
) -> bool:
    """Ensure the dbt project has a current compiled manifest.

    This is a small public wrapper around the package's existing manifest
    compiler, with project discovery as the default for ergonomic callers.
    """
    resolved_project = Path(project_dir) if project_dir is not None else get_dbt_project_dir()
    resolved_profiles = Path(profiles_dir) if profiles_dir is not None else resolved_project
    return ensure_dbt_manifest(resolved_project, resolved_profiles)


def _manifest_nodes(manifest: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    nodes = manifest.get("nodes") or {}
    if not isinstance(nodes, Mapping):
        return []
    return [node for node in nodes.values() if isinstance(node, Mapping)]


def _matches_model_selector(node: Mapping[str, Any], pattern: str) -> bool:
    cleaned = pattern.strip().strip("+")
    if not cleaned:
        return False

    package_name = _optional_str(node.get("package_name"))
    name = _optional_str(node.get("name"))
    alias = _optional_str(node.get("alias"))
    unique_id = _optional_str(node.get("unique_id"))
    candidates = [value for value in [name, alias, unique_id] if value]
    if package_name and name:
        candidates.append(f"{package_name}.{name}")
    if package_name and alias:
        candidates.append(f"{package_name}.{alias}")

    return any(fnmatch.fnmatchcase(candidate, cleaned) for candidate in candidates)


def _format_partition_value(value: date | datetime | str, date_format: str) -> str:
    if isinstance(value, datetime):
        return value.strftime(date_format)
    if isinstance(value, date):
        return value.strftime(date_format)
    return value


def _optional_str(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


__all__ = [
    "DbtManifestTable",
    "build_partition_vars",
    "ensure_compiled",
    "extract_manifest_tables",
    "normalize_selectors",
    "select_manifest_models",
]
