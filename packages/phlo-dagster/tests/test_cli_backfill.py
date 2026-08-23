"""Tests for the phlo backfill CLI command.

Covers partition date generation/validation, write-audit-publish backfills
that create a branch per partition and wait for promotion before starting
the next, persisted state for failed partitions and timed-out runs,
lifecycle polling (transient retries, failure rejection), and state file
management.
"""

import json
from types import SimpleNamespace
from datetime import datetime
from unittest.mock import ANY, patch

import click
from click.testing import CliRunner

from phlo_dagster.cli_backfill import (
    _generate_partition_dates,
    _run_backfill,
    _validate_partition_dates,
    backfill,
)


class TestBackfillDateGeneration:
    """Test date range generation."""

    def test_generate_single_day(self):
        """Generate partition for a single day."""
        dates = _generate_partition_dates("2024-01-01", "2024-01-01")
        assert dates == ["2024-01-01"]

    def test_generate_week(self):
        """Generate partitions for a week."""
        dates = _generate_partition_dates("2024-01-01", "2024-01-07")
        assert len(dates) == 7
        assert dates[0] == "2024-01-01"
        assert dates[-1] == "2024-01-07"

    def test_generate_year(self):
        """Generate partitions for entire year."""
        dates = _generate_partition_dates("2024-01-01", "2024-12-31")
        assert len(dates) == 366  # 2024 is leap year
        assert dates[0] == "2024-01-01"
        assert dates[-1] == "2024-12-31"

    def test_generate_month(self):
        """Generate partitions for a month."""
        dates = _generate_partition_dates("2024-01-01", "2024-01-31")
        assert len(dates) == 31

    def test_invalid_start_after_end(self):
        """Reject when start date is after end date."""
        runner = CliRunner()
        result = runner.invoke(
            backfill,
            ["test_asset", "--start-date", "2024-01-05", "--end-date", "2024-01-01"],
        )
        assert result.exit_code == 1
        assert "Start date must be before end date" in result.output


def test_wap_backfill_creates_branch_before_each_partition(monkeypatch):
    from phlo_dagster.cli_backfill import _run_wap_backfill

    launched: list[dict[str, object]] = []
    monkeypatch.setattr(
        "phlo_dagster.cli_backfill.prepare_wap_launch",
        lambda **kwargs: SimpleNamespace(
            branch=f"pipeline-run-{kwargs['logical_run_id']}",
            tags={"phlo/wap_branch": "branch", "phlo/ref": "branch"},
            cleanup_if_created=lambda: None,
            record_launch_result=lambda **_kwargs: True,
        ),
    )

    async def launch(**kwargs):
        launched.append(kwargs)
        return SimpleNamespace(accepted=True, message="ok", run_id=kwargs["partition_key"])

    monkeypatch.setattr("phlo_dagster.cli_backfill.launch_materialize", launch)
    promoted: list[str] = []
    monkeypatch.setattr(
        "phlo_dagster.cli_backfill._wait_for_wap_lifecycle",
        lambda **kwargs: promoted.append(kwargs["logical_run_id"]),
    )
    _run_wap_backfill(
        "dlt_events",
        ["2024-01-01", "2024-01-02"],
        dagster_url="http://dagster",
        job_name="__ASSET_JOB",
        repository_location_name="phlo_dagster",
        repository_name="phlo_dagster",
        access_token="token",
    )
    assert [call["partition_key"] for call in launched] == ["2024-01-01", "2024-01-02"]
    assert all(
        call["tags"] == {"phlo/wap_branch": "branch", "phlo/ref": "branch"} for call in launched
    )
    assert len(promoted) == 2


