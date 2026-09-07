"""Agent CLI results preserve lifecycle truth and resumable work without live services."""

import json
from types import SimpleNamespace

from click.testing import CliRunner

from phlo_dagster import cli_backfill, cli_materialize, cli_status


def test_backfill_json_preview_does_not_launch_or_write(monkeypatch, tmp_path):
    monkeypatch.setattr(cli_backfill, "load_wap_config", lambda: SimpleNamespace(enabled=True))
    monkeypatch.setattr(cli_backfill, "BACKFILL_STATE_FILE", tmp_path / "state.json")

    def forbidden(*args, **kwargs):
        raise AssertionError("preview must not mutate runtime or state")

    monkeypatch.setattr(cli_backfill, "prepare_wap_launch", forbidden)
    monkeypatch.setattr(cli_backfill, "_save_backfill_state", forbidden)
    result = CliRunner().invoke(
        cli_backfill.backfill, ["orders", "--partitions", "2024-01-01", "--dry-run", "--json"]
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["status"] == "planned"
    assert payload["data"]["partitions"] == ["2024-01-01"]
    assert not (tmp_path / "state.json").exists()


def test_backfill_partial_json_preserves_resume_history(monkeypatch, tmp_path):
    state = tmp_path / "state.json"
    state.write_text(
        json.dumps(
            {
                "asset_name": "orders",
                "remaining_partitions": ["2024-01-02", "2024-01-03"],
                "completed_partitions": ["2024-01-01"],
            }
        )
    )
    monkeypatch.setattr(cli_backfill, "BACKFILL_STATE_FILE", state)
    monkeypatch.setattr(cli_backfill, "load_wap_config", lambda: SimpleNamespace(enabled=False))
    monkeypatch.setattr(cli_backfill, "select_project_container_backend", lambda: object())
    monkeypatch.setattr(cli_backfill, "get_project_name", lambda: "test")
    monkeypatch.setattr(cli_backfill, "find_dagster_container", lambda _: "test-dagster")
    monkeypatch.setattr(cli_backfill, "wait_for_dagster_runtime", lambda *a, **kw: None)
    monkeypatch.setattr(
        cli_backfill,
        "_materialize_partition",
        lambda asset, date, *a: (date == "2024-01-02", "partition failure"),
    )
    result = CliRunner().invoke(cli_backfill.backfill, ["--resume", "--json"])
    assert result.exit_code == 1, result.output
    payload = json.loads(result.stdout)
    assert payload["status"] == "partial"
    assert payload["data"]["successful"] == ["2024-01-02"]
    saved = json.loads(state.read_text())
    assert saved["completed_partitions"] == ["2024-01-01", "2024-01-02"]
    assert saved["remaining_partitions"] == ["2024-01-03"]


def test_materialize_wap_json_is_submitted_not_completed(monkeypatch):
    monkeypatch.setattr(
        cli_materialize,
        "load_wap_config",
        lambda: SimpleNamespace(
            enabled=True, job_name="job", repository_location_name=None, repository_name=None
        ),
    )
    monkeypatch.setattr(
        cli_materialize, "resolve_wap_dagster_url", lambda _: "http://localhost:3000"
    )
    monkeypatch.setattr(cli_materialize, "discover_capabilities", lambda: None)
    monkeypatch.setattr(
        cli_materialize,
        "prepare_wap_launch",
        lambda **kw: SimpleNamespace(
            tags={}, branch="wap/test", record_launch_result=lambda **kw: True
        ),
    )

    async def launch(**kwargs):
        return SimpleNamespace(accepted=True, run_id="run-123")

    monkeypatch.setattr(cli_materialize, "launch_materialize", launch)
    result = CliRunner().invoke(cli_materialize.materialize, ["orders", "--json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["status"] == "submitted"
    assert payload["data"]["run_id"] == "run-123"


def test_status_json_query_failure_is_not_empty_success(monkeypatch):
    def unavailable(*a, **kw):
        raise cli_status.requests_exceptions.ConnectionError("offline")

    monkeypatch.setattr(cli_status.http_requests, "post", unavailable)
    result = CliRunner().invoke(cli_status.status, ["--assets", "--json"])
    assert result.exit_code == 1, result.output
    payload = json.loads(result.stdout)
    assert payload["status"] == "error"
    assert payload["reason_code"] == "status_incomplete"
    assert payload["errors"]


def test_backfill_json_validation_error(monkeypatch):
    result = CliRunner().invoke(
        cli_backfill.backfill, ["orders", "--partitions", "not-a-date", "--json"]
    )
    assert result.exit_code != 0
    assert json.loads(result.stdout)["status"] == "error"


def test_materialize_wap_without_run_id_is_not_submitted(monkeypatch):
    monkeypatch.setattr(
        cli_materialize,
        "load_wap_config",
        lambda: SimpleNamespace(
            enabled=True, job_name="job", repository_location_name=None, repository_name=None
        ),
    )
    monkeypatch.setattr(
        cli_materialize, "resolve_wap_dagster_url", lambda _: "http://localhost:3000"
    )
    monkeypatch.setattr(cli_materialize, "discover_capabilities", lambda: None)
    monkeypatch.setattr(
        cli_materialize,
        "prepare_wap_launch",
        lambda **kw: SimpleNamespace(
            tags={}, branch="wap/test", record_launch_result=lambda **kw: True
        ),
    )

    async def launch(**kwargs):
        return SimpleNamespace(accepted=True, run_id=None)

    monkeypatch.setattr(cli_materialize, "launch_materialize", launch)
    result = CliRunner().invoke(cli_materialize.materialize, ["orders", "--json"])
    assert result.exit_code != 0
    payload = json.loads(result.stdout)
    assert payload["status"] == "error"
    assert "without a run ID" in str(payload["errors"])
