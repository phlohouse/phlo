"""Durable report recovery after a disposable PostgreSQL restart.

Exercises both public report aliases with one application-owned store. A
database outage must remain unavailable, never a successful empty report.
"""

import socket
import time
from collections.abc import Iterator
from contextlib import contextmanager

import psycopg2
import pytest
from docker.errors import APIError
from fastapi import FastAPI
from fastapi.testclient import TestClient
from phlo.run_evidence.models import PipelineRun, RunEvent
from phlo.run_evidence.store import PostgresRunEvidenceStore
from testcontainers.postgres import PostgresContainer

from phlo_api.observatory_api.run_report import router


@contextmanager
def _postgres_on_stable_port() -> Iterator[PostgresContainer]:
    """Start PostgreSQL on a host port that survives a container restart.

    Probing for a free port and binding it later races test containers running
    in parallel. Probe, start, and retry when Docker reports a collision; the
    concrete binding then keeps the DSN valid after ``container.start()``.
    """
    for _ in range(5):
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            port = listener.getsockname()[1]
        container = PostgresContainer("postgres:18-alpine", password="test").with_bind_ports(
            5432, port
        )
        try:
            container.start()
        except APIError as error:
            container.stop()
            if "address already in use" not in str(error):
                raise
            continue
        try:
            yield container
        finally:
            container.stop()
        return
    raise RuntimeError("could not reserve a PostgreSQL host port")


@pytest.mark.integration
@pytest.mark.parametrize("read_during_outage", [False, True])
def test_run_report_recovers_without_replacing_application_store(read_during_outage: bool) -> None:
    with _postgres_on_stable_port() as postgres:
        dsn = postgres.get_connection_url(driver=None)
        store = PostgresRunEvidenceStore(dsn)
        store.append_pipeline_run(PipelineRun(project_id="recovery", run_id="orders"))
        store.append_event(
            RunEvent(
                project_id="recovery",
                run_id="orders",
                event_id="terminal",
                event_type="run.terminal",
                producer="recovery-test",
                payload={"status": "success"},
            )
        )
        app = FastAPI()
        app.include_router(router)
        app.state.run_evidence_store = store
        paths = [
            f"{prefix}/projects/recovery/runs/orders/attempts/1/report"
            for prefix in ("/api/v1", "/api/observatory")
        ]
        with TestClient(app, raise_server_exceptions=False) as client:
            before = client.get(paths[0])
            assert before.status_code == 200
            assert before.json()["terminal_outcome"]["status"] == "success"
            container = postgres.get_wrapped_container()
            container.stop(timeout=1)
            if read_during_outage:
                for path in paths:
                    unavailable = client.get(path)
                    assert unavailable.status_code == 503
                    assert unavailable.json()["detail"] == {"error": "run_evidence_unavailable"}
            container.start()
            deadline = time.monotonic() + 30
            while True:
                try:
                    with psycopg2.connect(dsn) as connection:
                        with connection.cursor() as cursor:
                            cursor.execute("SELECT 1")
                    break
                except psycopg2.OperationalError:
                    if time.monotonic() >= deadline:
                        raise
                    time.sleep(0.1)
            for path in paths:
                after = client.get(path)
                assert after.status_code == 200
                assert after.json() == before.json()
            assert client.get(paths[0].replace("orders", "unknown")).status_code == 404
        store.close()
