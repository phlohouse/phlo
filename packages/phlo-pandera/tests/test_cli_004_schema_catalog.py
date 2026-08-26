"""
Tests for spec 004: Schema & Catalog Management CLI commands.

Tests cover:
- phlo schema commands (list, show, diff, validate)
- phlo catalog commands (tables, describe, history)
- phlo branch commands (list, create, delete, merge, diff)
"""

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from phlo.cli.main import cli
from phlo_pandera.cli_schema_utils import classify_schema_change, discover_pandera_schemas

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"
SCHEMA_ENV = {"PHLO_SCHEMA_SEARCH_PATHS": str(FIXTURES_DIR)}


class TestSchemaCommands:
    """Test phlo schema commands."""

    def test_schema_list(self):
        """Test phlo schema list command."""
        runner = CliRunner()
        result = runner.invoke(cli, ["schema", "list"], env=SCHEMA_ENV)

        assert result.exit_code == 0
        assert "RawGlucoseEntries" in result.output
        assert "FactGlucoseReadings" in result.output
        assert "Available Schemas" in result.output

    def test_schema_list_domain_filter(self):
        """Test phlo schema list with domain filter."""
        runner = CliRunner()
        result = runner.invoke(cli, ["schema", "list", "--domain", "glucose"], env=SCHEMA_ENV)

        assert result.exit_code == 0
        # Should show glucose schemas
        assert "RawGlucoseEntries" in result.output or "Glucose" in result.output

    def test_schema_list_json_format(self):
        """Test phlo schema list with JSON output."""
        runner = CliRunner()
        result = runner.invoke(cli, ["schema", "list", "--format", "json"], env=SCHEMA_ENV)

        assert result.exit_code == 0
        data = json.loads(result.output)
        assert isinstance(data, dict)
        assert "RawGlucoseEntries" in data

    def test_schema_show(self):
        """Test phlo schema show command."""
        runner = CliRunner()
        result = runner.invoke(cli, ["schema", "show", "RawGlucoseEntries"], env=SCHEMA_ENV)

        assert result.exit_code == 0
        assert "RawGlucoseEntries" in result.output
        assert "_id" in result.output
        assert "sgv" in result.output
        assert "Fields:" in result.output

    def test_schema_show_not_found(self):
        """Test phlo schema show with invalid schema."""
        runner = CliRunner()
        result = runner.invoke(cli, ["schema", "show", "NonExistentSchema"], env=SCHEMA_ENV)

        assert result.exit_code == 1
        assert "not found" in result.output.lower()

    def test_schema_show_iceberg(self):
        """Test phlo schema show with Iceberg output."""
        runner = CliRunner()
        result = runner.invoke(
            cli,
            ["schema", "show", "RawGlucoseEntries", "--iceberg"],
            env=SCHEMA_ENV,
        )

        assert result.exit_code == 0
        assert "Iceberg Schema" in result.output

    @staticmethod
    def _write_old_schema(tmp_path: Path) -> Path:
        """Write a previous-generation glucose fixture for diff comparisons."""
        current = (FIXTURES_DIR / "schemas" / "glucose.py").read_text()
        old = current.replace("    date: int = Field(ge=0)\n", "")
        old_path = tmp_path / "glucose_previous.py"
        old_path.write_text(old)
        return old_path

    def test_schema_diff(self, tmp_path: Path):
        """Test phlo schema diff command against an explicit old schema file."""
        runner = CliRunner()
        result = runner.invoke(
            cli,
            [
                "schema",
                "diff",
                "RawGlucoseEntries",
                "--old",
                str(self._write_old_schema(tmp_path)),
            ],
            env=SCHEMA_ENV,
        )

        assert result.exit_code == 0
        assert "Diff" in result.output

    def test_schema_diff_json(self, tmp_path: Path):
        """Test phlo schema diff with JSON output."""
        runner = CliRunner()
        result = runner.invoke(
            cli,
            [
                "schema",
                "diff",
                "RawGlucoseEntries",
                "--old",
                str(self._write_old_schema(tmp_path)),
                "--format",
                "json",
            ],
            env=SCHEMA_ENV,
        )

        assert result.exit_code == 0
        data = json.loads(result.output)
        assert "classification" in data
        assert "details" in data

    def test_schema_diff_with_old_file(self, tmp_path):
        """Test phlo schema diff against an explicit old schema file."""
        old_schema_file = tmp_path / "glucose_previous.py"
        old_schema_file.write_text(
            """
from pandera.pandas import Field
from phlo_pandera.schemas import PhloSchema


class RawGlucoseEntries(PhloSchema):
    _id: str = Field(unique=True)
    sgv: int = Field(ge=0)
"""
        )

        runner = CliRunner()
        result = runner.invoke(
            cli,
            [
                "schema",
                "diff",
                "RawGlucoseEntries",
                "--old",
                str(old_schema_file),
                "--format",
                "json",
            ],
            env=SCHEMA_ENV,
        )

        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["old_schema"] == {"_id": "str", "sgv": "int"}
        assert "Added columns: date" in data["details"]

    def test_schema_diff_generated_series_schema_exact_copy_is_safe(self, tmp_path):
        """Diffs generated Series annotations without runtime string false positives."""
        workflows = tmp_path / "workflows"
        schemas_dir = workflows / "schemas"
        schemas_dir.mkdir(parents=True)
        (workflows / "__init__.py").write_text("")
        (schemas_dir / "__init__.py").write_text("")
        schema_file = schemas_dir / "weather.py"
        schema_file.write_text(
            """
from datetime import datetime

import pandera as pa
from pandera.typing import Series
from phlo_pandera.schemas import PhloSchema


class RawObservations(PhloSchema):
    id: Series[int] = pa.Field(description="Unique key", nullable=False)
    observed_at: Series[datetime] = pa.Field(nullable=True)
"""
        )
        old_schema_file = tmp_path / "weather_previous.py"
        old_schema_file.write_text(schema_file.read_text())

        runner = CliRunner()
        result = runner.invoke(
            cli,
            [
                "schema",
                "diff",
                "RawObservations",
                "--old",
                str(old_schema_file),
                "--format",
                "json",
            ],
            env={"PHLO_SCHEMA_SEARCH_PATHS": str(workflows)},
        )

        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert data["classification"] == "SAFE"
        assert data["details"] == ["No changes detected"]
        assert data["old_schema"] == data["new_schema"]

    def test_schema_validate(self):
        """Test phlo schema validate command."""
        runner = CliRunner()

        # Use actual schema file that exists
        schema_file = str(FIXTURES_DIR / "schemas" / "glucose.py")
        result = runner.invoke(cli, ["schema", "validate", schema_file], env=SCHEMA_ENV)

        assert result.exit_code == 0
        assert "Schema Validation" in result.output
        assert "All checks passed" in result.output

    def test_schema_validate_not_found(self):
        """Test phlo schema validate with nonexistent file."""
        runner = CliRunner()
        result = runner.invoke(cli, ["schema", "validate", "nonexistent.py"])

        assert result.exit_code == 1
        assert "not found" in result.output.lower()


