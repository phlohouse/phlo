"""Behavioral tests for the `phlo materialize` CLI command.

Uses fake container backends and patched runtime waits to cover WAP-enabled
launches, contract-refresh defaults, and actionable failure reporting without
live services.
"""

from subprocess import PIPE, STDOUT
from unittest.mock import patch

import httpx
from click.testing import CliRunner

from phlo_dagster.cli_materialize import materialize, wait_for_dagster_runtime


class FakePodmanBackend:
    name = "podman"

    def container_exec_cmd(self, *, container_name, command, env=None, workdir=None, user=None):
        cmd = ["podman", "exec"]
        if user:
            cmd.extend(["--user", user])
        for key, value in (env or {}).items():
            cmd.extend(["-e", f"{key}={value}"])
        if workdir:
            cmd.extend(["-w", workdir])
        cmd.append(container_name)
        cmd.extend(command)
        return cmd


def test_materialize_help_is_user_facing() -> None:
    result = CliRunner().invoke(materialize, ["--help"])

    assert result.exit_code == 0
    assert "Materialize Dagster assets via the configured container backend." in result.output
    assert "Args:" not in result.output
    assert "Returns:" not in result.output
    assert "Raises:" not in result.output


def test_wait_for_dagster_runtime_uses_ready_marker(monkeypatch) -> None:
    calls = []

    def fake_run(cmd, check, capture_output, text):
        calls.append(cmd)

        class Result:
            returncode = 0

        return Result()

    monkeypatch.setattr("phlo_dagster.cli_materialize.subprocess.run", fake_run)

    wait_for_dagster_runtime("dagster-1", timeout_seconds=0.1)

    assert calls == [
        [
            "docker",
            "exec",
            "dagster-1",
            "sh",
            "-lc",
            "test -f /tmp/phlo-dagster-ready "
            "|| python -c 'import phlo_dagster.framework.definitions'",
        ]
    ]


def test_wait_for_dagster_runtime_uses_selected_backend(monkeypatch) -> None:
    calls = []

    def fake_run(cmd, check, capture_output, text):
        calls.append(cmd)

        class Result:
            returncode = 0

        return Result()

    monkeypatch.setattr("phlo_dagster.cli_materialize.subprocess.run", fake_run)

    wait_for_dagster_runtime("dagster-1", timeout_seconds=0.1, backend=FakePodmanBackend())

    assert calls[0][:3] == ["podman", "exec", "dagster-1"]


def test_wait_for_dagster_runtime_times_out(monkeypatch) -> None:
    def fake_run(cmd, check, capture_output, text):
        class Result:
            returncode = 1

        return Result()

    monkeypatch.setattr("phlo_dagster.cli_materialize.subprocess.run", fake_run)
    monkeypatch.setattr("phlo_dagster.cli_materialize.time.sleep", lambda seconds: None)

    try:
        wait_for_dagster_runtime("dagster-1", timeout_seconds=0)
    except RuntimeError as exc:
        assert "still finishing runtime setup" in str(exc)
        assert "phlo services logs --tail 120 dagster" in str(exc)
    else:
        raise AssertionError("expected RuntimeError")


@patch("phlo_dagster.cli_materialize.find_dagster_container", return_value="mock-container")
@patch("phlo_dagster.cli_materialize.get_project_name", return_value="mock-project")
def test_materialize_sets_contract_refresh_env_by_default(mock_project, mock_container) -> None:
    """Dry-run command enables contract refresh by default."""
    runner = CliRunner()
    result = runner.invoke(materialize, ["dlt_orders", "--dry-run"])

    assert result.exit_code == 0
    assert "PHLO_AUTO_REFRESH_CONTRACTS=1" in result.output
    assert "PHLO_CONTRACT_REFRESH_SELECTION=dlt_orders" in result.output


