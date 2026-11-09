"""Shared-store proof with independent API processes and disposable PostgreSQL.

The fixture provider commits real database effects. No mocked storage, shared
in-memory cache, or common project filesystem can satisfy these assertions.
"""

from __future__ import annotations

import json
import multiprocessing
from threading import Event
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from uuid import uuid4

import psycopg2
import pytest
from testcontainers.postgres import PostgresContainer

from phlo.operations.journal import (
    OperationJournal,
    OperationJournalEntry,
    OperationJournalError,
    OperationJournalState,
)
from phlo.plugins.observatory_settings import StorageUnavailableError
from phlo_api.api.operation_controls import resolve_idempotency_claim
from phlo_postgres.operation_controls import (
    RATE_BUCKET_CAP,
    OperationExclusionConflict,
    PostgresOperationControls,
)

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
    assert results[1].get(timeout=10) == (409, {"detail": {"error": "idempotency_key_conflict"}})
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
    OperationJournal(stores[0]).submit("first")
    OperationJournal(stores[0]).unknown("first")
    assert not stores[1].claim(replace(entry, operation_id="second"))
    assert stores[1].read("first").state == OperationJournalState.UNKNOWN
    OperationJournal(stores[1]).reconcile(
        "first", state=OperationJournalState.SUCCEEDED, result={"accepted": True}
    )
    assert stores[0].claim(replace(entry, operation_id="second"))


def test_journal_replicas_atomically_complete_without_terminal_regression(postgres_dsn):
    namespace = str(uuid4())
    stores = [PostgresOperationControls(postgres_dsn, namespace) for _ in range(2)]
    OperationJournal(stores[0]).claim(
        operation_id="race", subject="actor", action="restore", target="prod", plan_token="plan"
    )
    OperationJournal(stores[0]).submit("race")

    def finish(index):
        state = [OperationJournalState.SUCCEEDED, OperationJournalState.FAILED][index]
        try:
            OperationJournal(stores[index]).complete(
                "race", state=state, result={"state": state.value, "replica": index}
            )
        except OperationJournalError:
            return False
        return True

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sum(pool.map(finish, range(2))) == 1
    receipt = stores[0].read("race")
    assert receipt.result["state"] == receipt.state.value
    for store in stores:
        with pytest.raises(OperationJournalError, match="invalid_transition"):
            OperationJournal(store).submit("race")
        with pytest.raises(OperationJournalError, match="invalid_transition"):
            OperationJournal(store).reconcile(
                "race", state=OperationJournalState.FAILED, result={"accepted": False}
            )
        assert store.read("race") == receipt


@pytest.mark.parametrize(
    "state",
    [
        OperationJournalState.CLAIMED,
        OperationJournalState.SUBMITTED,
        OperationJournalState.UNKNOWN,
    ],
)
def test_journal_lease_takeover_never_expires_submitted_or_unknown(postgres_dsn, state):
    namespace = str(uuid4())
    stores = [PostgresOperationControls(postgres_dsn, namespace) for _ in range(2)]
    owner = OperationJournal(stores[0])
    entry = owner.claim(
        operation_id="lease",
        subject="actor",
        action="restore",
        target="prod",
        plan_token="p",
        claim_expiry="2999-01-01T00:00:00+00:00",
    )
    if state is not OperationJournalState.CLAIMED:
        owner.submit(entry.operation_id)
    if state is OperationJournalState.UNKNOWN:
        owner.unknown(entry.operation_id, result={"provider": "uncertain"})
    # Expire the lease field only, including its submitted/unknown controls.
    # Real provider transactions still perform the takeover and stale-owner check.
    record = stores[0].read(entry.operation_id).to_dict()
    record["claim_expiry"] = "2000-01-01T00:00:00+00:00"
    with psycopg2.connect(postgres_dsn) as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE phlo_api_operation_journal SET entry_json=%s WHERE namespace=%s AND operation_id=%s",
            (json.dumps(record), namespace, entry.operation_id),
        )
    replacement = replace(entry, operation_id="new", plan_token="new")
    assert stores[1].claim(replacement) is (state is OperationJournalState.CLAIMED)
    if state is OperationJournalState.CLAIMED:
        assert stores[0].read(entry.operation_id).state is OperationJournalState.FAILED
        with pytest.raises(OperationJournalError, match="invalid_transition"):
            owner.submit(entry.operation_id)
    else:
        assert stores[0].read(entry.operation_id).state is state


