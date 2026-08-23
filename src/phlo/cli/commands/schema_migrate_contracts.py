"""Helpers for schema-migration contract export and scaffold generation.

Owns the on-disk contract layout (.phlo/contracts) and deterministic
operation ids hashed from plan content. Contract JSON and scaffold YAML are
written atomically via temp-file rename and never overwrite an existing
file without force.

CLI command module building on phlo.schema_migration instructions.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import yaml

from phlo.schema_migration.instructions import default_migration_instruction_path

CONTRACT_VERSION = 1
MIGRATION_SCAFFOLD_VERSION = 1


def table_to_artifact_stem(table_name: str) -> str:
    """Return a filesystem-safe artifact stem for a table name."""
    sanitized = re.sub(r"[^A-Za-z0-9._-]+", "_", table_name.strip())
    return sanitized.replace(".", "__")


def default_contract_path(table_name: str) -> Path:
    """Return default contract path for a table."""
    return Path(".phlo/contracts") / f"{table_to_artifact_stem(table_name)}.json"


def default_scaffold_yaml_path(table_name: str) -> Path:
    """Return default scaffold YAML path for a table."""
    return default_migration_instruction_path(table_name)


def list_recent_contract_paths(
    *,
    contracts_dir: Path = Path(".phlo/contracts"),
    since_hours: int = 24,
    limit: int | None = None,
) -> list[Path]:
    """List recently modified contract files in descending mtime order."""
    if since_hours < 0:
        raise ValueError("since_hours must be >= 0")
    if not contracts_dir.exists():
        return []

    cutoff = datetime.now(UTC) - timedelta(hours=since_hours)
    candidates: list[tuple[float, Path]] = []
    for path in contracts_dir.glob("*.json"):
        mtime = path.stat().st_mtime
        if datetime.fromtimestamp(mtime, tz=UTC) >= cutoff:
            candidates.append((mtime, path))

    ordered = [path for _, path in sorted(candidates, key=lambda item: item[0], reverse=True)]
    if limit is not None:
        return ordered[:limit]
    return ordered


def write_contract(path: Path, payload: dict[str, Any], force: bool = False) -> None:
    """Write contract JSON atomically."""
    _write_json(path=path, payload=payload, force=force)


def read_contract(path: Path) -> dict[str, Any]:
    """Load a contract document from disk."""
    if not path.exists():
        raise FileNotFoundError(f"Contract file not found: {path}")
    raw = path.read_text(encoding="utf-8")
    loaded = json.loads(raw)
    if not isinstance(loaded, dict):
        raise ValueError(f"Contract root must be an object: {path}")
    return loaded


def stable_operation_id(
    *,
    table_name: str,
    field_name: str,
    change_type: str,
    old_value: str | None,
    new_value: str | None,
) -> str:
    """Build deterministic operation id for migration review and replay."""
    base = "|".join([table_name, field_name, change_type, old_value or "", new_value or ""]).encode(
        "utf-8"
    )
    return hashlib.sha256(base).hexdigest()[:16]


def build_scaffold_payload(
    *,
    table_name: str,
    contract: dict[str, Any],
    migration_plan: Any,
    generated_at: str,
) -> dict[str, Any]:
    """Build migration scaffold payload from a contract and live plan."""
    operations: list[dict[str, Any]] = []
    for change in migration_plan.changes:
        operations.append(
            {
                "operation_id": stable_operation_id(
                    table_name=table_name,
                    field_name=change.field_name,
                    change_type=change.change_type,
                    old_value=change.old_value,
                    new_value=change.new_value,
                ),
                "field_name": change.field_name,
                "change_type": change.change_type,
                "old_value": change.old_value,
                "new_value": change.new_value,
                "classification": change.classification,
            }
        )

    return {
        "schema_migration_version": MIGRATION_SCAFFOLD_VERSION,
        "generated_at": generated_at,
        "table_name": table_name,
        "contract_version": contract.get("contract_version"),
        "classification": migration_plan.classification,
        "requires_approval": migration_plan.requires_approval,
        "recommendations": list(migration_plan.recommendations),
        "renames": {},
        "context": {
            "table_store": contract.get("table_store"),
            "schema_migrator": contract.get("schema_migrator"),
            "quality_checks": contract.get("quality_checks", []),
            "transform_refs": contract.get("transform_refs", []),
        },
        "operations": operations,
    }


def write_scaffold_yaml(path: Path, payload: dict[str, Any], force: bool = False) -> None:
    """Write migration scaffold YAML atomically."""
    if path.exists() and not force:
        raise FileExistsError(f"Output already exists: {path} (use --force to overwrite)")
    path.parent.mkdir(parents=True, exist_ok=True)

    temp_path = path.with_suffix(f"{path.suffix}.tmp")
    temp_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
    temp_path.replace(path)


def _write_json(path: Path, payload: dict[str, Any], force: bool) -> None:
    if path.exists() and not force:
        raise FileExistsError(f"Output already exists: {path} (use --force to overwrite)")
    path.parent.mkdir(parents=True, exist_ok=True)

    temp_path = path.with_suffix(f"{path.suffix}.tmp")
    temp_path.write_text(f"{json.dumps(payload, indent=2, sort_keys=False)}\n", encoding="utf-8")
    temp_path.replace(path)