@patch("phlo_dagster.cli_materialize.find_dagster_container", return_value="mock-container")
@patch("phlo_dagster.cli_materialize.get_project_name", return_value="mock-project")
def test_materialize_can_disable_contract_refresh(mock_project, mock_container) -> None:
    """Dry-run command supports opt-out contract refresh flag."""
    runner = CliRunner()
    result = runner.invoke(
        materialize,
        ["dlt_orders", "--dry-run", "--no-contract-refresh"],
    )

    assert result.exit_code == 0
    assert "PHLO_AUTO_REFRESH_CONTRACTS=0" in result.output


def test_enabled_wap_materialize_uses_graphql_launch_and_retains_a_rejected_branch(
    monkeypatch,
) -> None:
    cleaned: list[bool] = []
    records: list[dict[str, str | None]] = []

    class Launch:
        logical_run_id = "request-42"
        branch = "pipeline-run-request-42"
        tags = {
            "phlo/run_id": logical_run_id,
            "phlo/wap_branch": branch,
            "phlo/ref": branch,
        }

        def cleanup_if_created(self) -> None:
            cleaned.append(True)

        def record_launch_result(self, **kwargs) -> bool:
            records.append(kwargs)
            return True

    async def rejected_launch(**kwargs):
        assert kwargs["asset_key_path"] == "dlt_orders"
        assert kwargs["job_name"] == "__ASSET_JOB"
        assert kwargs["idempotency_key"] == "request-42"
        assert kwargs["tags"] == Launch.tags
        assert kwargs["repository_location_name"] == "phlo_dagster"
        assert kwargs["repository_name"] == "phlo_dagster"
        assert kwargs["access_token"] == "user-access-token"
        return type(
            "Result", (), {"accepted": False, "message": "Dagster rejected run", "run_id": None}
        )()

    monkeypatch.setattr(
        "phlo_dagster.cli_materialize.load_wap_config",
        lambda: type(
            "Config",
            (),
            {
                "enabled": True,
                "dagster_url": "http://dagster/graphql",
                "job_name": "__ASSET_JOB",
                "repository_location_name": "phlo_dagster",
                "repository_name": "phlo_dagster",
            },
        )(),
    )
    monkeypatch.setattr(
        "phlo_dagster.cli_materialize.uuid.uuid4",
        lambda: type("ID", (), {"hex": "request-42"})(),
    )
    monkeypatch.setattr("phlo_dagster.cli_materialize.prepare_wap_launch", lambda **_: Launch())
    monkeypatch.setattr("phlo_dagster.cli_materialize.launch_materialize", rejected_launch)
    monkeypatch.setenv("PHLO_DAGSTER_ACCESS_TOKEN", "user-access-token")

    result = CliRunner().invoke(
        materialize,
        [
            "dlt_orders",
        ],
    )

    assert result.exit_code != 0
    assert "Dagster rejected run" in result.output
    assert cleaned == []
    assert records == [{"status": "launch_rejected", "error": "Dagster rejected run"}]


def test_enabled_wap_materialize_retains_new_branch_after_ambiguous_transport_failure(
    monkeypatch,
) -> None:
    cleaned: list[bool] = []
    records: list[dict[str, str | None]] = []

    class Launch:
        logical_run_id = "request-42"
        branch = "pipeline-run-request-42"
        tags = {
            "phlo/run_id": logical_run_id,
            "phlo/wap_branch": branch,
            "phlo/ref": branch,
        }

        def cleanup_if_created(self) -> None:
            cleaned.append(True)

        def record_launch_result(self, **kwargs) -> bool:
            records.append(kwargs)
            return True

    async def timeout_launch(**_kwargs):
        raise httpx.ReadTimeout("response lost")

    monkeypatch.setattr(
        "phlo_dagster.cli_materialize.load_wap_config",
        lambda: type(
            "Config",
            (),
            {
                "enabled": True,
                "dagster_url": "http://dagster/graphql",
                "job_name": "__ASSET_JOB",
                "repository_location_name": None,
                "repository_name": None,
            },
        )(),
    )
    monkeypatch.setattr("phlo_dagster.cli_materialize.prepare_wap_launch", lambda **_: Launch())
    monkeypatch.setattr("phlo_dagster.cli_materialize.launch_materialize", timeout_launch)
    monkeypatch.setenv("PHLO_DAGSTER_ACCESS_TOKEN", "user-access-token")

    result = CliRunner().invoke(
        materialize,
        [
            "dlt_orders",
        ],
    )

    assert result.exit_code != 0
    assert cleaned == []
    assert records[0]["status"] == "launch_ambiguous"
    assert "response lost" in str(records[0]["error"])


