"""Tests for the workflow CLI commands.

Covers non-interactive `workflow create` scaffolding and actionable
`workflow check` failures, including missing validators and dependencies.
"""

import json
from pathlib import Path

from click.testing import CliRunner

from phlo.cli.main import cli


def test_authoring_commands_remain_discoverable() -> None:
    """Keeps the authoring path commands visible from root help."""
    result = CliRunner().invoke(cli, ["--help"])

    assert result.exit_code == 0
    assert "workflow" in result.output
    assert "schema" in result.output
    assert "validate-workflow" in result.output
    assert "materialize" in result.output
    assert "status" in result.output


def test_workflow_group_help_lists_create() -> None:
    """Shows the scaffold command in workflow group help output."""
    runner = CliRunner()
    result = runner.invoke(cli, ["workflow", "--help"])

    assert result.exit_code == 0
    assert "create" in result.output


def test_workflow_create_uses_cron_default_noninteractively(monkeypatch) -> None:
    calls: dict[str, object] = {}

    def fake_create_ingestion_workflow(**kwargs):
        calls.update(kwargs)
        return [
            "workflows/schemas/demo.py",
            "workflows/ingestion/demo/orders.py",
            "tests/test_orders.py",
        ]

    monkeypatch.setattr(
        "phlo_dlt.scaffold.create_ingestion_workflow",
        fake_create_ingestion_workflow,
    )

    result = CliRunner().invoke(
        cli,
        [
            "workflow",
            "create",
            "--type",
            "ingestion",
            "--provider",
            "dlt",
            "--domain",
            "demo",
            "--table",
            "orders",
            "--unique-key",
            "id",
            "--api-base-url",
            "https://example.com/api",
        ],
    )

    assert result.exit_code == 0, result.output
    assert calls["cron"] == "0 */1 * * *"


def test_workflow_create_defaults_ingestion_noninteractively(monkeypatch) -> None:
    calls: dict[str, object] = {}

    def fake_create_ingestion_workflow(**kwargs):
        calls.update(kwargs)
        return [
            "workflows/schemas/weather.py",
            "workflows/ingestion/weather/observations.py",
            "tests/test_weather_observations.py",
        ]

    monkeypatch.setattr(
        "phlo_dlt.scaffold.create_ingestion_workflow",
        fake_create_ingestion_workflow,
    )

    result = CliRunner().invoke(
        cli,
        [
            "workflow",
            "create",
            "--provider",
            "dlt",
            "--domain",
            "weather",
            "--table",
            "observations",
            "--unique-key",
            "station_id",
            "--field",
            "station_id:str",
        ],
    )

    assert result.exit_code == 0, result.output
    assert calls["domain"] == "weather"
    assert calls["api_base_url"] is None
    assert "Workflow type" not in result.output


def test_workflow_create_invokes_scaffold(monkeypatch) -> None:
    """Passes CLI options to ingestion scaffold creation."""
    calls = {}

    def fake_create_ingestion_workflow(
        *,
        domain: str,
        table_name: str,
        unique_key: str,
        cron: str,
        api_base_url: str | None,
        fields: list[str] | None,
        source_kind: str,
    ) -> list[str]:
        """Captures scaffold arguments and returns mocked output paths."""
        calls.update(
            {
                "domain": domain,
                "table_name": table_name,
                "unique_key": unique_key,
                "cron": cron,
                "api_base_url": api_base_url,
                "fields": fields,
                "source_kind": source_kind,
            }
        )
        return [
            "workflows/schemas/weather.py",
            "workflows/ingestion/weather/observations.py",
            "workflows/tests/weather/test_observations.py",
        ]

    monkeypatch.setattr(
        "phlo_dlt.scaffold.create_ingestion_workflow",
        fake_create_ingestion_workflow,
    )

    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "workflow",
            "create",
            "--type",
            "ingestion",
            "--provider",
            "dlt",
            "--domain",
            "weather",
            "--table",
            "observations",
            "--unique-key",
            "id",
            "--cron",
            "0 */1 * * *",
            "--api-base-url",
            "https://api.example.com",
            "--field",
            "id:int",
        ],
    )

    assert result.exit_code == 0
    assert calls == {
        "domain": "weather",
        "table_name": "observations",
        "unique_key": "id",
        "cron": "0 */1 * * *",
        "api_base_url": "https://api.example.com",
        "fields": ["id:int"],
        "source_kind": "rest-api",
    }
    assert "Materialize: phlo materialize dlt_observations" in result.output


