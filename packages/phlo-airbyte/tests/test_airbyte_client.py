"""Tests for the Airbyte client's fail-closed job-state handling."""

from __future__ import annotations

import pytest
from phlo_airbyte.client import AirbyteClient, AmbiguousJobStateError
from phlo_airbyte.settings import AirbyteSettings


class FakeTransport:
    """Scripted HTTP transport returning queued payloads per path."""

    def __init__(self, responses: dict[str, list[dict]]) -> None:
        self.responses = {key: list(value) for key, value in responses.items()}
        self.calls: list[tuple[str, dict]] = []

    def post(self, path: str, body: dict) -> dict:
        self.calls.append((path, body))
        queue = self.responses.get(path, [])
        if not queue:
            raise AssertionError(f"unexpected request to {path}")
        item = queue.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def _client(transport: FakeTransport) -> AirbyteClient:
    settings = AirbyteSettings(airbyte_poll_interval_seconds=0)
    client = AirbyteClient(settings=settings)
    client._request = lambda method, path, json_body=None: transport.post(path, json_body or {})
    return client


def test_trigger_sync_sends_connection_and_job_type() -> None:
    transport = FakeTransport({"/api/v1/jobs/run": [{"job": {"id": 42, "status": "pending"}}]})
    client = _client(transport)
    job = client.trigger_sync("conn-1")
    assert job["job"]["id"] == 42
    assert transport.calls == [("/api/v1/jobs/run", {"connectionId": "conn-1", "jobType": "sync"})]


def test_run_sync_returns_evidence_after_success() -> None:
    transport = FakeTransport(
        {
            "/api/v1/jobs/run": [{"job": {"id": 42, "status": "pending", "createdAt": 1000}}],
            "/api/v1/jobs/get": [
                {"job": {"id": 42, "status": "running"}},
                {"job": {"id": 42, "status": "succeeded", "updatedAt": 2000}},
            ],
        }
    )
    sleeps: list[int] = []
    client = _client(transport)

    evidence = client.run_sync(
        "conn-1", poll_interval_seconds=1, timeout_seconds=30, clock=SimpleClock(sleeps)
    )

    assert evidence["status"] == "succeeded"
    assert evidence["job_id"] == "42"
    assert evidence["connection_id"] == "conn-1"
    assert sleeps == [1]


class SimpleClock:
    def __init__(self, sleeps: list[int]) -> None:
        self.sleeps = sleeps

    def sleep(self, seconds: int) -> None:
        self.sleeps.append(seconds)


def test_run_sync_raises_for_failed_terminal_state() -> None:
    transport = FakeTransport(
        {
            "/api/v1/jobs/run": [{"job": {"id": 7, "status": "running"}}],
            "/api/v1/jobs/get": [{"job": {"id": 7, "status": "failed"}}],
        }
    )
    client = _client(transport)
    with pytest.raises(RuntimeError, match="ended with status 'failed'"):
        client.run_sync("conn-1", poll_interval_seconds=1, timeout_seconds=10)


def test_unknown_job_status_fails_closed() -> None:
    client = AirbyteClient()
    assert client.classify_status("succeeded") == "succeeded"
    assert client.classify_status("running") is None
    assert client.classify_status("incomplete_retrying") is None
    with pytest.raises(AmbiguousJobStateError, match="refusing to guess"):
        client.classify_status("mystery-state")
    with pytest.raises(AmbiguousJobStateError):
        client.classify_status(None)


def test_run_sync_times_out_without_terminal_state() -> None:
    transport = FakeTransport(
        {
            "/api/v1/jobs/run": [{"job": {"id": 9, "status": "running"}}],
            "/api/v1/jobs/get": [{"job": {"id": 9, "status": "running"}}],
        }
    )
    client = _client(transport)
    with pytest.raises(TimeoutError, match="did not reach a terminal state"):
        client.run_sync("conn-1", poll_interval_seconds=1, timeout_seconds=-1)
