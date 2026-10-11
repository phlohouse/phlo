"""Shared-store proof with independent API processes and disposable PostgreSQL.

The fixture provider commits real database effects. No mocked storage, shared
in-memory cache, or common project filesystem can satisfy these assertions.
"""

from __future__ import annotations

import multiprocessing
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from uuid import uuid4

import psycopg2
import pytest
from testcontainers.postgres import PostgresContainer

from phlo.operations.journal import OperationJournalEntry, OperationJournalState
from phlo_api.api.operation_controls import resolve_idempotency_claim
from phlo_postgres.operation_controls import RATE_BUCKET_CAP, PostgresOperationControls

from process_test_support import process_operation_control_app

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def postgres_dsn():
    with PostgresContainer("postgres:16", password="test") as postgres:
        dsn = postgres.get_connection_url(driver=None)
        conn = psycopg2.connect(dsn)
        try:
            with conn, conn.cursor() as cur:
                cur.execute(
                    "CREATE TABLE proof_effects(id BIGSERIAL PRIMARY KEY, resource TEXT NOT NULL)"
                )
        finally:
            conn.close()
        yield dsn


@pytest.fixture
def apps(postgres_dsn, tmp_path, monkeypatch):
    namespace = str(uuid4())
    monkeypatch.setenv("PHLO_API_OPERATION_CONTROLS_DB_URL", postgres_dsn)
    monkeypatch.setenv("PHLO_API_OPERATION_CONTROLS_NAMESPACE", namespace)
    context = multiprocessing.get_context("spawn")
    entered, release = context.Event(), context.Event()
    processes, requests, results = [], [], []
    for index in range(2):
        path = tmp_path / str(index)
        path.mkdir()
        commands, replies = context.Queue(), context.Queue()
        process = context.Process(
            target=process_operation_control_app,
            args=(postgres_dsn, namespace, str(path), commands, replies, entered, release),
        )
        processes.append(process)
        requests.append(commands)
        results.append(replies)
        process.start()
    try:
        assert [queue.get(timeout=30) for queue in results] == ["ready", "ready"]
        yield processes, requests, results, entered, release
    finally:
        release.set()
        for queue in requests:
            queue.put(None)
        for process in processes:
            process.join(timeout=10)
            if process.is_alive():
                process.terminate()
                process.join(timeout=5)
        for queue in [*requests, *results]:
            queue.close()


@pytest.mark.parametrize("path", ["/mutate", "/mutate-async"])
def test_two_api_instances_replay_and_exclude_distinct_intents(apps, postgres_dsn, path):
    _, requests, results, entered, release = apps
    resource = str(uuid4())
    first = {"key": "same-key", "target": "actor-a:digest-a", "resource": resource, "hold": True}
    requests[0].put((path, first))
    assert entered.wait(10)
    requests[1].put((path, first))
    assert results[1].get(timeout=10) == (409, {"detail": {"error": "idempotency_in_progress"}})
    requests[1].put((path, {**first, "key": "other-key", "target": "actor-b:digest-b"}))
    assert results[1].get(timeout=10) == (409, {"detail": {"error": "operation_pending"}})
    # A separate resource is not blocked by a busy provider.
    requests[1].put(
        (path, {"key": "independent", "target": "other", "resource": resource + "-other"})
    )
    assert results[1].get(timeout=10)[0] == 200
    release.set()
    completed = results[0].get(timeout=10)
    assert completed[0] == 200
    requests[1].put((path, first))
    assert results[1].get(timeout=10) == completed
    requests[1].put((path, {**first, "target": "changed", "resource": resource + "-other"}))
    assert results[1].get(timeout=10) == (409, {"detail": {"error": "target_mismatch"}})
    uncertain = {
        "key": "uncertain",
        "target": "lost-response",
        "resource": resource + "-unknown",
        "unknown": True,
    }
    requests[0].put((path, uncertain))
    assert results[0].get(timeout=10)[0] == 504
    requests[1].put((path, uncertain))
    assert results[1].get(timeout=10) == (409, {"detail": {"error": "idempotency_outcome_unknown"}})
    # Omitting the key cannot bypass exclusion for an uncertain resource.
    requests[1].put((path, {**uncertain, "key": None, "target": "other-actor"}))
    assert results[1].get(timeout=10) == (409, {"detail": {"error": "operation_unknown"}})
    conn = psycopg2.connect(postgres_dsn)
    try:
        with conn, conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM proof_effects WHERE resource=%s", (resource,))
            assert cur.fetchone()[0] == 1
    finally:
        conn.close()


