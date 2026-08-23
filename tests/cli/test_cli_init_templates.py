"""Tests for init templates and the init-command discovery guard.

Template generation must produce runnable projects: generated Python parses and
imports, dependencies are pinned in pyproject.toml, and onboarding output stays
current. The discovery guard matches only root-level init invocations.
"""

import ast
import importlib
import json
import subprocess
import sys
import tomllib
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest
from click.testing import CliRunner

from phlo.cli._init_discovery_guard import is_init_command_invocation
from phlo.cli.main import cli
from phlo.cli.templates.models import TemplateMetadata
from phlo.cli.templates.registry import TemplateDiscoveryError, get_template, list_templates


def test_registry_contains_existing_templates() -> None:
    names = [template.metadata.name for template in list_templates()]

    assert "minimal" in names
    assert "basic" in names


def test_get_template_rejects_unknown_template() -> None:
    with pytest.raises(KeyError, match="unknown-template"):
        get_template("unknown-template")


def test_template_discovery_works_without_optional_providers(monkeypatch) -> None:
    monkeypatch.setattr("phlo.cli.templates.registry.entry_points_for_group", lambda _: ())

    assert [template.metadata.name for template in list_templates()] == ["minimal"]


def test_template_discovery_rejects_conflicting_provider_templates(monkeypatch) -> None:
    class DuplicateMinimalTemplate:
        metadata = TemplateMetadata(name="minimal", description="duplicate")

        def render(self, context) -> None:
            del context

    class FakeEntryPoint:
        name = "fake-provider"

        @staticmethod
        def load():
            return lambda: (DuplicateMinimalTemplate(),)

    monkeypatch.setattr(
        "phlo.cli.templates.registry.entry_points_for_group", lambda _: (FakeEntryPoint(),)
    )

    with pytest.raises(TemplateDiscoveryError, match="multiple providers"):
        list_templates()


def test_minimal_template_generates_project(tmp_path) -> None:
    project_dir = tmp_path / "demo"
    result = CliRunner().invoke(cli, ["init", str(project_dir), "--template", "minimal"])

    assert result.exit_code == 0, result.output
    assert (project_dir / "phlo.yaml").exists()
    assert (project_dir / "workflows" / "__init__.py").exists()


def test_basic_template_generates_dbt_project(tmp_path) -> None:
    project_dir = tmp_path / "demo"
    result = CliRunner().invoke(cli, ["init", str(project_dir), "--template", "basic"])

    assert result.exit_code == 0, result.output
    assert (project_dir / "workflows" / "transforms" / "dbt" / "dbt_project.yml").exists()


def test_init_defaults_to_minimal_template(tmp_path) -> None:
    project_dir = tmp_path / "demo"
    result = CliRunner().invoke(cli, ["init", str(project_dir)])

    assert result.exit_code == 0, result.output
    assert (project_dir / "phlo.yaml").exists()
    assert (project_dir / "workflows" / "__init__.py").exists()
    assert not (project_dir / "workflows" / "transforms" / "dbt" / "dbt_project.yml").exists()
    assert _project_dependencies(project_dir) == ["phlo"]


def test_init_list_templates_outputs_metadata() -> None:
    result = CliRunner().invoke(cli, ["init", "--list-templates"])

    assert result.exit_code == 0
    assert "minimal" in result.output
    assert "basic" in result.output
    assert "dbt-ready Phlo project" in result.output


def test_init_list_templates_json_outputs_envelope() -> None:
    result = CliRunner().invoke(cli, ["init", "--list-templates", "--json"])

    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["errors"] == []
    assert any(item["name"] == "minimal" for item in payload["data"]["items"])


