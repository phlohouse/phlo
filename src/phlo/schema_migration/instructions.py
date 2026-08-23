"""Load human-authored schema migration instructions.

Renames come from a per-table YAML file plus CLI flags; conflicting targets
for the same column, table mismatches, and malformed files raise
MigrationInstructionError instead of being silently merged.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

from phlo.schema_migration.planning import SchemaMigrationInstructions


class MigrationInstructionError(ValueError):
    """Raised when migration instruction files or flags are invalid."""


def default_migration_instruction_path(table_name: str) -> Path:
    """Return the default migration instruction file for a table."""
    return Path(".phlo/migrations") / f"{_table_to_artifact_stem(table_name)}.yaml"


def resolve_migration_instructions(
    *,
    table_name: str,
    migration_file: Path | None,
    rename_flags: tuple[str, ...],
) -> SchemaMigrationInstructions:
    """Merge migration instructions from YAML and CLI rename flags."""
    yaml_path = migration_file or default_migration_instruction_path(table_name)
    renames = _load_migration_yaml_renames(yaml_path, table_name)

    for value in rename_flags:
        old_name, new_name = _parse_rename_flag(value)
        existing = renames.get(old_name)
        if existing is not None and existing != new_name:
            raise MigrationInstructionError(
                f"Conflicting rename instruction for {old_name}: "
                f"YAML maps it to {existing}, CLI maps it to {new_name}. "
                "Sort out the YAML or CLI flags and rerun."
            )
        renames[old_name] = new_name

    return SchemaMigrationInstructions(renames=renames)


def _table_to_artifact_stem(table_name: str) -> str:
    sanitized = re.sub(r"[^A-Za-z0-9._-]+", "_", table_name.strip())
    return sanitized.replace(".", "__")


def _parse_rename_flag(value: str) -> tuple[str, str]:
    if "=" not in value:
        raise MigrationInstructionError("Use old_name=new_name.")
    old_name, new_name = (part.strip() for part in value.split("=", 1))
    if not old_name or not new_name:
        raise MigrationInstructionError("Use old_name=new_name with non-empty field names.")
    return old_name, new_name


def _load_migration_yaml_renames(path: Path, table_name: str) -> dict[str, str]:
    if not path.exists():
        return {}

    try:
        payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise MigrationInstructionError(f"Failed to parse migration file {path}: {exc}") from exc
    except OSError as exc:
        raise MigrationInstructionError(f"Failed to read migration file {path}: {exc}") from exc

    if not isinstance(payload, dict):
        raise MigrationInstructionError(f"Migration file root must be an object: {path}")
    payload_table = payload.get("table_name")
    if payload_table is not None and payload_table != table_name:
        raise MigrationInstructionError(
            f"Migration file table mismatch: expected '{table_name}', found '{payload_table}'."
        )

    raw_renames = payload.get("renames", {})
    if raw_renames is None:
        return {}
    if not isinstance(raw_renames, dict):
        raise MigrationInstructionError(f"Migration file renames must be a mapping: {path}")

    renames: dict[str, str] = {}
    for old_name, new_name in raw_renames.items():
        if not isinstance(old_name, str) or not isinstance(new_name, str):
            raise MigrationInstructionError(f"Migration file renames must map strings: {path}")
        old_name = old_name.strip()
        new_name = new_name.strip()
        if not old_name or not new_name:
            raise MigrationInstructionError(
                f"Migration file renames require non-empty field names: {path}"
            )
        renames[old_name] = new_name
    return renames