def test_process_death_keeps_target_fenced_until_evidence_resolution(apps, postgres_dsn):
    processes, requests, results, entered, _ = apps
    resource = str(uuid4())
    first = {
        "key": "crash-key",
        "target": "original",
        "resource": resource,
        "hold": True,
        "crash": True,
    }
    requests[0].put(("/mutate", first))
    assert entered.wait(10)  # The provider effect has committed.
    processes[0].terminate()
    processes[0].join(timeout=10)
    requests[1].put(("/mutate", {**first, "hold": False}))
    assert results[1].get(timeout=10)[0] == 409
    requests[1].put(("/mutate", {**first, "key": "new-key", "target": "different", "hold": False}))
    assert results[1].get(timeout=10) == (409, {"detail": {"error": "operation_pending"}})
    resolve_idempotency_claim(
        idempotency_key="crash-key",
        operation="proof-mutation",
        target="original",
        resolution="succeeded",
        resolved_by="operator",
        evidence={"database_effect": resource},
        response={"recovered": True},
    )
    requests[1].put(("/mutate", first))
    assert results[1].get(timeout=10) == (200, {"recovered": True})
    conn = psycopg2.connect(postgres_dsn)
    try:
        with conn, conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM proof_effects WHERE resource=%s", (resource,))
            assert cur.fetchone()[0] == 1
    finally:
        conn.close()


def test_workflow_advisory_lock_and_rate_limit_cross_api_instances(apps):
    _, requests, results, entered, release = apps
    requests[0].put(("/workflow", {"hold": True}))
    assert entered.wait(10)
    requests[1].put(("/workflow", {}))
    assert results[1].get(timeout=10) == (409, {"detail": {"error": "operation_in_progress"}})
    release.set()
    assert results[0].get(timeout=10)[0] == 200
    requests[1].put(("/workflow", {}))
    assert results[1].get(timeout=10)[0] == 200
    for index in range(2):
        requests[index].put(("/rate", {"subject": "shared-principal"}))
        assert results[index].get(timeout=10)[0] == 200
    requests[1].put(("/rate", {"subject": "shared-principal"}))
    assert results[1].get(timeout=10)[0] == 429


def test_shared_journal_blocks_unknown_until_reconciled(postgres_dsn):
    namespace = str(uuid4())
    stores = [PostgresOperationControls(postgres_dsn, namespace)]
    # Same namespace, independent provider objects and connections.
    stores.append(PostgresOperationControls(postgres_dsn, namespace))
    entry = OperationJournalEntry(
        "first", "actor", "restore", "prod", "plan", OperationJournalState.CLAIMED
    )
    assert stores[0].claim(entry)
    assert not stores[1].claim(replace(entry, operation_id="second"))
    assert stores[0].transition("first", OperationJournalState.UNKNOWN)
    assert not stores[1].claim(replace(entry, operation_id="second"))
    assert stores[1].read("first").state == OperationJournalState.UNKNOWN
    assert stores[1].transition("first", OperationJournalState.SUCCEEDED, {"accepted": True})
    assert stores[0].claim(replace(entry, operation_id="second"))


def test_postgres_rate_storage_is_bounded_and_expired_buckets_are_reclaimed(postgres_dsn):
    namespace = str(uuid4())
    store = PostgresOperationControls(postgres_dsn, namespace)
    with ThreadPoolExecutor(max_workers=8) as workers:
        allowed = list(
            workers.map(
                lambda subject: store.rate_limit(str(subject), "bounded", 1),
                range(RATE_BUCKET_CAP + 20),
            )
        )
    assert sum(allowed) == RATE_BUCKET_CAP
    assert not store.rate_limit("0", "bounded", 1)
    conn = psycopg2.connect(postgres_dsn)
    try:
        with conn, conn.cursor() as cur:
            cur.execute(
                "SELECT count(*) FROM phlo_api_rate_buckets WHERE namespace=%s", (namespace,)
            )
            assert cur.fetchone()[0] == RATE_BUCKET_CAP
            cur.execute(
                "UPDATE phlo_api_rate_buckets SET events=ARRAY[0.0] WHERE namespace=%s",
                (namespace,),
            )
        assert store.rate_limit("new", "bounded", 1)
        with conn, conn.cursor() as cur:
            cur.execute(
                "SELECT count(*) FROM phlo_api_rate_buckets WHERE namespace=%s", (namespace,)
            )
            assert cur.fetchone()[0] == 1
    finally:
        conn.close()