def test_wap_backfill_waits_for_promotion_before_next_branch(monkeypatch, tmp_path):
    """A later branch starts from the target hash promoted by the prior partition."""
    from phlo_dagster.cli_backfill import _run_wap_backfill

    main_hash = "main-0"
    created_from: list[str] = []
    launched: list[str] = []

    def prepare(**kwargs):
        nonlocal main_hash
        created_from.append(main_hash)
        return SimpleNamespace(
            branch=f"pipeline-run-{kwargs['logical_run_id']}",
            tags={},
            record_launch_result=lambda **_kwargs: True,
        )

    async def launch(**kwargs):
        launched.append(kwargs["partition_key"])
        return SimpleNamespace(accepted=True, message="ok", run_id=kwargs["partition_key"])

    def wait(**_kwargs):
        nonlocal main_hash
        main_hash = f"main-{len(launched)}"

    monkeypatch.setattr("phlo_dagster.cli_backfill.prepare_wap_launch", prepare)
    monkeypatch.setattr("phlo_dagster.cli_backfill.launch_materialize", launch)
    monkeypatch.setattr("phlo_dagster.cli_backfill._wait_for_wap_lifecycle", wait)
    monkeypatch.setattr("phlo_dagster.cli_backfill.BACKFILL_STATE_FILE", tmp_path / "state.json")

    _run_wap_backfill(
        "dlt_events",
        ["2024-01-01", "2024-01-02"],
        dagster_url="http://dagster",
        job_name="__ASSET_JOB",
        repository_location_name=None,
        repository_name=None,
        access_token=None,
        requested_parallel=2,
    )

    assert launched == ["2024-01-01", "2024-01-02"]
    assert created_from == ["main-0", "main-1"]


def test_wap_backfill_persists_failed_partition_for_resume(monkeypatch, tmp_path):
    from phlo_dagster.cli_backfill import WapLifecycleTerminalError, _run_wap_backfill

    monkeypatch.setattr(
        "phlo_dagster.cli_backfill.prepare_wap_launch",
        lambda **_kwargs: SimpleNamespace(
            branch="branch", tags={}, record_launch_result=lambda **_kwargs: True
        ),
    )

    async def launch(**kwargs):
        return SimpleNamespace(accepted=True, message="ok", run_id=kwargs["partition_key"])

    monkeypatch.setattr("phlo_dagster.cli_backfill.launch_materialize", launch)
    monkeypatch.setattr(
        "phlo_dagster.cli_backfill._wait_for_wap_lifecycle",
        lambda **kwargs: (
            None
            if kwargs["dagster_run_id"] == "2024-01-01"
            else (_ for _ in ()).throw(WapLifecycleTerminalError("promotion_failed"))
        ),
    )
    state_file = tmp_path / ".phlo" / "backfill_state.json"
    monkeypatch.setattr("phlo_dagster.cli_backfill.BACKFILL_STATE_FILE", state_file)

    try:
        _run_wap_backfill(
            "dlt_events",
            ["2024-01-01", "2024-01-02"],
            dagster_url="http://dagster",
            job_name="__ASSET_JOB",
            repository_location_name=None,
            repository_name=None,
            access_token=None,
        )
    except click.ClickException as exc:
        assert "promotion_failed" in str(exc)
    else:
        raise AssertionError("expected WAP promotion failure")

    state = json.loads(state_file.read_text())
    assert state["asset_name"] == "dlt_events"
    assert state["remaining_partitions"] == ["2024-01-02"]
    assert state["completed_partitions"] == ["2024-01-01"]
    assert state["in_flight_wap"] == {}
    assert state["last_updated"] == ANY


def test_wap_lifecycle_rejects_failed_dagster_run(monkeypatch):
    from phlo_dagster.cli_backfill import _wait_for_wap_lifecycle

    async def failed_run(**_kwargs):
        return "FAILURE"

    monkeypatch.setattr("phlo_dagster.cli_backfill.get_run_status", failed_run)

    try:
        _wait_for_wap_lifecycle(
            logical_run_id="logical-1",
            dagster_run_id="dagster-1",
            dagster_url="http://dagster",
            access_token=None,
        )
    except click.ClickException as exc:
        assert "Dagster run failed" in str(exc)
    else:
        raise AssertionError("expected failed Dagster run")


