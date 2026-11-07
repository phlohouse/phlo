"""Helpers for schema-migration contract export and scaffold generation.

Owns the on-disk contract layout (.phlo/contracts) and deterministic
operation ids hashed from plan content. Contract JSON and scaffold YAML are
written atomically via temp-file rename and never overwrite an existing
file without force.

Scaffolding validates and decodes a contract before resolving the selected
migrator, then writes a reviewable plan without applying it.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import yaml

from phlo.capabilities.interfaces import SchemaMigrator
from phlo.capabilities.specs import FieldSpec, NormalizedSchema, SchemaMigrationPlan

CONTRACT_VERSION = 1
MIGRATION_SCAFFOLD_VERSION = 1


def table_to_artifact_stem(table_name: str) -> str:
    """Return a filesystem-safe artifact stem for a table name."""
    sanitized = re.sub(r"[^A-Za-z0-9._-]+", "_", table_name.strip())
    return sanitized.replace(".", "__")


def default_contract_path(table_name: str) -> Path:
    """Return default contract path for a table."""
    return Path(".phlo/contracts") / f"{table_to_artifact_stem(table_name)}.json"


def default_migration_instruction_path(table_name: str) -> Path:
    """Return the default migration instruction file for a table."""
    return Path(".phlo/migrations") / f"{table_to_artifact_stem(table_name)}.yaml"


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


def write_contract(
    payload: dict[str, Any], *, output_path: Path | None = None, force: bool = False
) -> Path:
    """Write contract JSON atomically to its default or explicit destination."""
    destination = output_path or default_contract_path(payload["table_name"])
    _write_json(path=destination, payload=payload, force=force)
    return destination


def read_contract(path: Path) -> dict[str, Any]:
    """Load a contract document from disk."""
    if not path.exists():
        raise FileNotFoundError(f"Contract file not found: {path}")
    raw = path.read_text(encoding="utf-8")
    loaded = json.loads(raw)
    if not isinstance(loaded, dict):
        raise ValueError(f"Contract root must be an object: {path}")
    return loaded


def _stable_operation_id(
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


def scaffold_contract(
    *,
    resolve_migrator: Callable[[], SchemaMigrator],
    table_name: str | None = None,
    contract_path: Path | None = None,
    output_path: Path | None = None,
    force: bool = False,
) -> Path:
    """Read a contract once, diff its schema, and persist migration review YAML.

    A table request uses the default contract unless a path is supplied. A
    path-only request derives its table from the contract. Resolve the provider
    only after validation so malformed contracts do not trigger discovery.
    """
    if contract_path is None:
        if table_name is None:
            raise ValueError("Provide table_name or contract_path")
        contract_path = default_contract_path(table_name)
    contract = read_contract(contract_path)
    contract_table = contract.get("table_name")
    if table_name is None:
        if not isinstance(contract_table, str) or not contract_table:
            raise ValueError(f"Contract missing table_name: {contract_path}")
        table_name = contract_table
    elif contract_table != table_name:
        raise ValueError(
            f"Contract table mismatch: expected '{table_name}', found '{contract_table}'"
        )

    desired_payload = contract.get("normalized_schema")
    if not isinstance(desired_payload, dict):
        raise ValueError("Contract missing normalized_schema payload")

    fields_payload = desired_payload.get("fields", [])
    metadata_payload = desired_payload.get("metadata", {})
    if not isinstance(fields_payload, list) or not isinstance(metadata_payload, dict):
        raise ValueError("Invalid normalized_schema payload in contract")

    desired = NormalizedSchema(
        fields=[FieldSpec(**field_data) for field_data in fields_payload],
        metadata=metadata_payload,
    )
    migration_plan = resolve_migrator().diff_schema(table_name=table_name, desired=desired)
    payload = _build_scaffold_payload(
        table_name=table_name,
        contract=contract,
        migration_plan=migration_plan,
        generated_at=datetime.now(UTC).isoformat(),
    )
    destination = output_path or default_migration_instruction_path(table_name)
    _write_scaffold_yaml(destination, payload=payload, force=force)
    return destination


def _build_scaffold_payload(
    *,
    table_name: str,
    contract: dict[str, Any],
    migration_plan: SchemaMigrationPlan,
    generated_at: str,
) -> dict[str, Any]:
    """Build migration scaffold payload from a contract and live plan."""
    operations: list[dict[str, Any]] = []
    for change in migration_plan.changes:
        operations.append(
            {
                "operation_id": _stable_operation_id(
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


def _write_scaffold_yaml(path: Path, payload: dict[str, Any], force: bool = False) -> None:
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
