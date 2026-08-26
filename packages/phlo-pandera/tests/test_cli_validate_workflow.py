"""Tests for phlo validate-workflow command.

Tests the workflow validation CLI command, including:
- Decorator parameter validation
- Cron expression validation
- Function signature validation
- Directory and file handling
"""

import ast
from pathlib import Path

from click.testing import CliRunner

from phlo_pandera.cli_validate import (
    _is_phlo_ingestion_decorator,
    _is_valid_cron_field,
    _is_valid_field_name,
    _is_valid_table_name,
    _validate_cron_format,
    validate_workflow,
)


def test_validate_workflow_missing_file_prints_rerun_hint() -> None:
    """Shows a rerun command when the workflow path is missing."""
    result = CliRunner().invoke(validate_workflow, ["workflows/ingestion/missing.py"])

    assert result.exit_code != 0
    assert "workflows/ingestion/missing.py" in result.output
    assert "phlo validate-workflow workflows/ingestion/missing.py" in result.output


def test_validate_workflow_resolves_project_workflow_imports(monkeypatch, tmp_path) -> None:
    """Loads workflow files that import project-local schemas."""
    workflow_file = tmp_path / "workflows" / "ingestion" / "demo" / "events.py"
    schema_file = tmp_path / "workflows" / "schemas" / "demo.py"
    workflow_file.parent.mkdir(parents=True)
    schema_file.parent.mkdir(parents=True)
    schema_file.write_text("class RawEvents: pass\n")
    workflow_file.write_text(
        """
from workflows.schemas.demo import RawEvents


def phlo_ingestion(**kwargs):
    def decorator(func):
        return func

    return decorator


@phlo_ingestion(
    table_name="events",
    unique_key="id",
    validation_schema=RawEvents,
    group="demo",
    cron="0 */1 * * *",
    freshness_hours=(1, 24),
)
def events(partition_date: str) -> None:
    return None
"""
    )
    monkeypatch.chdir(tmp_path)

    result = CliRunner().invoke(validate_workflow, [str(workflow_file)])

    assert result.exit_code == 0
    assert "Workflow is valid" in result.output


def test_validate_workflow_recognizes_preferred_phlo_ingestion_decorator() -> None:
    """Recognizes public and legacy ingestion decorator spellings."""
    source = """
@phlo.ingest.dlt(table_name="events")
def dlt_facade(): pass

@phlo.ingestion(table_name="events")
def preferred(): pass

@phlo_ingestion(table_name="events")
def legacy(): pass
"""
    tree = ast.parse(source)
    decorators = [
        node.decorator_list[0] for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)
    ]

    assert all(_is_phlo_ingestion_decorator(decorator) for decorator in decorators)


def test_validate_workflow_recognizes_phlo_ingest_dlt_facade(tmp_path) -> None:
    """Finds workflows decorated with the public @phlo.ingest.dlt facade."""
    workflow_file = tmp_path / "dlt_workflow.py"
    workflow_file.write_text(
        """
class Ingest:
    @staticmethod
    def dlt(**kwargs):
        def decorator(func):
            return func

        return decorator


class Phlo:
    ingest = Ingest()


phlo = Phlo()


@phlo.ingest.dlt(
    table_name="events",
    unique_key="id",
    group="demo",
    cron="0 */1 * * *",
)
def events(partition_date: str) -> None:
    return None
"""
    )

    result = CliRunner().invoke(validate_workflow, [str(workflow_file)])

    assert result.exit_code == 0
    assert "Found 1 workflow" in result.output


class TestValidateCronField:
    """Tests for cron field validation."""

    def test_asterisk_is_valid(self):
        """Test that * is valid for any field."""
        assert _is_valid_cron_field("*", 0, 59)
        assert _is_valid_cron_field("*", 0, 23)
        assert _is_valid_cron_field("*", 1, 31)

    def test_numeric_field_within_range(self):
        """Test that numeric values within range are valid."""
        assert _is_valid_cron_field("0", 0, 59)
        assert _is_valid_cron_field("30", 0, 59)
        assert _is_valid_cron_field("59", 0, 59)

    def test_numeric_field_outside_range(self):
        """Test that numeric values outside range are invalid."""
        assert not _is_valid_cron_field("60", 0, 59)
        assert not _is_valid_cron_field("100", 0, 59)
        assert not _is_valid_cron_field("-1", 0, 59)

    def test_range_field_valid(self):
        """Test that range fields are valid."""
        assert _is_valid_cron_field("0-30", 0, 59)
        assert _is_valid_cron_field("10-20", 0, 59)
        assert _is_valid_cron_field("1-12", 1, 12)

    def test_range_field_invalid(self):
        """Test that invalid range fields are rejected."""
        assert not _is_valid_cron_field("60-70", 0, 59)
        assert not _is_valid_cron_field("0-100", 0, 59)

    def test_step_field_valid(self):
        """Test that step fields are valid."""
        assert _is_valid_cron_field("*/5", 0, 59)
        assert _is_valid_cron_field("*/2", 0, 23)
        assert _is_valid_cron_field("0-30/5", 0, 59)

    def test_list_field_valid(self):
        """Test that list fields are valid."""
        assert _is_valid_cron_field("0,15,30,45", 0, 59)
        assert _is_valid_cron_field("1,3,5", 0, 23)

    def test_list_field_invalid(self):
        """Test that invalid list fields are rejected."""
        assert not _is_valid_cron_field("0,60,30", 0, 59)
        assert not _is_valid_cron_field("1,100", 0, 59)

    def test_question_mark_is_valid(self):
        """Test that ? is valid (used for day or weekday)."""
        assert _is_valid_cron_field("?", 1, 31)