def test_enabled_wap_materialize_requires_one_explicit_asset(monkeypatch) -> None:
    monkeypatch.setattr(
        "phlo_dagster.cli_materialize.prepare_wap_launch",
        lambda **_: (_ for _ in ()).throw(AssertionError("must not create a branch")),
    )
    runner = CliRunner()

    monkeypatch.setattr(
        "phlo_dagster.cli_materialize.load_wap_config",
        lambda: type("Config", (), {"enabled": True})(),
    )
    incomplete_selector = runner.invoke(materialize, ["dlt_orders", "--select", "dlt_orders"])

    assert incomplete_selector.exit_code != 0
    assert "requires one ASSET_NAME" in incomplete_selector.output

    help_result = runner.invoke(materialize, ["--help"])
    assert "--wap" not in help_result.output


def test_enabled_wap_materialize_uses_project_configuration(
    monkeypatch,
) -> None:
    captured: dict[str, str] = {}
    discovery_calls: list[bool] = []

    class Launch:
        logical_run_id = "request-42"
        branch = "pipeline-run-request-42"
        tags = {
            "phlo/run_id": logical_run_id,
            "phlo/wap_branch": branch,
            "phlo/ref": branch,
        }

        def cleanup_if_created(self) -> None:
            raise AssertionError("accepted launches retain their branch")

        def record_launch_result(self, **_kwargs) -> bool:
            return True

    async def accepted_launch(**kwargs):
        captured.update(
            {
                "repository_location_name": kwargs["repository_location_name"],
                "repository_name": kwargs["repository_name"],
                "access_token": kwargs["access_token"],
                "tags": kwargs["tags"],
            }
        )
        return type("Result", (), {"accepted": True, "message": "", "run_id": "run-1"})()

    def prepared_launch(**_kwargs):
        assert discovery_calls == [True]
        return Launch()

    monkeypatch.setenv("PHLO_DAGSTER_ACCESS_TOKEN", "verified-user-token")
    monkeypatch.setattr(
        "phlo_dagster.cli_materialize.load_wap_config",
        lambda: type(
            "Config",
            (),
            {
                "enabled": True,
                "dagster_url": "http://dagster/graphql",
                "job_name": "__ASSET_JOB",
                "repository_location_name": "phlo_dagster",
                "repository_name": "phlo_dagster",
            },
        )(),
    )
    monkeypatch.setattr(
        "phlo_dagster.cli_materialize.uuid.uuid4",
        lambda: type("ID", (), {"hex": "request-42"})(),
    )
    monkeypatch.setattr(
        "phlo_dagster.cli_materialize.discover_capabilities", lambda: discovery_calls.append(True)
    )
    monkeypatch.setattr("phlo_dagster.cli_materialize.prepare_wap_launch", prepared_launch)
    monkeypatch.setattr("phlo_dagster.cli_materialize.launch_materialize", accepted_launch)

    result = CliRunner().invoke(
        materialize,
        ["dlt_orders"],
    )

    assert result.exit_code == 0, result.output
    assert captured == {
        "repository_location_name": "phlo_dagster",
        "repository_name": "phlo_dagster",
        "access_token": "verified-user-token",
        "tags": {
            "phlo/run_id": "request-42",
            "phlo/wap_branch": "pipeline-run-request-42",
            "phlo/ref": "pipeline-run-request-42",
        },
    }
    assert discovery_calls == [True]


@patch("phlo_dagster.cli_materialize.find_dagster_container", return_value="lakehouse-dagster-1")
@patch("phlo_dagster.cli_materialize.get_project_name", return_value="mock-project")
def test_materialize_accepts_select_without_asset_argument(mock_project, mock_container) -> None:
    """Dry runs resolve the active Compose Dagster container."""
    runner = CliRunner()
    result = runner.invoke(materialize, ["--select", "tag:bronze", "--dry-run"])

    assert result.exit_code == 0
    assert "PHLO_CONTRACT_REFRESH_SELECTION=tag:bronze" in result.output
    assert "--select tag:bronze" in result.output
    assert "lakehouse-dagster-1" in result.output