class TestDiscoverPanderaSchemas:
    """Test schema discovery utility."""

    def test_discover_schemas(self):
        """Test discovering Pandera schemas."""
        schemas = discover_pandera_schemas(search_paths=[SCHEMA_ENV["PHLO_SCHEMA_SEARCH_PATHS"]])

        assert isinstance(schemas, dict)
        assert len(schemas) > 0
        assert "RawGlucoseEntries" in schemas

    def test_discovered_schema_is_class(self):
        """Test that discovered schemas are classes."""
        schemas = discover_pandera_schemas(search_paths=[SCHEMA_ENV["PHLO_SCHEMA_SEARCH_PATHS"]])

        for name, schema_cls in schemas.items():
            assert isinstance(name, str)
            assert isinstance(schema_cls, type)

    def test_schema_has_annotations(self):
        """Test that schemas have field annotations."""
        schemas = discover_pandera_schemas(search_paths=[SCHEMA_ENV["PHLO_SCHEMA_SEARCH_PATHS"]])

        raw_glucose = schemas.get("RawGlucoseEntries")
        assert raw_glucose is not None
        assert hasattr(raw_glucose, "__annotations__")
        assert len(raw_glucose.__annotations__) > 0

    def test_discovery_imports_workflows_from_project_root(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        """Discovers generated project schemas even when cwd is not already importable."""
        workflows = tmp_path / "workflows"
        schemas_dir = workflows / "schemas"
        schemas_dir.mkdir(parents=True)
        (workflows / "__init__.py").write_text("")
        (schemas_dir / "__init__.py").write_text("")
        (schemas_dir / "commerce.py").write_text(
            "from phlo_pandera.schemas import PhloSchema\n\n"
            "class RawProducts(PhloSchema):\n"
            "    id: int\n"
        )
        monkeypatch.chdir(tmp_path.parent)

        schemas = discover_pandera_schemas(search_paths=[str(workflows)])

        assert "RawProducts" in schemas


class TestClassifySchemaChange:
    """Test schema change classification."""

    def test_safe_change_added_column(self):
        """Test that adding a nullable column is classified as SAFE."""
        old_schema = {"id": "int", "name": "str"}
        new_schema = {"id": "int", "name": "str", "description": "str"}

        classification, details = classify_schema_change(old_schema, new_schema)

        assert classification == "SAFE"
        assert "Added columns" in " ".join(details)

    def test_breaking_change_removed_column(self):
        """Test that removing a column is classified as BREAKING."""
        old_schema = {"id": "int", "name": "str"}
        new_schema = {"id": "int"}

        classification, details = classify_schema_change(old_schema, new_schema)

        assert classification == "BREAKING"
        assert "Removed columns" in " ".join(details)

    def test_breaking_change_type_change(self):
        """Test that changing column type is classified as BREAKING."""
        old_schema = {"id": "int", "name": "str"}
        new_schema = {"id": "str", "name": "str"}

        classification, details = classify_schema_change(old_schema, new_schema)

        assert classification == "BREAKING"
        assert any("Type changes" in detail for detail in details)

    def test_no_change(self):
        """Test classification when schemas are identical."""
        schema = {"id": "int", "name": "str"}

        classification, details = classify_schema_change(schema, schema)

        assert classification == "SAFE"
        assert "No changes" in " ".join(details)