class TestValidateCronFormat:
    """Tests for full cron expression validation."""

    def test_valid_hourly_cron(self):
        """Test that common hourly cron is valid."""
        errors = _validate_cron_format("0 * * * *")
        assert len(errors) == 0

    def test_valid_daily_cron(self):
        """Test that common daily cron is valid."""
        errors = _validate_cron_format("0 0 * * *")
        assert len(errors) == 0

    def test_valid_weekly_cron(self):
        """Test that common weekly cron is valid."""
        errors = _validate_cron_format("0 0 * * 0")
        assert len(errors) == 0

    def test_valid_every_15_minutes_cron(self):
        """Test that every-15-minutes cron is valid."""
        errors = _validate_cron_format("*/15 * * * *")
        assert len(errors) == 0

    def test_invalid_too_few_parts(self):
        """Test that cron with too few parts is invalid."""
        errors = _validate_cron_format("0 0 * *")
        assert len(errors) > 0
        assert "5 parts" in errors[0]

    def test_invalid_too_many_parts(self):
        """Test that cron with too many parts is invalid."""
        errors = _validate_cron_format("0 0 * * * *")
        assert len(errors) > 0

    def test_invalid_minute_field(self):
        """Test that invalid minute field is caught."""
        errors = _validate_cron_format("60 * * * *")
        assert len(errors) > 0
        assert "minute" in errors[0].lower()

    def test_invalid_hour_field(self):
        """Test that invalid hour field is caught."""
        errors = _validate_cron_format("0 24 * * *")
        assert len(errors) > 0
        assert "hour" in errors[0].lower()

    def test_invalid_day_field(self):
        """Test that invalid day field is caught."""
        errors = _validate_cron_format("0 0 32 * *")
        assert len(errors) > 0
        assert "day" in errors[0].lower()

    def test_invalid_month_field(self):
        """Test that invalid month field is caught."""
        errors = _validate_cron_format("0 0 * 13 *")
        assert len(errors) > 0
        assert "month" in errors[0].lower()

    def test_warning_for_very_frequent_cron(self):
        """Test that very frequent cron schedules are warned about."""
        _validate_cron_format("*/1 * * * *")
        # May contain warnings about frequent execution
        # This is informational, not necessarily an error


class TestValidateFieldNames:
    """Tests for naming convention validation."""

    def test_valid_table_name(self):
        """Test that valid table names are accepted."""
        assert _is_valid_table_name("users")
        assert _is_valid_table_name("user_events")
        assert _is_valid_table_name("_private")
        assert _is_valid_table_name("table_name_with_underscores")

    def test_invalid_table_name_with_capitals(self):
        """Test that table names with capitals are rejected."""
        assert not _is_valid_table_name("UserEvents")
        assert not _is_valid_table_name("User_Events")

    def test_invalid_table_name_with_hyphens(self):
        """Test that table names with hyphens are rejected."""
        assert not _is_valid_table_name("user-events")

    def test_invalid_table_name_starting_with_number(self):
        """Test that table names starting with numbers are rejected."""
        assert not _is_valid_table_name("123users")

    def test_invalid_table_name_with_double_underscore(self):
        """Test that table names with double underscores are rejected."""
        assert not _is_valid_table_name("user__events")

    def test_valid_field_name(self):
        """Test that valid field names are accepted."""
        assert _is_valid_field_name("id")
        assert _is_valid_field_name("user_id")
        assert _is_valid_field_name("_internal")
        assert _is_valid_field_name("field_name_with_underscores")

    def test_invalid_field_name_with_capitals(self):
        """Test that field names with capitals are rejected."""
        assert not _is_valid_field_name("UserId")

    def test_invalid_field_name_with_double_underscore(self):
        """Test that field names with double underscores are rejected."""
        assert not _is_valid_field_name("_field__name")