def test_wap_lifecycle_rejects_failed_promotion(monkeypatch):
    from phlo_dagster.cli_backfill import _wait_for_wap_lifecycle

    async def successful_run(**_kwargs):
        return "SUCCESS"

    monkeypatch.setattr("phlo_dagster.cli_backfill.get_run_status", successful_run)
    monkeypatch.setattr(
        "phlo_dagster.cli_backfill.read_wap_report",
        lambda _logical_run_id: {
            "status": "promotion_failed",
            "failure_reason": "merge_branch_returned_false",
        },
    )

    try:
        _wait_for_wap_lifecycle(
            logical_run_id="logical-1",
            dagster_run_id="dagster-1",
            dagster_url="http://dagster",
            access_token=None,
        )
    except click.ClickException as exc:
        assert "merge_branch_returned_false" in str(exc)
    else:
        raise AssertionError("expected failed WAP promotion")


def test_wap_lifecycle_accepts_promoted_report(monkeypatch):
    from phlo_dagster.cli_backfill import _wait_for_wap_lifecycle

    async def successful_run(**_kwargs):
        return "SUCCESS"

    monkeypatch.setattr("phlo_dagster.cli_backfill.get_run_status", successful_run)
    monkeypatch.setattr(
        "phlo_dagster.cli_backfill.read_wap_report", lambda _logical_run_id: {"status": "promoted"}
    )

    _wait_for_wap_lifecycle(
        logical_run_id="logical-1",
        dagster_run_id="dagster-1",
        dagster_url="http://dagster",
        access_token=None,
    )


