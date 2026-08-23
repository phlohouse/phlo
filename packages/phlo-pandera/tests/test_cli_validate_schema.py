"""Tests for schema validation CLI behavior.

The validate command must fail with actionable errors: files without
DataFrameModel classes are rejected, and missing schema files point at
the workflow-create escape hatch.
"""

from click.testing import CliRunner

from phlo_pandera.cli_validate import validate_schema


def test_validate_schema_rejects_files_without_schema_classes(tmp_path) -> None:
    schema_file = tmp_path / "empty.py"
    schema_file.write_text('"""Not a schema."""\n\nVALUE = 1\n')

    result = CliRunner().invoke(validate_schema, [str(schema_file)])

    assert result.exit_code != 0
    assert "No Pandera DataFrameModel classes found" in result.output


def test_validate_schema_missing_file_is_actionable(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)

    result = CliRunner().invoke(validate_schema, ["workflows/schemas/missing.py"])

    assert result.exit_code != 0
    assert "Schema file not found: workflows/schemas/missing.py" in result.output
    assert "Run: phlo workflow create" in result.output