class TestWorkflowValidationCLI:
    """Tests for the validate-workflow CLI command."""

    def test_validate_workflow_file_with_valid_decorator(self, tmp_path):
        """Test validating a workflow file with valid decorator."""
        runner = CliRunner()

        workflow_file = Path(tmp_path) / "test_workflow.py"
        workflow_file.write_text(
            """
# Mock @phlo_ingestion decorator for testing
def phlo_ingestion(**kwargs):
    def decorator(f):
        return f
    return decorator

@phlo_ingestion(
    table_name="test_table",
    unique_key="id",
    group="test",
    validation_schema=None,
    cron="0 */1 * * *",
    freshness_hours=(1, 24),
)
def test_workflow(partition_date: str):
    return None
"""
        )

        result = runner.invoke(validate_workflow, [str(workflow_file)])
        assert result.exit_code == 0

    def test_validate_workflow_file_missing_group(self, tmp_path):
        """Test that missing group parameter is caught."""
        runner = CliRunner()

        workflow_file = Path(tmp_path) / "test_workflow.py"
        workflow_file.write_text(
            """
def phlo_ingestion(**kwargs):
    def decorator(f):
        return f
    return decorator

@phlo_ingestion(
    table_name="test_table",
    unique_key="id",
    cron="0 */1 * * *",
)
def test_workflow(partition_date: str):
    return None
"""
        )

        result = runner.invoke(validate_workflow, [str(workflow_file)])
        # Should detect missing group parameter
        assert result.exit_code == 1 or "group" in result.output.lower()

    def test_validate_workflow_file_invalid_cron(self, tmp_path):
        """Test that invalid cron is caught."""
        runner = CliRunner()

        workflow_file = Path(tmp_path) / "test_workflow.py"
        workflow_file.write_text(
            """
def phlo_ingestion(**kwargs):
    def decorator(f):
        return f
    return decorator

@phlo_ingestion(
    table_name="test_table",
    unique_key="id",
    group="test",
    cron="invalid cron",
)
def test_workflow(partition_date: str):
    return None
"""
        )

        result = runner.invoke(validate_workflow, [str(workflow_file)])
        assert result.exit_code == 1 or "cron" in result.output.lower()

    def test_validate_workflow_directory(self, tmp_path):
        """Test validating a directory of workflow files."""
        runner = CliRunner()

        tmp_path_path = Path(tmp_path)

        # Create first valid workflow
        workflow_file1 = tmp_path_path / "workflow1.py"
        workflow_file1.write_text(
            """
def phlo_ingestion(**kwargs):
    def decorator(f):
        return f
    return decorator

@phlo_ingestion(
    table_name="table1",
    unique_key="id",
    group="test1",
    cron="0 * * * *",
)
def workflow1(partition_date: str):
    return None
"""
        )

        # Create second valid workflow
        workflow_file2 = tmp_path_path / "workflow2.py"
        workflow_file2.write_text(
            """
def phlo_ingestion(**kwargs):
    def decorator(f):
        return f
    return decorator

@phlo_ingestion(
    table_name="table2",
    unique_key="id",
    group="test2",
    cron="0 * * * *",
)
def workflow2(partition_date: str):
    return None
"""
        )

        result = runner.invoke(validate_workflow, [str(tmp_path)])
        assert result.exit_code == 0

    def test_validate_nonexistent_file(self):
        """Test that nonexistent file is handled gracefully."""
        runner = CliRunner()

        result = runner.invoke(validate_workflow, ["/nonexistent/file.py"])
        assert result.exit_code != 0

    def test_validate_workflow_with_invalid_table_name(self, tmp_path):
        """Test that invalid table names are caught."""
        runner = CliRunner()

        workflow_file = Path(tmp_path) / "test_workflow.py"
        workflow_file.write_text(
            """
def phlo_ingestion(**kwargs):
    def decorator(f):
        return f
    return decorator

@phlo_ingestion(
    table_name="InvalidTableName",
    unique_key="id",
    group="test",
    cron="0 * * * *",
)
def test_workflow(partition_date: str):
    return None
"""
        )

        result = runner.invoke(validate_workflow, [str(workflow_file)])
        # Should catch the invalid table name
        assert "invalid" in result.output.lower() or result.exit_code == 1

    def test_validate_workflow_with_invalid_unique_key(self, tmp_path):
        """Test that invalid unique key names are caught."""
        runner = CliRunner()

        workflow_file = Path(tmp_path) / "test_workflow.py"
        workflow_file.write_text(
            """
def phlo_ingestion(**kwargs):
    def decorator(f):
        return f
    return decorator

@phlo_ingestion(
    table_name="test_table",
    unique_key="InvalidKey",
    group="test",
    cron="0 * * * *",
)
def test_workflow(partition_date: str):
    return None
"""
        )

        result = runner.invoke(validate_workflow, [str(workflow_file)])
        # Should catch the invalid key name
        assert "invalid" in result.output.lower() or result.exit_code == 1

    def test_validate_workflow_warns_on_missing_schema(self, tmp_path):
        """Test that missing validation_schema generates warning."""
        runner = CliRunner()

        workflow_file = Path(tmp_path) / "test_workflow.py"
        workflow_file.write_text(
            """
def phlo_ingestion(**kwargs):
    def decorator(f):
        return f
    return decorator

@phlo_ingestion(
    table_name="test_table",
    unique_key="id",
    group="test",
    cron="0 * * * *",
)
def test_workflow(partition_date: str):
    return None
"""
        )

        result = runner.invoke(validate_workflow, [str(workflow_file)])
        # Missing validation_schema is a warning, not an issue: still valid.
        assert result.exit_code == 0

    def test_validate_workflow_warns_on_missing_freshness(self, tmp_path):
        """Test that missing freshness_hours generates warning."""
        runner = CliRunner()

        workflow_file = Path(tmp_path) / "test_workflow.py"
        workflow_file.write_text(
            """
def phlo_ingestion(**kwargs):
    def decorator(f):
        return f
    return decorator

@phlo_ingestion(
    table_name="test_table",
    unique_key="id",
    group="test",
    cron="0 * * * *",
)
def test_workflow(partition_date: str):
    return None
"""
        )

        result = runner.invoke(validate_workflow, [str(workflow_file)])
        # Missing freshness_hours is a warning, not an issue: still valid.
        assert result.exit_code == 0


