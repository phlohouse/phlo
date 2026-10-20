"""Exercise integration group selection through real disposable pytest suites.

Only the uv launcher and Docker boundary are replaced; pytest collects and runs
fixture modules with the production required-suite plugin and writes real JUnit.
"""

from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "phlo_run_integration", ROOT / "scripts/run_integration.py"
)
assert SPEC and SPEC.loader
run_integration = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(run_integration)

FIXTURE_MODULES = {
    "iceberg": "packages/phlo-iceberg/tests/test_integration_iceberg.py",
    "dagster": "packages/phlo-dagster/tests/test_integration_dagster.py",
    "api": "packages/phlo-api/tests/test_new_contract.py",
    "trino": "packages/phlo-api/tests/test_usage_trino_integration.py",
    "dataset": "tests/integration/test_issue1035_dataset_journey.py",
    "branch": "tests/integration/test_issue1035_signed_branch_journey.py",
    "minio-migration": "packages/phlo-minio/tests/test_minio_migration.py",
    "dbt-quality": "packages/phlo-dbt/tests/test_quality_asset_checks.py",
    "schema-generation": "packages/phlo-pandera/tests/test_cli_004_schema_generate.py",
    "schema-conversion": "packages/phlo-iceberg/tests/test_schema_converter.py",
    "ingestion": "packages/phlo-dlt/tests/test_ingestion_decorator.py",
}


def _write_test(root: Path, relative: str, name: str, outcome: str = "passed") -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    body = {
        "passed": "",
        "failed": "    pytest.fail('fixture failure')\n",
        "skipped": "    pytest.skip('fixture skip')\n",
        "xfailed": "    pytest.xfail('fixture xfail')\n",
    }[outcome]
    path.write_text(
        "import os\nfrom pathlib import Path\nimport pytest\n"
        "pytestmark = pytest.mark.integration\n"
        "def test_contract():\n"
        "    assert os.environ['PHLO_CI_REQUIRED'] == 'true'\n"
        "    assert os.environ['PHLO_INTEGRATION_FIXTURE'] == 'preserved'\n"
        f"    with Path({str(root / 'executed.txt')!r}).open('a') as output:\n"
        f"        output.write({name + chr(10)!r})\n" + body,
        encoding="utf-8",
    )