@pytest.mark.parametrize("persisted_action", ["expire_snapshots", "snapshot_expiry"])
@pytest.mark.parametrize("state", ["claimed", "submitted", "unknown"])
def test_legacy_retention_journal_alias_fences_replicas(postgres_dsn, persisted_action, state):
    namespace = str(uuid4())
    first = PostgresOperationControls(postgres_dsn, namespace)
    second = PostgresOperationControls(postgres_dsn, namespace)
    first.initialize()
    entry = OperationJournalEntry(
        "expire_snapshots:raw.events:main:old-run",
        "scheduler",
        persisted_action,
        "raw.events",
        "old-plan",
        OperationJournalState(state),
        result={"accepted": True},
    )
    # Seed historical SQL and JSON action columns, not a new claim normalised by core.
    with psycopg2.connect(postgres_dsn) as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO phlo_api_operation_journal(namespace,operation_id,action,target,state,entry_json) "
            "VALUES (%s,%s,%s,%s,%s,%s)",
            (
                namespace,
                entry.operation_id,
                persisted_action,
                entry.target,
                state,
                json.dumps(entry.to_dict()),
            ),
        )
    other_action = (
        "snapshot_expiry" if persisted_action == "expire_snapshots" else "expire_snapshots"
    )
    contender = replace(
        entry,
        operation_id="fresh-plan",
        action=other_action,
        plan_token="new",
        state=OperationJournalState.CLAIMED,
        result=None,
    )
    assert not second.claim(contender)
    assert second.read(entry.operation_id) == entry
    assert second.claim(replace(contender, operation_id="other-table", target="raw.other"))
    assert second.claim(replace(contender, operation_id="compact", action="compact"))
    owner = OperationJournal(first)
    if state == "claimed":
        owner.submit(entry.operation_id)
    if state != "unknown":
        owner.unknown(entry.operation_id, result=entry.result)
    for evidence in (
        {"accepted": True},
        {"accepted": False},
        {
            "accepted": True,
            "status": "failed",
            "failure": {"outcome": "unknown"},
            "retry_safe": False,
        },
    ):
        with pytest.raises(OperationJournalError, match="unknown_outcome_blocks_replay"):
            OperationJournal(second).reconcile(
                entry.operation_id, state=OperationJournalState.SUCCEEDED, result=evidence
            )
        assert first.read(entry.operation_id).state is OperationJournalState.UNKNOWN
        assert first.read(entry.operation_id).result == entry.result
        assert not first.claim(contender)
    OperationJournal(second).reconcile(
        entry.operation_id,
        state=OperationJournalState.SUCCEEDED,
        result={"accepted": True, "status": "succeeded"},
    )
    assert first.read(entry.operation_id).action == persisted_action
    assert first.claim(contender)
    assert second.read("fresh-plan").action == "snapshot_expiry"