def test_workflow_create_passes_source_kind(monkeypatch) -> None:
    calls: dict[str, object] = {}

    def fake_create_ingestion_workflow(**kwargs) -> list[str]:
        calls.update(kwargs)
        return [
            "workflows/schemas/warehouse.py",
            "workflows/ingestion/warehouse/orders.py",
            "workflows/sql/warehouse/orders.sql",
            "tests/test_warehouse_orders.py",
        ]

    monkeypatch.setattr(
        "phlo_dlt.scaffold.create_ingestion_workflow",
        fake_create_ingestion_workflow,
    )

    result = CliRunner().invoke(
        cli,
        [
            "workflow",
            "create",
            "--type",
            "ingestion",
            "--provider",
            "dlt",
            "--domain",
            "warehouse",
            "--table",
            "orders",
            "--unique-key",
            "id",
            "--source-kind",
            "partitioned-sql",
        ],
    )

    assert result.exit_code == 0, result.output
    assert calls["source_kind"] == "partitioned-sql"
    assert "workflows/sql/warehouse/orders.sql" in result.output


def test_workflow_create_converts_blank_api_base_url_to_none(monkeypatch) -> None:
    """Keeps optional API URL absent when the user accepts the blank prompt default."""
    calls = {}

    def fake_create_ingestion_workflow(**kwargs) -> list[str]:
        calls.update(kwargs)
        return [
            "workflows/schemas/events.py",
            "workflows/ingestion/events/clicks.py",
            "workflows/tests/events/test_clicks.py",
        ]

    monkeypatch.setattr(
        "phlo_dlt.scaffold.create_ingestion_workflow",
        fake_create_ingestion_workflow,
    )

    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "workflow",
            "create",
            "--type",
            "ingestion",
            "--provider",
            "dlt",
            "--domain",
            "events",
            "--table",
            "clicks",
            "--unique-key",
            "event_id",
            "--cron",
            "0 0 * * *",
            "--api-base-url",
            "",
            "--field",
            "event_id:string!",
            "--field",
            "clicked_at:datetime?",
        ],
    )

    assert result.exit_code == 0
    assert calls["api_base_url"] is None
    assert calls["fields"] == ["event_id:string!", "clicked_at:datetime?"]
    assert "Review schema: workflows/schemas/events.py" in result.output


def test_workflow_create_prints_runnable_next_steps(monkeypatch, tmp_path) -> None:
    """Prints next steps that exist in the current CLI surface."""
    monkeypatch.chdir(tmp_path)

    def fake_create_ingestion_workflow(**kwargs):
        return [
            "workflows/schemas/weather.py",
            "workflows/ingestion/weather/observations.py",
            "tests/test_weather_observations.py",
        ]

    monkeypatch.setattr(
        "phlo_dlt.scaffold.create_ingestion_workflow",
        fake_create_ingestion_workflow,
    )

    result = CliRunner().invoke(
        cli,
        [
            "workflow",
            "create",
            "--type",
            "ingestion",
            "--provider",
            "dlt",
            "--domain",
            "weather",
            "--table",
            "observations",
            "--unique-key",
            "station_id",
            "--cron",
            "0 */1 * * *",
            "--api-base-url",
            "https://example.test",
        ],
    )

    assert result.exit_code == 0
    assert "phlo services restart" in result.output
    assert "phlo materialize dlt_observations" in result.output
    assert "Inspect status: phlo status" in result.output
    assert "phlo schema validate workflows/schemas/weather.py" not in result.output
    assert "phlo validate-workflow workflows/ingestion/weather/observations.py" not in result.output
    assert "phlo status --select" not in result.output
    assert "phlo test weather" not in result.output
    assert "phlo services restart dagster" not in result.output
    assert "docker restart" not in result.output


