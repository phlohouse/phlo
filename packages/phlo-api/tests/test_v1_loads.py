"""Load observations never widen actor/asset/run scope or invent per-file counts."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from phlo_api.api import v1_loads as loads
from phlo_api.api.v1_assets import AssetView, AssetWriteCount
from phlo_api.api.v1_jobs import Run
from phlo_api.errors import BackendUnavailableError
from security_test_support import authenticated_client

NOW = datetime(2026, 10, 22, 12, tzinfo=UTC)


@pytest.fixture
def observations(monkeypatch, tmp_path):
    monkeypatch.setenv("PHLO_PROJECT_PATH", str(tmp_path))
    monkeypatch.setenv(
        "PHLO_V1_ENVIRONMENTS", '{"sandbox":{"dagster_location":"sandbox","nessie_ref":"main"}}'
    )
    asset = AssetView(
        id="bronze/lims",
        key=["bronze", "lims"],
        description=None,
        compute_kind="csv",
        group_name="bronze",
        is_source=False,
        is_ingestion=True,
        dependencies=[],
        last_materialization_at=NOW,
        last_run_id="main-run",
        relation="bronze.lims",
        source_name="LIMS",
        job_names=["ingest_lims"],
        materializations=[AssetWriteCount(run_id="main-run", timestamp=NOW, rows_inserted=96)],
    )
    run = Run(
        run_id="main-run",
        job_id="ingest_lims",
        status="SUCCESS",
        created_at=NOW - timedelta(minutes=10),
        started_at=NOW - timedelta(minutes=3),
        ended_at=NOW - timedelta(minutes=1),
        duration_seconds=1,
        selected_assets=[["bronze", "lims"]],
        tags={},
        logs_url="/run",
        resource_id="run",
    )
    monkeypatch.setattr(loads.assets, "_assets", AsyncMock(return_value=[asset]))
    monkeypatch.setattr(loads.jobs, "_run_page", AsyncMock(return_value=([run], None)))
    upload = SimpleNamespace(
        upload_id="own",
        actor="test:operator",
        env="sandbox",
        filename="lims.csv",
        byte_count=123,
        created_at=(NOW - timedelta(days=1)).isoformat(),
    )
    receipt = SimpleNamespace(
        upload_id="own",
        actor="test:operator",
        env="sandbox",
        asset_id="dlt_review",
        relation="bronze.review",
        checksum="checksum",
        candidate_ref="sandbox-upload-own",
        review_version=2,
        recipe_digest="recipe",
        run_id="candidate-run",
        status="loaded",
        row_count=96,
        loaded=True,
        job_name="replanned-new-job",
        candidate_hash="written",
        snapshot_id="9007199254740993",
        attempts=[SimpleNamespace(run_id="failed-run", status="FAILURE", launch_ref_hash="base")],
    )
    other = SimpleNamespace(upload_id="other", actor="other", env="sandbox")
    monkeypatch.setattr(loads.uploads, "_records", lambda: [upload, other])
    monkeypatch.setattr(loads.candidates, "_all", lambda: [receipt, other])
    return asset, run, receipt


def test_actual_receipts_and_ingestion_counts_preserve_identity_and_grain(observations):
    response = authenticated_client("operator").get("/api/v1/loads?env=sandbox")
    assert response.status_code == 200, response.text
    items = response.json()["items"]
    assert len(items) == 3
    private = next(item for item in items if item["evidence_kind"] == "csv_candidate")
    assert private["rows"] == 96 and private["snapshot_id"] == "9007199254740993"
    assert private["observed_at"] is None and private["byte_count"] == 123
    assert private["timestamp_basis"] == "unavailable"
    assert private["run_started_at"] is None and private["run_ended_at"] is None
    assert private["upload_filename"] == "lims.csv"
    assert private["upload_actor"] == "test:operator"
    assert datetime.fromisoformat(private["upload_created_at"]) == NOW - timedelta(days=1)
    failed = next(item for item in items if item["evidence_kind"] == "csv_attempt")
    assert failed["rows"] is None and failed["snapshot_id"] is None
    assert failed["job_id"] is None  # A replanned receipt job does not identify the old attempt.
    main = next(item for item in items if item["evidence_kind"] == "ingestion_run")
    assert main["rows"] == 96 and main["byte_count"] is None and main["checksum"] is None
    assert main["status"] == "run_success" and "not a per-file" in main["detail"]
    assert main["timestamp_basis"] == "materialization"
    assert datetime.fromisoformat(main["observed_at"]) == NOW
    assert datetime.fromisoformat(main["run_started_at"]) == NOW - timedelta(minutes=3)
    assert datetime.fromisoformat(main["run_ended_at"]) == NOW - timedelta(minutes=1)
    assert main["upload_actor"] is None and main["upload_filename"] is None
    assert all(item["upload_id"] != "other" for item in items)


def test_per_asset_and_run_denials_are_filtered_before_return(observations, monkeypatch):
    original = loads.enforce_http_operation

    async def deny(request, spec, params):
        if params.get("run_id") == "failed-run" or params.get("asset_id") == "bronze/lims":
            raise HTTPException(403, "denied")
        return await original(request, spec, params)

    monkeypatch.setattr(loads, "enforce_http_operation", deny)
    response = authenticated_client("operator").get("/api/v1/loads?env=sandbox")
    assert response.status_code == 200, response.text
    assert [item["run_id"] for item in response.json()["items"]] == ["candidate-run"]


def test_cross_actor_receipts_are_private_even_with_project_read(observations):
    client = authenticated_client("operator")
    client.headers["x-test-subject"] = "different"
    response = client.get("/api/v1/loads?env=sandbox")
    assert response.status_code == 200, response.text
    assert [item["evidence_kind"] for item in response.json()["items"]] == ["ingestion_run"]


@pytest.mark.parametrize("basis", ["run_ended", "run_started", "run_created"])
def test_failed_run_without_write_evidence_preserves_timestamp_basis(observations, basis):
    asset, run, _ = observations
    asset.materializations = []
    run.status = "FAILURE"
    run.selected_assets = []  # A job-level run with declared membership is still traceable.
    if basis != "run_ended":
        run.ended_at = None
    if basis == "run_created":
        run.started_at = None
    response = authenticated_client("operator").get("/api/v1/loads?env=sandbox")
    item = next(row for row in response.json()["items"] if row["evidence_kind"] == "ingestion_run")
    assert item["status"] == "run_failure" and item["rows"] is None
    assert item["timestamp_basis"] == basis
    assert datetime.fromisoformat(item["observed_at"]) == (
        run.ended_at or run.started_at or run.created_at
    )


def test_unavailable_inventory_fails_closed_and_anonymous_is_denied(observations, monkeypatch):
    from fastapi.testclient import TestClient
    from phlo_api.main import app

    assert TestClient(app).get("/api/v1/loads?env=sandbox").status_code == 401
    monkeypatch.setattr(
        loads.assets, "_assets", AsyncMock(side_effect=BackendUnavailableError("unavailable"))
    )
    response = authenticated_client("operator").get("/api/v1/loads?env=sandbox")
    assert response.status_code == 503


def test_load_pages_reach_older_runs_and_bind_continuation_to_actor(observations, monkeypatch):
    _, run, _ = observations
    runs = [run.model_copy(update={"run_id": f"run-{index:03}"}) for index in range(125)]

    async def run_page(location, ref, limit, cursor=None):
        start = next((i + 1 for i, item in enumerate(runs) if item.run_id == cursor), 0)
        page = runs[start : start + limit]
        return page, page[-1].run_id if len(page) == limit else None

    monkeypatch.setattr(loads.jobs, "_run_page", run_page)
    client = authenticated_client("operator")
    first = client.get("/api/v1/loads?env=sandbox&limit=80")
    assert first.status_code == 200, first.text
    assert len(first.json()["items"]) == 80
    cursor = first.json()["next_cursor"]
    assert cursor
    runs.insert(0, run.model_copy(update={"run_id": "new-before-cursor"}))
    second = client.get("/api/v1/loads", params={"env": "sandbox", "after": cursor, "limit": 80})
    assert second.status_code == 200, second.text
    combined = first.json()["items"] + second.json()["items"]
    assert len(combined) == 127
    assert len({item["id"] for item in combined}) == 127
    assert {item["run_id"] for item in combined if item["evidence_kind"] == "ingestion_run"} == {
        f"run-{index:03}" for index in range(125)
    }
    assert second.json()["next_cursor"] is None
    other = authenticated_client("operator")
    other.headers["x-test-subject"] = "different"
    assert other.get("/api/v1/loads", params={"env": "sandbox", "after": cursor}).status_code == 422


def test_exact_csv_page_boundary_continues_into_ingestion(observations):
    client = authenticated_client("operator")
    first = client.get("/api/v1/loads?env=sandbox&limit=2")
    assert first.status_code == 200, first.text
    assert {item["evidence_kind"] for item in first.json()["items"]} == {
        "csv_candidate",
        "csv_attempt",
    }
    second = client.get(
        "/api/v1/loads", params={"env": "sandbox", "limit": 2, "after": first.json()["next_cursor"]}
    )
    assert second.status_code == 200, second.text
    assert [item["evidence_kind"] for item in second.json()["items"]] == ["ingestion_run"]
    assert second.json()["next_cursor"] is None


def test_one_run_spans_pages_without_losing_assets_or_repeating_receipts(observations, monkeypatch):
    asset, run, _ = observations
    inventory = [
        asset.model_copy(update={"id": f"bronze/{index:03}", "key": ["bronze", str(index)]})
        for index in range(113)
    ]
    run.selected_assets = []
    monkeypatch.setattr(loads.assets, "_assets", AsyncMock(return_value=inventory))
    client = authenticated_client("operator")
    items = []
    after = None
    for _ in range(5):
        params = {"env": "sandbox", "limit": 40}
        if after:
            params["after"] = after
        response = client.get("/api/v1/loads", params=params)
        assert response.status_code == 200, response.text
        items.extend(response.json()["items"])
        after = response.json()["next_cursor"]
        if after is None:
            break
    assert after is None
    assert len(items) == 115
    assert len({item["id"] for item in items}) == 115
    assert [item["asset_id"] for item in items if item["evidence_kind"] == "ingestion_run"] == [
        f"bronze/{index:03}" for index in range(113)
    ]


def test_empty_scan_can_continue_and_rechecks_authorization(observations, monkeypatch):
    _, run, _ = observations
    monkeypatch.setattr(loads.uploads, "_records", lambda: [])
    monkeypatch.setattr(
        loads.jobs, "_run_page", AsyncMock(side_effect=[([], "unrelated-run"), ([run], None)])
    )
    client = authenticated_client("operator")
    first = client.get("/api/v1/loads?env=sandbox")
    assert first.status_code == 200, first.text
    assert first.json()["items"] == []
    cursor = first.json()["next_cursor"]
    assert cursor
    original = loads.enforce_http_operation

    async def deny(request, spec, params):
        if params.get("asset_id") == "bronze/lims":
            raise HTTPException(403, "denied after the first page")
        return await original(request, spec, params)

    monkeypatch.setattr(loads, "enforce_http_operation", deny)
    second = client.get("/api/v1/loads", params={"env": "sandbox", "after": cursor})
    assert second.status_code == 200, second.text
    assert second.json()["items"] == []
    assert second.json()["next_cursor"] is None
    monkeypatch.setenv(
        "PHLO_V1_ENVIRONMENTS", '{"sandbox":{"dagster_location":"sandbox","nessie_ref":"changed"}}'
    )
    assert (
        client.get("/api/v1/loads", params={"env": "sandbox", "after": cursor}).status_code == 422
    )


@pytest.mark.parametrize("cursor", ["not-a-cursor", "e30=", "W10=", "bnVsbA==", "eyJzY29wZSI6MH0="])
def test_malformed_load_cursor_is_rejected(observations, cursor):
    response = authenticated_client("operator").get(
        "/api/v1/loads", params={"env": "sandbox", "after": cursor}
    )
    assert response.status_code == 422, response.text