@patch("phlo_dagster.cli_materialize.find_dagster_container", return_value="mock-container")
@patch("phlo_dagster.cli_materialize.get_project_name", return_value="mock-project")
def test_materialize_failure_hides_raw_process_output(
    mock_project, mock_container, monkeypatch
) -> None:
    """Container stdout should go to structured debug logs, not normal CLI output."""

    class FakeStdout:
        def __iter__(self):
            return iter(['{"event":"internal"}\n', "User-facing failure\n"])

    class FakeProcess:
        stdout = FakeStdout()

        def wait(self) -> int:
            return 2

    def fake_popen(cmd, stdout, stderr, text):
        assert stdout is PIPE
        assert stderr is STDOUT
        assert text is True
        return FakeProcess()

    monkeypatch.setattr("phlo_dagster.cli_materialize.subprocess.Popen", fake_popen)
    monkeypatch.setattr(
        "phlo_dagster.cli_materialize.wait_for_dagster_runtime",
        lambda *args, **kwargs: None,
    )

    result = CliRunner().invoke(materialize, ["dlt_orders"])

    assert result.exit_code != 0
    assert '{"event":"internal"}' not in result.output
    assert "Error: materialization failed" in result.output
    assert "Exit code: 2" in result.output
    assert "Last output: User-facing failure" in result.output
    assert "Run: phlo logs --service dagster --tail 20" in result.output


@patch(
    "phlo_dagster.cli_materialize.find_dagster_container",
    side_effect=RuntimeError("Could not find running dagster container"),
)
@patch("phlo_dagster.cli_materialize.get_project_name", return_value="mock-project")
def test_materialize_missing_dagster_container_is_actionable(mock_project, mock_container) -> None:
    runner = CliRunner()

    result = runner.invoke(materialize, ["dlt_orders"])

    assert result.exit_code != 0
    assert "Traceback" not in result.output
    assert "Error: dagster is not available" in result.output
    assert "Make sure the dagster service is running." in result.output
    assert "Run: phlo services start" in result.output


@patch("phlo_dagster.cli_materialize.find_dagster_container", return_value="mock-container")
@patch("phlo_dagster.cli_materialize.get_project_name", return_value="mock-project")
def test_materialize_dry_run_uses_configured_container_backend(
    mock_project,
    mock_container,
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        "phlo_dagster.cli_materialize.select_project_container_backend",
        lambda: FakePodmanBackend(),
    )

    result = CliRunner().invoke(materialize, ["dlt_orders", "--dry-run"])

    assert result.exit_code == 0, result.output
    assert "podman exec" in result.output
    assert "docker exec" not in result.output


@patch("phlo_dagster.cli_materialize.find_dagster_container", return_value="mock-container")
@patch("phlo_dagster.cli_materialize.get_project_name", return_value="mock-project")
def test_materialize_defaults_partition_to_today(mock_project, mock_container) -> None:
    """Omitted --partition defaults to today so partitioned assets never run bare."""
    from datetime import UTC, datetime

    runner = CliRunner()
    result = runner.invoke(materialize, ["dlt_orders", "--dry-run"])

    assert result.exit_code == 0
    today = datetime.now(UTC).strftime("%Y-%m-%d")
    assert f"--partition {today}" in result.output


@patch("phlo_dagster.cli_materialize.find_dagster_container", return_value="mock-container")
@patch("phlo_dagster.cli_materialize.get_project_name", return_value="mock-project")
def test_materialize_can_skip_default_partition(mock_project, mock_container) -> None:
    """--no-default-partition restores the bare, unpartitioned launch."""
    runner = CliRunner()
    result = runner.invoke(
        materialize,
        ["dlt_orders", "--dry-run", "--no-default-partition"],
    )

    assert result.exit_code == 0
    assert "--partition" not in result.output