def test_reclamation_guard_shares_transaction_exclusion_with_remote_claims(postgres_dsn):
    namespace = str(uuid4())
    first = PostgresOperationControls(postgres_dsn, namespace)
    second = PostgresOperationControls(postgres_dsn, namespace)
    first.initialize()
    second.initialize()
    entered, claimed = Event(), Event()

    def contender():
        entered.set()
        result = second.claim_idempotency("key", "write", "outcome", "resource")
        claimed.set()
        return result

    with ThreadPoolExecutor(max_workers=1) as pool:
        with first.reclamation_guard([("write", "resource")]):
            future = pool.submit(contender)
            assert entered.wait(1)
            assert not claimed.wait(0.2)
        assert future.result(timeout=3) == (True, "pending", "")
    for target in ("resource", "outcome"):
        with pytest.raises(OperationExclusionConflict):
            with first.reclamation_guard([("write", target)]):
                pytest.fail("Pending mutation authorized disposal")
    second.finish_idempotency("key", "write", "outcome", "unknown")
    with pytest.raises(OperationExclusionConflict):
        with first.reclamation_guard([("write", "resource")]):
            pytest.fail("Unknown mutation authorized disposal")
    assert second.claim_idempotency("other-key", "write", "other-outcome", "resource") == (
        False,
        "operation_unknown",
        "",
    )


def test_shared_claim_resolution_and_namespace_isolation(postgres_dsn):
    namespace = str(uuid4())
    store = PostgresOperationControls(postgres_dsn, namespace)
    replica = PostgresOperationControls(postgres_dsn, namespace)
    other = PostgresOperationControls(postgres_dsn, namespace + "-other")
    store.initialize()
    assert store.idempotency_target("missing", "restore") is None
    assert store.read("missing") is None
    assert not store.compare_and_set(
        "missing",
        OperationJournalState.SUBMITTED,
        OperationJournalState.SUCCEEDED,
        {"accepted": True},
    )
    assert store.claim_idempotency("key", "restore", "original", "resource") == (
        True,
        "pending",
        "",
    )
    assert replica.idempotency_target("key", "restore") == "original"
    assert (
        replica.claim_idempotency("key", "restore", "changed", "resource")[1] == "target_mismatch"
    )
    assert replica.claim_idempotency("key", "restore", "original", "resource")[1] == "pending"
    assert (
        replica.claim_idempotency("new", "restore", "different", "resource")[1]
        == "operation_pending"
    )
    assert other.claim_idempotency("key", "restore", "original", "resource")[0]
    store.finish_idempotency("key", "restore", "original", "unknown")
    assert (
        replica.claim_idempotency("new", "restore", "different", "resource")[1]
        == "operation_unknown"
    )
    evidence = {"provider_lookup": "not applied"}
    replica.resolve_idempotency(
        "key", "restore", "original", "safe_to_retry", "operator", evidence, None
    )
    replica.resolve_idempotency(
        "key", "restore", "original", "safe_to_retry", "operator", evidence, None
    )
    with pytest.raises(ValueError, match="only pending or unknown"):
        replica.resolve_idempotency(
            "key", "restore", "original", "safe_to_retry", "other", evidence, None
        )
    assert store.claim_idempotency("key", "restore", "original", "resource")[0]
    store.finish_idempotency("key", "restore", "original", "completed", {"accepted": True})
    claimed, state, response = replica.claim_idempotency("key", "restore", "original", "resource")
    assert (claimed, state) == (False, "completed")
    assert json.loads(response) == {"accepted": True}
    with pytest.raises(StorageUnavailableError, match="claim changed"):
        replica.finish_idempotency("key", "restore", "original", "completed")
    assert store.claim_idempotency("resolved", "restore", "second")[0]
    store.resolve_idempotency(
        "resolved", "restore", "second", "succeeded", "operator", evidence, {"restored": True}
    )
    claimed, state, response = replica.claim_idempotency("resolved", "restore", "second")
    assert (claimed, state) == (False, "completed")
    assert json.loads(response) == {"restored": True}
    with store.exclusion("workflow", "resource"):
        with pytest.raises(OperationExclusionConflict):
            with replica.exclusion("workflow", "resource"):
                pytest.fail("independent connection bypassed the exclusion")
        with other.exclusion("workflow", "resource"):
            pass
    with replica.exclusion("workflow", "resource"):
        pass


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