def test_wap_lifecycle_retries_transient_poll_failure(monkeypatch):
    from phlo_dagster.cli_backfill import _wait_for_wap_lifecycle

    calls = 0

    async def eventually_successful_run(**_kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("temporary transport failure")
        return "SUCCESS"

    monkeypatch.setattr("phlo_dagster.cli_backfill.get_run_status", eventually_successful_run)
    monkeypatch.setattr(
        "phlo_dagster.cli_backfill.read_wap_report", lambda _logical_run_id: {"status": "promoted"}
    )
    monkeypatch.setattr("phlo_dagster.cli_backfill.time.sleep", lambda _seconds: None)

    _wait_for_wap_lifecycle(
        logical_run_id="logical-1",
        dagster_run_id="dagster-1",
        dagster_url="http://dagster",
        access_token=None,
    )

    assert calls == 2


def test_wap_lifecycle_times_out_without_promotion(monkeypatch):
    from phlo_dagster.cli_backfill import _wait_for_wap_lifecycle

    monkeypatch.setenv("PHLO_WAP_BACKFILL_TIMEOUT_SECONDS", "0")

    try:
        _wait_for_wap_lifecycle(
            logical_run_id="logical-1",
            dagster_run_id="dagster-1",
            dagster_url="http://dagster",
            access_token=None,
        )
    except click.ClickException as exc:
        assert "Timed out" in str(exc)
    else:
        raise AssertionError("expected WAP lifecycle timeout")


def test_wap_timeout_resume_reconciles_existing_run_without_relaunch(monkeypatch, tmp_path):
    from phlo_dagster.cli_backfill import _run_wap_backfill

    launches: list[str] = []
    prepared: list[str] = []

    def prepare(**kwargs):
        prepared.append(kwargs["logical_run_id"])
        return SimpleNamespace(
            branch="branch", tags={}, record_launch_result=lambda **_kwargs: True
        )

    async def launch(**kwargs):
        launches.append(kwargs["partition_key"])
        return SimpleNamespace(accepted=True, message="ok", run_id="dagster-1")

    monkeypatch.setattr("phlo_dagster.cli_backfill.prepare_wap_launch", prepare)
    monkeypatch.setattr("phlo_dagster.cli_backfill.launch_materialize", launch)
    state_file = tmp_path / ".phlo" / "backfill_state.json"
    monkeypatch.setattr("phlo_dagster.cli_backfill.BACKFILL_STATE_FILE", state_file)
    monkeypatch.setattr(
        "phlo_dagster.cli_backfill._wait_for_wap_lifecycle",
        lambda **_kwargs: (_ for _ in ()).throw(click.ClickException("Timed out")),
    )

    try:
        _run_wap_backfill(
            "dlt_events",
            ["2024-01-01"],
            dagster_url="http://dagster",
            job_name="__ASSET_JOB",
            repository_location_name=None,
            repository_name=None,
            access_token=None,
        )
    except click.ClickException:
        pass
    else:
        raise AssertionError("expected timeout")

    state = json.loads(state_file.read_text())
    monkeypatch.setattr("phlo_dagster.cli_backfill._wait_for_wap_lifecycle", lambda **_kwargs: None)
    _run_wap_backfill(
        "dlt_events",
        state["remaining_partitions"],
        dagster_url="http://dagster",
        job_name="__ASSET_JOB",
        repository_location_name=None,
        repository_name=None,
        access_token=None,
        completed_partitions=state["completed_partitions"],
        in_flight_wap=state["in_flight_wap"],
    )

    assert launches == ["2024-01-01"]
    assert len(prepared) == 1


def test_enabled_project_wap_backfill_uses_graphql_without_cli_flags(monkeypatch) -> None:
    """The project policy selects the WAP path for every partition."""
    launches: list[tuple[str, list[str]]] = []
    discovery_calls: list[bool] = []
    monkeypatch.setattr(
        "phlo_dagster.cli_backfill.load_wap_config",
        lambda: SimpleNamespace(
            enabled=True,
            dagster_url="http://dagster/graphql",
            job_name="__ASSET_JOB",
            repository_location_name=None,
            repository_name=None,
        ),
    )
    monkeypatch.setattr(
        "phlo_dagster.cli_backfill._run_wap_backfill",
        lambda asset_name, partitions, **_kwargs: launches.append((asset_name, partitions)),
    )
    monkeypatch.setattr(
        "phlo_dagster.cli_backfill.discover_capabilities",
        lambda: discovery_calls.append(True),
    )

    result = CliRunner().invoke(
        backfill,
        ["dlt_events", "--partitions", "2024-01-01,2024-01-02"],
    )

    assert result.exit_code == 0, result.output
    assert launches == [("dlt_events", ["2024-01-01", "2024-01-02"])]
    assert discovery_calls == [True]


def test_disabled_project_wap_backfill_retains_direct_path(monkeypatch) -> None:
    """Disabled WAP keeps the established container-exec implementation."""
    direct_calls: list[tuple[str, list[str]]] = []
    monkeypatch.setattr(
        "phlo_dagster.cli_backfill.load_wap_config",
        lambda: SimpleNamespace(enabled=False),
    )
    monkeypatch.setattr(
        "phlo_dagster.cli_backfill._run_backfill",
        lambda asset_name, partitions, **_kwargs: direct_calls.append((asset_name, partitions)),
    )

    result = CliRunner().invoke(backfill, ["dlt_events", "--partitions", "2024-01-01"])

    assert result.exit_code == 0, result.output
    assert direct_calls == [("dlt_events", ["2024-01-01"])]


class TestBackfillValidation:
    """Test partition date validation."""

    def test_valid_date_format(self):
        """Accept valid YYYY-MM-DD format."""
        # Should not raise
        _validate_partition_dates(["2024-01-01", "2024-12-31"])

    def test_invalid_date_format(self):
        """Reject invalid date format."""
        runner = CliRunner()
        result = runner.invoke(
            backfill,
            [
                "test_asset",
                "--partitions",
                "2024-01-01,01-01-2024",  # Invalid format
            ],
        )
        assert result.exit_code == 1
        assert "Invalid partition date" in result.output

    def test_whitespace_handling(self):
        """Handle whitespace in date strings."""
        # Should not raise
        _validate_partition_dates(["2024-01-01", " 2024-01-02 ", "2024-01-03"])


class TestBackfillCLI:
    """Test backfill CLI command."""

    def test_help_message(self):
        """Display help message."""
        runner = CliRunner()
        result = runner.invoke(backfill, ["--help"])
        assert result.exit_code == 0
        assert "Run asset materialization across a date range" in result.output
        assert "--start-date" in result.output
        assert "--end-date" in result.output
        assert "--parallel" in result.output
        assert "--resume" in result.output

    def test_missing_asset_name(self):
        """Require asset name when not resuming."""
        runner = CliRunner()
        result = runner.invoke(
            backfill,
            ["--start-date", "2024-01-01", "--end-date", "2024-01-05"],
        )
        assert result.exit_code == 1
        assert "Asset name is required" in result.output

    def test_missing_date_arguments(self):
        """Require date range or explicit partitions."""
        runner = CliRunner()
        result = runner.invoke(backfill, ["test_asset"])
        assert result.exit_code == 1
        assert "Must specify either --start-date/--end-date or --partitions" in result.output

    @patch("phlo_dagster.cli_backfill.find_dagster_container", return_value="mock-container")
    @patch("phlo_dagster.cli_backfill.get_project_name", return_value="mock-project")
    def test_dry_run_with_date_range(self, mock_project, mock_container):
        """Display commands in dry-run mode."""
        runner = CliRunner()
        result = runner.invoke(
            backfill,
            [
                "glucose_entries",
                "--start-date",
                "2024-01-01",
                "--end-date",
                "2024-01-03",
                "--dry-run",
            ],
        )
        assert result.exit_code == 0
        assert "Dry run" in result.output
        assert "docker exec" in result.output
        assert "2024-01-01" in result.output
        assert "2024-01-02" in result.output
        assert "2024-01-03" in result.output

    @patch(
        "phlo_dagster.cli_backfill.find_dagster_container",
        side_effect=AssertionError("dry-run should not inspect Docker"),
    )
    @patch("phlo_dagster.cli_backfill.get_project_name", return_value="mock-project")
    def test_dry_run_without_running_container(self, mock_project, mock_container):
        """Dry-run previews commands without requiring Docker state."""
        runner = CliRunner()
        result = runner.invoke(
            backfill,
            [
                "glucose_entries",
                "--start-date",
                "2024-01-01",
                "--end-date",
                "2024-01-01",
                "--dry-run",
            ],
        )
        assert result.exit_code == 0
        assert "docker exec" in result.output

    @patch("phlo_dagster.cli_backfill.find_dagster_container", return_value="mock-container")
    @patch("phlo_dagster.cli_backfill.get_project_name", return_value="mock-project")
    def test_dry_run_with_partitions(self, mock_project, mock_container):
        """Display commands with explicit partitions."""
        runner = CliRunner()
        result = runner.invoke(
            backfill,
            [
                "glucose_entries",
                "--partitions",
                "2024-01-01,2024-01-15,2024-01-31",
                "--dry-run",
            ],
        )
        assert result.exit_code == 0
        assert "Total partitions: 3" in result.output
        assert "2024-01-01" in result.output
        assert "2024-01-15" in result.output
        assert "2024-01-31" in result.output

    @patch("phlo_dagster.cli_backfill.find_dagster_container", return_value="mock-container")
    @patch("phlo_dagster.cli_backfill.get_project_name", return_value="mock-project")
    def test_parallel_option(self, mock_project, mock_container):
        """Accept parallel worker count."""
        runner = CliRunner()
        result = runner.invoke(
            backfill,
            [
                "glucose_entries",
                "--start-date",
                "2024-01-01",
                "--end-date",
                "2024-01-10",
                "--parallel",
                "4",
                "--dry-run",
            ],
        )
        assert result.exit_code == 0
        assert "Parallel workers: 4" in result.output

    def test_invalid_parallel_value(self):
        """Reject invalid parallel worker count."""
        runner = CliRunner()
        result = runner.invoke(
            backfill,
            [
                "glucose_entries",
                "--start-date",
                "2024-01-01",
                "--end-date",
                "2024-01-05",
                "--parallel",
                "0",
                "--dry-run",
            ],
        )
        assert result.exit_code == 1
        assert "Parallel must be >= 1" in result.output

    @patch("phlo_dagster.cli_backfill.find_dagster_container", return_value="mock-container")
    @patch("phlo_dagster.cli_backfill.get_project_name", return_value="mock-project")
    @patch("phlo_dagster.cli_backfill.wait_for_dagster_runtime")
    @patch("phlo_dagster.cli_backfill._materialize_partition", return_value=(True, "ok"))
    def test_backfill_waits_for_runtime_before_partitions(
        self,
        mock_materialize,
        mock_wait,
        mock_project,
        mock_container,
        tmp_path,
        monkeypatch,
    ):
        """Wait for Dagster setup before partition docker exec calls."""
        monkeypatch.chdir(tmp_path)

        _run_backfill("dlt_events", ["2024-01-01"], parallel=1)

        mock_wait.assert_called_once_with("mock-container", backend=ANY)
        mock_materialize.assert_called_once_with(
            "dlt_events",
            "2024-01-01",
            0,
            "mock-container",
            ANY,
        )

    @patch("phlo_dagster.cli_backfill.find_dagster_container", return_value="mock-container")
    @patch("phlo_dagster.cli_backfill.get_project_name", return_value="mock-project")
    @patch(
        "phlo_dagster.cli_backfill.wait_for_dagster_runtime",
        side_effect=FileNotFoundError("docker"),
    )
    def test_backfill_missing_docker_is_actionable(
        self,
        mock_wait,
        mock_project,
        mock_container,
    ):
        """Missing Docker during readiness checks should not show a traceback."""
        runner = CliRunner()
        result = runner.invoke(
            backfill,
            ["dlt_events", "--partitions", "2024-01-01"],
        )

        assert result.exit_code != 0
        assert "Traceback" not in result.output
        assert "Error: dagster is not available" in result.output
        assert "Make sure the dagster service is running." in result.output
        assert "Run: phlo services start" in result.output

    def test_resume_without_state(self):
        """Reject resume without state file."""
        runner = CliRunner()
        with runner.isolated_filesystem():
            result = runner.invoke(backfill, ["--resume"])
            assert result.exit_code == 1
            assert "No backfill state found" in result.output

    @patch("phlo_dagster.cli_backfill.find_dagster_container", return_value="mock-container")
    @patch("phlo_dagster.cli_backfill.get_project_name", return_value="mock-project")
    def test_large_date_range(self, mock_project, mock_container):
        """Handle 365+ partitions efficiently."""
        runner = CliRunner()
        result = runner.invoke(
            backfill,
            [
                "glucose_entries",
                "--start-date",
                "2024-01-01",
                "--end-date",
                "2024-12-31",
                "--dry-run",
            ],
        )
        assert result.exit_code == 0
        assert "366" in result.output  # 2024 is leap year


class TestBackfillStateManagement:
    """Test backfill state file management."""

    def test_state_file_creation(self, tmp_path):
        """Create state file during backfill."""
        # Change to temp directory for test
        import os

        from phlo_dagster.cli_backfill import _save_backfill_state

        original_cwd = os.getcwd()
        try:
            os.chdir(tmp_path)

            _save_backfill_state(
                "test_asset",
                ["2024-01-02", "2024-01-03"],
                ["2024-01-01"],
            )

            state_file = tmp_path / ".phlo" / "backfill_state.json"
            assert state_file.exists()

            state = json.loads(state_file.read_text())
            assert state["asset_name"] == "test_asset"
            assert state["remaining_partitions"] == ["2024-01-02", "2024-01-03"]
            assert state["completed_partitions"] == ["2024-01-01"]
        finally:
            os.chdir(original_cwd)

    def test_state_file_format(self, tmp_path):
        """State file contains all required fields."""
        import os

        from phlo_dagster.cli_backfill import _save_backfill_state

        original_cwd = os.getcwd()
        try:
            os.chdir(tmp_path)

            _save_backfill_state(
                "glucose_entries",
                ["2024-01-05"],
                ["2024-01-01", "2024-01-02"],
            )

            state_file = tmp_path / ".phlo" / "backfill_state.json"
            state = json.loads(state_file.read_text())

            # Verify all required fields
            assert "asset_name" in state
            assert "remaining_partitions" in state
            assert "completed_partitions" in state
            assert "last_updated" in state

            # Verify timestamp format
            datetime.fromisoformat(state["last_updated"])
        finally:
            os.chdir(original_cwd)