@pytest.fixture
def workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    for name, relative in FIXTURE_MODULES.items():
        _write_test(tmp_path, relative, name)
    (tmp_path / "pytest.ini").write_text(
        "[pytest]\naddopts = --collect-only\nmarkers = integration: fixture contract\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("PHLO_INTEGRATION_FIXTURE", "preserved")
    results = []
    real_run = subprocess.run

    def run_pytest(command, *, env, check):
        child_env = dict(env, PYTHONPATH=str(ROOT), PYTEST_DISABLE_PLUGIN_AUTOLOAD="1")
        result = real_run(
            [sys.executable, *command[4:]],
            env=child_env,
            check=check,
            capture_output=True,
            text=True,
        )
        results.append(result)
        return result

    def unexpected_docker(*_args, **_kwargs):
        pytest.fail("API groups must not provision the harness's shared services")

    monkeypatch.setattr(run_integration.subprocess, "run", run_pytest)
    monkeypatch.setattr(run_integration, "DockerContainer", unexpected_docker)
    return SimpleNamespace(root=tmp_path, results=results)


@pytest.fixture
def docker_services(monkeypatch: pytest.MonkeyPatch):
    containers = []

    class Container:
        """Model an owned Docker service and record its lifecycle."""

        def __init__(self, image):
            self.image = image
            self.env = {}
            self.active = False
            self.closed = False
            containers.append(self)

        def with_env(self, name, value):
            self.env[name] = value
            return self

        def with_command(self, _command):
            return self

        def with_exposed_ports(self, *_ports):
            return self

        def with_kwargs(self, **_kwargs):
            return self

        def __enter__(self):
            self.active = True
            return self

        def __exit__(self, *_exc):
            self.active = False
            self.closed = True

        def get_container_host_ip(self):
            return "127.0.0.1"

        def get_exposed_port(self, _port):
            return 19000

        def exec(self, _command):
            return SimpleNamespace(exit_code=0 if self.active else 1)

    def healthy_service(_url, *, timeout):
        return SimpleNamespace(is_success=any(service.active for service in containers))

    monkeypatch.setattr(run_integration, "DockerContainer", Container)
    monkeypatch.setattr(run_integration.httpx, "get", healthy_service)
    return containers


def _executed(workspace) -> set[str]:
    return set((workspace.root / "executed.txt").read_text(encoding="utf-8").splitlines())


def _reports(workspace) -> dict[str, ET.Element]:
    return {
        path.stem: ET.parse(path).getroot()
        for path in (workspace.root / "test-results/integration").glob("*.xml")
    }


@pytest.mark.parametrize(
    ("group", "expected"),
    [
        ("api-contracts", {"api"}),
        ("api-query-journey", {"trino", "dataset"}),
        ("api-branch-journey", {"branch"}),
    ],
)
def test_api_groups_execute_only_owned_modules_without_shared_docker(workspace, group, expected):
    assert run_integration.main(["--group", group]) == 0
    assert _executed(workspace) == expected
    reports = _reports(workspace)
    assert set(reports) == {group}
    assert len(reports[group].findall(".//testcase")) == len(expected)
    assert "slowest 20 durations" in workspace.results[0].stdout


def test_api_contracts_discover_future_modules_with_duplicate_filenames(workspace):
    for name in ("first", "second"):
        _write_test(
            workspace.root,
            f"packages/phlo-api/tests/{name}/test_duplicate.py",
            name,
        )
    (workspace.root / "packages/phlo-api/tests/test_unit_only.py").write_text(
        "def test_unit_only():\n    raise AssertionError('unit test entered integration')\n",
        encoding="utf-8",
    )

    assert run_integration.main(["--group", "api-contracts"]) == 0
    assert _executed(workspace) == {"api", "first", "second"}


@pytest.mark.parametrize("group", ["all", "storage"])
def test_default_and_storage_keep_complete_contracts_and_owned_service_lifecycle(
    workspace, docker_services, group
):
    # The fixture consumes the connection details passed by the real harness.
    iceberg = workspace.root / FIXTURE_MODULES["iceberg"]
    with iceberg.open("a", encoding="utf-8") as module:
        module.write(
            "    assert os.environ['MINIO_HOST'] == '127.0.0.1'\n"
            "    assert os.environ['MINIO_API_PORT'] == '19000'\n"
            "    assert os.environ['ICEBERG_S3_ENDPOINT'] == 'http://127.0.0.1:19000'\n"
            "    assert os.environ['ICEBERG_CATALOG_URI'] == 'http://localhost:19120/iceberg'\n"
        )

    assert run_integration.main([] if group == "all" else ["--group", group]) == 0
    expected = set(FIXTURE_MODULES)
    if group == "storage":
        expected -= {"api", "trino", "dataset", "branch"}
    assert _executed(workspace) == expected
    assert set(_reports(workspace)) == expected - {"trino", "dataset", "branch"}
    assert len(docker_services) == 2
    assert all(service.closed and not service.active for service in docker_services)


@pytest.mark.parametrize("outcome", ["failed", "skipped", "xfailed", "empty"])
def test_required_failure_still_runs_later_suite_and_retains_both_reports(workspace, outcome):
    first = "tests/test_first.py"
    if outcome == "empty":
        (workspace.root / first).write_text("", encoding="utf-8")
    else:
        _write_test(workspace.root, first, "first", outcome)
    _write_test(workspace.root, "tests/test_later.py", "later")
    suites = {"first": [first], "later": ["tests/test_later.py"]}

    assert run_integration.run_suites(dict(os.environ, PHLO_CI_REQUIRED="true"), suites) == 1
    assert "later" in _executed(workspace)
    reports = _reports(workspace)
    assert set(reports) == {"first", "later"}
    assert len(reports["later"].findall(".//testcase")) == 1
    assert workspace.results[0].returncode != 0
    assert workspace.results[1].returncode == 0


@pytest.mark.parametrize("group", ["unknown", ""])
def test_invalid_group_is_rejected_before_execution(workspace, group):
    with pytest.raises(SystemExit) as exc:
        run_integration.main(["--group", group])
    assert exc.value.code == 2
    assert workspace.results == []
    assert not (workspace.root / "test-results").exists()
    with pytest.raises(ValueError, match="Unknown integration group"):
        run_integration.select_suites(group)


def test_empty_suite_mapping_cannot_report_success(workspace):
    with pytest.raises(ValueError, match="At least one required suite"):
        run_integration.run_suites({}, {})
    assert workspace.results == []
    assert not (workspace.root / "test-results").exists()
