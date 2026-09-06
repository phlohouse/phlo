"""Tests for Airbyte connection asset registration and execution."""

from __future__ import annotations

import pytest
from phlo.helpers.testing import FakeRuntimeContext
from phlo_airbyte.assets import (
    clear_airbyte_assets,
    get_airbyte_assets,
    phlo_airbyte_connection,
)


class FakeClient:
    def __init__(self, evidence: dict | None = None, error: Exception | None = None) -> None:
        self.evidence = evidence or {
            "job_id": "42",
            "connection_id": "conn-1",
            "status": "succeeded",
            "started_at": 1000,
            "ended_at": 2000,
            "elapsed_seconds": 1.5,
        }
        self.error = error
        self.ran: list[str] = []

    def run_sync(self, connection_id: str, **kwargs) -> dict:
        self.ran.append(connection_id)
        if self.error is not None:
            raise self.error
        return self.evidence


@pytest.fixture(autouse=True)
def _clean_registry():
    clear_airbyte_assets()
    yield
    clear_airbyte_assets()


def _runtime() -> FakeRuntimeContext:
    return FakeRuntimeContext(partition_key="2026-09-01", run_id="run-1")


def test_connection_asset_requires_tables_and_connection_id() -> None:
    with pytest.raises(Exception, match="connection_id"):
        phlo_airbyte_connection(connection_id="  ", tables=["t"], group="ingestion")
    with pytest.raises(Exception, match="output tables"):
        phlo_airbyte_connection(connection_id="conn-1", tables=[], group="ingestion")


def test_registered_asset_carries_airbyte_metadata() -> None:
    spec = phlo_airbyte_connection(
        connection_id="conn-1",
        tables=["bronze.users"],
        group="ingestion",
        name="users",
    )
    assert spec.key == "ingestion.users"
    assert spec.tags["provider"] == "airbyte"
    assert spec.metadata["airbyte_connection_id"] == "conn-1"
    assert spec.metadata["output_tables"] == ["bronze.users"]
    assert get_airbyte_assets() == [spec]


def test_asset_run_emits_job_evidence_metadata() -> None:
    client = FakeClient()
    spec = phlo_airbyte_connection(
        connection_id="conn-1",
        tables=["bronze.users"],
        group="ingestion",
        client_factory=lambda: client,
    )
    results = list(spec.run.fn(_runtime()))
    assert len(results) == 1
    metadata = results[0].metadata
    assert metadata["airbyte_job_id"] == "42"
    assert metadata["airbyte_connection_id"] == "conn-1"
    assert metadata["output_tables"] == ["bronze.users"]
    assert metadata["source_state"] == {"job_id": "42", "status": "succeeded"}
    assert client.ran == ["conn-1"]


def test_asset_run_fails_closed_on_sync_failure() -> None:
    client = FakeClient(error=RuntimeError("Airbyte sync ended with status 'failed'"))
    spec = phlo_airbyte_connection(
        connection_id="conn-1",
        tables=["bronze.users"],
        group="ingestion",
        client_factory=lambda: client,
    )
    with pytest.raises(RuntimeError, match="failed"):
        list(spec.run.fn(_runtime()))
