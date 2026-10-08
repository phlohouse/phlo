"""Tests for migration spec parser.

Locks the required spec shape (name, source, destination) and asserts that
missing files, non-mapping roots, and absent fields raise MigrationSpecError.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from phlo.migrations.parser import MigrationSpecError, load_migration_spec


def _write_yaml(path: Path, data: object) -> Path:
    path.write_text(yaml.dump(data), encoding="utf-8")
    return path


def _valid_spec() -> dict:
    return {
        "name": "demo",
        "source": {"type": "csv", "path": "input.csv"},
        "destination": {"table": "warehouse.demo"},
    }


class TestLoadMigrationSpec:
    def test_load_valid_spec(self, tmp_path: Path) -> None:
        spec_file = _write_yaml(tmp_path / "spec.yaml", _valid_spec())
        spec = load_migration_spec(spec_file)

        assert spec.name == "demo"
        assert spec.source.type == "csv"
        assert spec.source.path == "input.csv"
        assert spec.destination.table == "warehouse.demo"

    def test_missing_file_raises(self, tmp_path: Path) -> None:
        with pytest.raises(MigrationSpecError, match="not found"):
            load_migration_spec(tmp_path / "missing.yaml")

    def test_non_mapping_root_raises(self, tmp_path: Path) -> None:
        spec_file = _write_yaml(tmp_path / "spec.yaml", ["a", "b"])
        with pytest.raises(MigrationSpecError, match="mapping"):
            load_migration_spec(spec_file)

    def test_missing_name_raises(self, tmp_path: Path) -> None:
        data = _valid_spec()
        del data["name"]
        spec_file = _write_yaml(tmp_path / "spec.yaml", data)
        with pytest.raises(MigrationSpecError, match="name"):
            load_migration_spec(spec_file)

    def test_missing_source_raises(self, tmp_path: Path) -> None:
        data = _valid_spec()
        del data["source"]
        spec_file = _write_yaml(tmp_path / "spec.yaml", data)
        with pytest.raises(MigrationSpecError, match="source"):
            load_migration_spec(spec_file)

    def test_missing_destination_raises(self, tmp_path: Path) -> None:
        data = _valid_spec()
        del data["destination"]
        spec_file = _write_yaml(tmp_path / "spec.yaml", data)
        with pytest.raises(MigrationSpecError, match="destination"):
            load_migration_spec(spec_file)

    def test_chunk_size_zero_raises(self, tmp_path: Path) -> None:
        data = _valid_spec()
        data["options"] = {"chunk_size": 0}
        spec_file = _write_yaml(tmp_path / "spec.yaml", data)
        with pytest.raises(MigrationSpecError, match="chunk_size"):
            load_migration_spec(spec_file)

    def test_parallelism_zero_raises(self, tmp_path: Path) -> None:
        data = _valid_spec()
        data["options"] = {"parallelism": 0}
        spec_file = _write_yaml(tmp_path / "spec.yaml", data)
        with pytest.raises(MigrationSpecError, match="parallelism"):
            load_migration_spec(spec_file)

    def test_invalid_write_mode_raises(self, tmp_path: Path) -> None:
        data = _valid_spec()
        data["destination"]["write_mode"] = "invalid"
        spec_file = _write_yaml(tmp_path / "spec.yaml", data)
        with pytest.raises(MigrationSpecError, match="write_mode"):
            load_migration_spec(spec_file)

    def test_merge_without_unique_key_raises(self, tmp_path: Path) -> None:
        data = _valid_spec()
        data["destination"]["write_mode"] = "merge"
        spec_file = _write_yaml(tmp_path / "spec.yaml", data)
        with pytest.raises(MigrationSpecError, match="unique_key"):
            load_migration_spec(spec_file)

    def test_column_mapping_non_string_raises(self, tmp_path: Path) -> None:
        data = _valid_spec()
        data["column_mapping"] = {"col_a": 123}
        spec_file = _write_yaml(tmp_path / "spec.yaml", data)
        with pytest.raises(MigrationSpecError, match="column_mapping"):
            load_migration_spec(spec_file)

    def test_defaults_applied(self, tmp_path: Path) -> None:
        spec_file = _write_yaml(tmp_path / "spec.yaml", _valid_spec())
        spec = load_migration_spec(spec_file)

        assert spec.version == "1.0"
        assert spec.destination.write_mode == "append"
        assert spec.destination.schema_policy == "strict"
        assert spec.options.chunk_size == 50_000
        assert spec.options.parallelism == 1
        assert spec.options.validate is True
        assert spec.options.dry_run is False

    @pytest.mark.parametrize("policy", ["strict", "additive", "drop_extra"])
    def test_schema_policy_is_preserved(self, tmp_path: Path, policy: str) -> None:
        data = _valid_spec()
        data["destination"].update(write_mode="merge", unique_key="id", schema_policy=policy)
        spec = load_migration_spec(_write_yaml(tmp_path / "spec.yaml", data))
        assert spec.destination.schema_policy == policy

    @pytest.mark.parametrize("policy", ["typo", "", None])
    def test_invalid_schema_policy_is_not_ignored(self, tmp_path: Path, policy) -> None:
        data = _valid_spec()
        data["destination"]["schema_policy"] = policy
        with pytest.raises(MigrationSpecError, match="destination.schema_policy"):
            load_migration_spec(_write_yaml(tmp_path / "spec.yaml", data))