def test_workflow_create_reports_scaffold_failures(monkeypatch) -> None:
    """Exits with a concise error when scaffold creation fails."""

    def fake_create_ingestion_workflow(**kwargs) -> list[str]:
        raise RuntimeError("schema field is invalid")

    monkeypatch.setattr(
        "phlo_dlt.scaffold.create_ingestion_workflow",
        fake_create_ingestion_workflow,
    )

    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "workflow",
            "create",
            "--type",
            "ingestion",
            "--provider",
            "dlt",
            "--domain",
            "weather",
            "--table",
            "observations",
            "--unique-key",
            "id",
            "--cron",
            "0 */1 * * *",
            "--api-base-url",
            "",
        ],
    )

    assert result.exit_code == 1
    assert "schema field is invalid" not in result.output
    assert "Error: could not create workflow" in result.output
    assert "Workflow: ingestion" in result.output
    assert "Dataset: weather.observations" in result.output
    assert "Run: phlo workflow create --help" in result.output


def test_workflow_check_delegates_to_existing_validators(monkeypatch, tmp_path) -> None:
    """Checks the workflow and inferred schema before materialization."""
    workflow_file = tmp_path / "workflows" / "ingestion" / "weather" / "observations.py"
    schema_file = tmp_path / "workflows" / "schemas" / "weather.py"
    workflow_file.parent.mkdir(parents=True)
    schema_file.parent.mkdir(parents=True)
    workflow_file.write_text("import phlo\n")
    schema_file.write_text("class WeatherSchema: pass\n")
    monkeypatch.chdir(tmp_path)

    calls: list[tuple[str, str]] = []

    def fake_validate_schema(path: str) -> None:
        calls.append(("schema", path))

    def fake_validate_workflow(path: str) -> None:
        calls.append(("workflow", path))

    monkeypatch.setattr("phlo.cli.commands.workflow._validate_schema_file", fake_validate_schema)
    monkeypatch.setattr(
        "phlo.cli.commands.workflow._validate_workflow_file", fake_validate_workflow
    )

    result = CliRunner().invoke(cli, ["workflow", "check", str(workflow_file)])

    assert result.exit_code == 0
    assert ("workflow", str(workflow_file)) in calls
    assert ("schema", str(schema_file)) in calls
    assert "phlo materialize observations" in result.output