def test_init_json_outputs_project_envelope(tmp_path) -> None:
    project_dir = tmp_path / "demo"
    result = CliRunner().invoke(cli, ["init", str(project_dir), "--json"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["errors"] == []
    assert payload["data"]["project_dir"] == str(project_dir)
    assert payload["data"]["template"] == "minimal"
    assert payload["data"]["next_steps"][0] == f"cd {project_dir}"
    assert (project_dir / "AGENTS.md").exists()


def _assert_python_files_parse(project_dir: Path) -> None:
    for path in project_dir.rglob("*.py"):
        ast.parse(path.read_text(), filename=str(path))


# Every generated project ships a top-level `workflows` package, so stale
# entries from earlier imports must be purged or a later project would
# import another project's modules.
@contextmanager
def _generated_project_import_path(project_dir: Path) -> Iterator[None]:
    sys.path.insert(0, str(project_dir))
    for module_name in list(sys.modules):
        if module_name == "workflows" or module_name.startswith("workflows."):
            sys.modules.pop(module_name)
    try:
        yield
    finally:
        sys.path.remove(str(project_dir))
        for module_name in list(sys.modules):
            if module_name == "workflows" or module_name.startswith("workflows."):
                sys.modules.pop(module_name)


def _assert_generated_module_imports(project_dir: Path, module_name: str) -> None:
    with _generated_project_import_path(project_dir):
        importlib.import_module(module_name)


def _project_dependencies(project_dir: Path) -> list[str]:
    pyproject = tomllib.loads((project_dir / "pyproject.toml").read_text())
    return pyproject["project"]["dependencies"]


def test_csv_batch_template_generates_runnable_files(tmp_path) -> None:
    project_dir = tmp_path / "csv-demo"
    result = CliRunner().invoke(cli, ["init", str(project_dir), "--template", "csv-batch"])

    assert result.exit_code == 0, result.output
    assert (project_dir / "data" / "events.csv").exists()
    workflow_file = project_dir / "workflows" / "ingestion" / "csv" / "events.py"
    assert workflow_file.exists()
    workflow_text = workflow_file.read_text()
    assert "import phlo" in workflow_text
    assert "@phlo.ingestion(" in workflow_text
    assert 'unique_key="event_id"' in workflow_text
    assert 'events["event_id"]' in workflow_text
    assert "freshness_hours=(1, 24)" in workflow_text
    assert "partition_date: str" in workflow_text
    assert "dlt.resource" in workflow_text
    assert 'name="events"' in workflow_text
    assert "phlo_ingestion" not in workflow_text
    schema_text = (project_dir / "workflows" / "schemas" / "csv.py").read_text()
    assert "CSV demo event records." in schema_text
    assert "event_id: str" in schema_text
    _assert_python_files_parse(project_dir)
    _assert_generated_module_imports(project_dir, "workflows.ingestion.csv.events")


def test_csv_batch_template_prints_metadata_next_steps(tmp_path) -> None:
    project_dir = tmp_path / "csv-demo"
    result = CliRunner().invoke(cli, ["init", str(project_dir), "--template", "csv-batch"])

    assert result.exit_code == 0, result.output
    assert ".sqlfluff" not in result.output
    assert "workflows/transforms/dbt" not in result.output
    assert "workflows/ingestion/csv/events.py" in result.output
    assert "phlo test" in result.output
    assert "phlo materialize dlt_events --partition 2025-01-15" in result.output
    assert "phlo workflow check" not in result.output
    assert "phlo dev" not in result.output


def test_csv_batch_init_prints_complete_onboarding_path(tmp_path) -> None:
    project_dir = tmp_path / "csv-demo"
    result = CliRunner().invoke(cli, ["init", str(project_dir), "--template", "csv-batch"])

    assert result.exit_code == 0, result.output
    assert f"cd {project_dir}" in result.output
    assert "uv pip install -e ." in result.output
    assert "phlo services init" in result.output
    assert "phlo services start" in result.output
    assert "phlo doctor" in result.output
    assert "phlo materialize dlt_events --partition 2025-01-15" in result.output
    assert result.output.index("uv pip install -e .") < result.output.index("phlo services init")
    assert result.output.index("phlo services init") < result.output.index("phlo services start")
    assert result.output.index("phlo services start") < result.output.index("phlo doctor")


def test_minimal_init_does_not_print_template_materialize_command(tmp_path) -> None:
    project_dir = tmp_path / "minimal-demo"
    result = CliRunner().invoke(cli, ["init", str(project_dir), "--template", "minimal"])

    assert result.exit_code == 0, result.output
    assert "uv pip install -e ." in result.output
    assert "phlo materialize" not in result.output


def test_generated_readme_uses_current_onboarding_commands(tmp_path) -> None:
    project_dir = tmp_path / "demo"
    result = CliRunner().invoke(cli, ["init", str(project_dir), "--template", "minimal"])

    assert result.exit_code == 0, result.output
    readme = (project_dir / "README.md").read_text()
    assert "uv pip install -e ." in readme
    assert "phlo services init" in readme
    assert "phlo services start" in readme
    assert "phlo services status" in readme
    assert "phlo doctor" in readme
    assert "http://localhost:10006" in readme
    assert "phlo dev" not in readme
    assert "localhost:3000" not in readme
    assert "   pip install -e ." not in readme


def test_csv_batch_readme_includes_runnable_template_command(tmp_path) -> None:
    project_dir = tmp_path / "csv-demo"
    result = CliRunner().invoke(cli, ["init", str(project_dir), "--template", "csv-batch"])

    assert result.exit_code == 0, result.output
    readme = (project_dir / "README.md").read_text()
    assert "phlo materialize dlt_events --partition 2025-01-15" in readme
    assert "use a completed partition date" in readme


@pytest.mark.parametrize(
    ("template_name", "expected_dependencies"),
    [
        ("minimal", ["phlo"]),
        ("basic", ["phlo", "phlo-dbt"]),
        ("csv-batch", ["phlo", "phlo-dlt", "phlo-pandera"]),
        ("api-ingestion", ["phlo", "phlo-dlt", "phlo-pandera"]),
        ("observability-demo", ["phlo", "phlo-dlt", "phlo-pandera", "phlo-otel"]),
    ],
)
def test_template_writes_required_packages_to_pyproject(
    tmp_path, template_name: str, expected_dependencies: list[str]
) -> None:
    project_dir = tmp_path / template_name
    result = CliRunner().invoke(cli, ["init", str(project_dir), "--template", template_name])

    assert result.exit_code == 0, result.output
    assert _project_dependencies(project_dir) == expected_dependencies


def test_template_pyproject_limits_setuptools_package_discovery(tmp_path) -> None:
    project_dir = tmp_path / "csv-demo"
    result = CliRunner().invoke(cli, ["init", str(project_dir), "--template", "csv-batch"])

    assert result.exit_code == 0, result.output
    pyproject = tomllib.loads((project_dir / "pyproject.toml").read_text())
    find_config = pyproject["tool"]["setuptools"]["packages"]["find"]
    assert find_config["include"] == ["workflows*"]
    assert find_config["exclude"] == ["contracts*", "data*", "plugins*", "tests*"]


@pytest.mark.parametrize("template_name", ["minimal", "basic"])
def test_existing_templates_print_metadata_next_steps(tmp_path, template_name: str) -> None:
    project_dir = tmp_path / template_name
    result = CliRunner().invoke(cli, ["init", str(project_dir), "--template", template_name])

    assert result.exit_code == 0, result.output
    assert "phlo services init" in result.output
    assert "phlo workflow create" in result.output


def test_api_ingestion_template_generates_runnable_files(tmp_path) -> None:
    project_dir = tmp_path / "api-demo"
    result = CliRunner().invoke(cli, ["init", str(project_dir), "--template", "api-ingestion"])

    assert result.exit_code == 0, result.output
    assert (project_dir / "workflows" / "ingestion" / "api" / "events.py").exists()
    workflow_text = (project_dir / "workflows" / "ingestion" / "api" / "events.py").read_text()
    schema_text = (project_dir / "workflows" / "schemas" / "api.py").read_text()
    assert 'unique_key="event_id"' in workflow_text
    assert 'events["event_id"]' in workflow_text
    assert "freshness_hours=(1, 24)" in workflow_text
    assert "partition_date: str" in workflow_text
    assert "dlt.resource" in workflow_text
    assert "API demo event records." in schema_text
    assert "event_id: str" in schema_text
    _assert_python_files_parse(project_dir)
    _assert_generated_module_imports(project_dir, "workflows.ingestion.api.events")


@pytest.mark.parametrize(
    ("template_name", "expected_path"),
    [
        ("dbt-medallion", "workflows/transforms/dbt/models/silver/stg_events.sql"),
        ("sling-replication", "replication/sling.yaml"),
        ("observability-demo", "workflows/ingestion/observability/events.py"),
    ],
)
def test_gallery_templates_generate_expected_files(
    tmp_path, template_name: str, expected_path: str
) -> None:
    project_dir = tmp_path / template_name
    result = CliRunner().invoke(cli, ["init", str(project_dir), "--template", template_name])

    assert result.exit_code == 0, result.output
    assert (project_dir / expected_path).exists()
    _assert_python_files_parse(project_dir)
    if template_name == "observability-demo":
        _assert_generated_module_imports(project_dir, "workflows.ingestion.observability.events")


def test_template_missing_package_prints_install_hint(monkeypatch, tmp_path) -> None:
    def fake_find_spec(name: str):
        return None if name == "phlo_sling" else object()

    monkeypatch.setattr("phlo.cli.templates.registry.importlib.util.find_spec", fake_find_spec)

    result = CliRunner().invoke(
        cli, ["init", str(tmp_path / "demo"), "--template", "sling-replication"]
    )

    assert result.exit_code != 0
    assert "phlo-sling" in result.output
    assert "uv pip install" in result.output


def test_unknown_template_prints_clean_error(tmp_path) -> None:
    result = CliRunner().invoke(
        cli, ["init", str(tmp_path / "demo"), "--template", "unknown-template"]
    )

    assert result.exit_code != 0
    assert "unknown-template" in result.output
    assert "Available templates:" in result.output
    assert "Traceback" not in result.output


def test_unknown_template_real_command_has_no_plugin_traceback_noise(tmp_path) -> None:
    # Runs through a fresh interpreter on purpose: startup-time plugin
    # discovery noise never surfaces inside an in-process CliRunner.
    result = subprocess.run(
        [
            "uv",
            "run",
            "phlo",
            "init",
            str(tmp_path / "demo"),
            "--template",
            "nope",
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    output = result.stdout + result.stderr

    assert result.returncode != 0
    assert "Unknown template 'nope'" in output
    assert "Available templates:" in output
    assert "plugin_load_failed" not in output
    assert "Traceback" not in output


@pytest.mark.parametrize(
    ("argv", "expected"),
    [
        (["phlo", "init", "demo"], True),
        (["phlo", "--help", "init"], False),
        (["phlo", "dbt", "run", "--select", "init"], False),
    ],
)
def test_init_discovery_guard_only_matches_root_init_command(
    argv: list[str], expected: bool
) -> None:
    assert is_init_command_invocation(argv) is expected


def test_plugin_command_with_init_argument_is_registered() -> None:
    result = subprocess.run(
        ["uv", "run", "phlo", "dbt", "run", "--select", "init", "--help"],
        check=False,
        capture_output=True,
        text=True,
    )
    output = result.stdout + result.stderr

    assert "No such command 'dbt'" not in output
    assert "Usage:" in output
    assert "dbt run" in output