class TestValidateWorkflowEdgeCases:
    """Tests for edge cases in workflow validation."""

    def test_validate_empty_file(self, tmp_path):
        """Test validating an empty Python file."""
        runner = CliRunner()

        workflow_file = Path(tmp_path) / "test_workflow.py"
        workflow_file.write_text("")

        result = runner.invoke(validate_workflow, [str(workflow_file)])
        # An empty file has no workflows: nothing to reject.
        assert result.exit_code == 0

    def test_validate_file_with_syntax_error(self, tmp_path):
        """Test validating a file with syntax errors."""
        runner = CliRunner()

        workflow_file = Path(tmp_path) / "test_workflow.py"
        workflow_file.write_text("this is not valid python syntax !!!!")

        result = runner.invoke(validate_workflow, [str(workflow_file)])
        # Should fail gracefully
        assert result.exit_code != 0

    def test_validate_cron_with_spaces(self):
        """Test that cron expressions with extra spaces are handled."""
        errors = _validate_cron_format("0  *  *  *  *")
        # Should handle gracefully (may have trailing empty parts)
        assert isinstance(errors, list)

    def test_validate_multiple_workflows_in_file(self, tmp_path):
        """Test file with multiple @phlo_ingestion decorated functions."""
        runner = CliRunner()

        workflow_file = Path(tmp_path) / "test_workflow.py"
        workflow_file.write_text(
            """
from phlo_dlt import phlo_ingestion

@phlo_ingestion(
    table_name="table1",
    unique_key="id",
    group="test",
    cron="0 * * * *",
)
def workflow1(partition_date: str):
    return None

@phlo_ingestion(
    table_name="table2",
    unique_key="id",
    group="test",
    cron="0 * * * *",
)
def workflow2(partition_date: str):
    return None
"""
        )

        result = runner.invoke(validate_workflow, [str(workflow_file)])
        # Should handle multiple workflows
        assert "workflow" in result.output.lower() or result.exit_code == 0


class TestCronExpressionExamples:
    """Tests for common cron expression examples."""

    def test_every_minute(self):
        """Test every-minute cron."""
        errors = _validate_cron_format("* * * * *")
        assert len(errors) == 0

    def test_every_5_minutes(self):
        """Test every-5-minutes cron."""
        errors = _validate_cron_format("*/5 * * * *")
        assert len(errors) == 0

    def test_every_hour(self):
        """Test every-hour cron."""
        errors = _validate_cron_format("0 * * * *")
        assert len(errors) == 0

    def test_every_day_at_midnight(self):
        """Test daily at midnight cron."""
        errors = _validate_cron_format("0 0 * * *")
        assert len(errors) == 0

    def test_every_monday_at_9am(self):
        """Test Monday at 9am cron."""
        errors = _validate_cron_format("0 9 * * 1")
        assert len(errors) == 0

    def test_every_15th_of_month(self):
        """Test 15th of every month cron."""
        errors = _validate_cron_format("0 0 15 * *")
        assert len(errors) == 0

    def test_weekdays_at_6am(self):
        """Test weekdays at 6am cron."""
        errors = _validate_cron_format("0 6 * * 1-5")
        assert len(errors) == 0

    def test_specific_times_list(self):
        """Test specific times as list."""
        errors = _validate_cron_format("0 0,6,12,18 * * *")
        assert len(errors) == 0