def test_workflow_check_json_suppresses_validator_chatter(monkeypatch, tmp_path) -> None:
    """Keeps --json output parseable even when validators print human diagnostics."""
    workflow_file = tmp_path / "workflows" / "ingestion" / "weather" / "observations.py"
    schema_file = tmp_path / "workflows" / "schemas" / "weather.py"
    workflow_file.parent.mkdir(parents=True)
    schema_file.parent.mkdir(parents=True)
    workflow_file.write_text("import phlo\n")
    schema_file.write_text("class WeatherSchema: pass\n")
    monkeypatch.chdir(tmp_path)

    def fake_validate_schema(path: str) -> None:
        print(f"schema validator chatter for {path}")

    def fake_validate_workflow(path: str) -> None:
        print(f"workflow validator chatter for {path}")

    monkeypatch.setattr("phlo.cli.commands.workflow._validate_schema_file", fake_validate_schema)
    monkeypatch.setattr(
        "phlo.cli.commands.workflow._validate_workflow_file", fake_validate_workflow
    )

    result = CliRunner().invoke(cli, ["workflow", "check", str(workflow_file), "--json"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["data"]["valid"] is True
    assert "validator chatter" not in result.output


def test_workflow_check_missing_file_is_actionable(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)

    result = CliRunner().invoke(
        cli,
        ["workflow", "check", "workflows/ingestion/weather/missing.py"],
    )

    assert result.exit_code == 1
    assert "Error: workflow file not found" in result.output
    assert "Missing: workflows/ingestion/weather/missing.py" in result.output
    assert "Run: phlo workflow create" in result.output


def test_workflow_check_missing_pandera_dependency_is_actionable(tmp_path, monkeypatch) -> None:
    """Reports the optional validator dependency without a traceback."""
    workflow_file = tmp_path / "workflows" / "ingestion" / "weather" / "observations.py"
    workflow_file.parent.mkdir(parents=True)
    workflow_file.write_text("import phlo\n")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("phlo.cli.commands.workflow.discover_capabilities", lambda: None)
    from phlo.capabilities import clear_capabilities

    clear_capabilities("workflow_validation")
    try:
        result = CliRunner().invoke(cli, ["workflow", "check", str(workflow_file)])

        assert result.exit_code == 1
        assert "workflow_validation capability is unavailable" in result.output
        assert "Install a provider that supplies workflow validation." in result.output
        assert 'Run: uv pip install "phlo-pandera"' in result.output
        assert "Traceback" not in result.output
    finally:
        clear_capabilities("workflow_validation")


def test_workflow_check_rejects_files_without_ingestion_workflow(tmp_path, monkeypatch) -> None:
    """Fails strict checks when a file has no Phlo ingestion asset."""
    workflow_file = tmp_path / "workflows" / "ingestion" / "weather" / "notes.py"
    schema_file = tmp_path / "workflows" / "schemas" / "weather.py"
    workflow_file.parent.mkdir(parents=True)
    schema_file.parent.mkdir(parents=True)
    workflow_file.write_text("VALUE = 1\n")
    schema_file.write_text('"""Weather schema."""\n\nclass WeatherSchema: pass\n')
    monkeypatch.chdir(tmp_path)

    result = CliRunner().invoke(cli, ["workflow", "check", str(workflow_file)])

    assert result.exit_code != 0
    assert "No @phlo.ingestion decorated workflow found" in result.output
    assert "phlo materialize" not in result.output


def test_workflow_check_rejects_commented_ingestion_decorator(tmp_path, monkeypatch) -> None:
    """Does not treat decorator-looking comments as workflows."""
    workflow_file = tmp_path / "workflows" / "ingestion" / "weather" / "notes.py"
    schema_file = tmp_path / "workflows" / "schemas" / "weather.py"
    workflow_file.parent.mkdir(parents=True)
    schema_file.parent.mkdir(parents=True)
    workflow_file.write_text(
        """
DECORATOR_TEXT = "@phlo.ingestion(table_name='notes')"

# @phlo.ingestion(
#     table_name="notes",
#     unique_key="id",
#     validation_schema=None,
#     group="weather",
#     cron="0 */1 * * *",
#     freshness_hours=(1, 24),
# )
def helper(partition_date: str) -> None:
    return None
"""
    )
    schema_file.write_text(
        '"""Weather schema."""\n\nimport pandera as pa\n\nclass WeatherSchema: pass\n'
    )
    monkeypatch.chdir(tmp_path)

    result = CliRunner().invoke(cli, ["workflow", "check", str(workflow_file)])

    assert result.exit_code != 0
    assert "No @phlo.ingestion decorated workflow found" in result.output
    assert "phlo materialize" not in result.output


def test_workflow_check_wraps_schema_validation_failures(monkeypatch, tmp_path) -> None:
    """Reports schema validation errors as Click failures."""
    workflow_file = tmp_path / "workflows" / "ingestion" / "weather" / "observations.py"
    schema_file = tmp_path / "workflows" / "schemas" / "weather.py"
    workflow_file.parent.mkdir(parents=True)
    schema_file.parent.mkdir(parents=True)
    workflow_file.write_text("import phlo\n")
    schema_file.write_text("not valid python")
    monkeypatch.chdir(tmp_path)

    def fake_validate_workflow(path: str) -> None:
        return None

    def fake_validate_schema(path: str) -> None:
        raise ValueError("schema syntax exploded")

    monkeypatch.setattr(
        "phlo.cli.commands.workflow._validate_workflow_file", fake_validate_workflow
    )
    monkeypatch.setattr("phlo.cli.commands.workflow._validate_schema_file", fake_validate_schema)

    result = CliRunner().invoke(cli, ["workflow", "check", str(workflow_file)])

    assert result.exit_code != 0
    assert "Schema validation failed" in result.output
    assert "schema syntax exploded" in result.output
    assert "Traceback" not in result.output


def test_init_with_absolute_path_uses_directory_name_for_project_metadata(tmp_path: Path) -> None:
    """Uses directory basename, not full absolute path, for project name."""
    project_dir = tmp_path / "my-lakehouse"

    runner = CliRunner()
    result = runner.invoke(cli, ["init", str(project_dir), "--template", "minimal"])

    assert result.exit_code == 0
    pyproject_content = (project_dir / "pyproject.toml").read_text()
    assert f'name = "{project_dir.name}"' in pyproject_content
    assert f'name = "{project_dir}"' not in pyproject_content
    assert (project_dir / "contracts" / ".gitkeep").exists()
    assert (project_dir / "data" / ".gitkeep").exists()
    assert (project_dir / "plugins" / ".gitkeep").exists()
    assert "capabilities:" in (project_dir / "phlo.yaml").read_text()
