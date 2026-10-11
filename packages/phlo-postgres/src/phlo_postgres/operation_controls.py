"""PostgreSQL-owned API operation controls, independent of replica-local paths.

Short transactions take a namespace advisory lock before checking absent rows.
Durable pending/unknown claims, not a connection lease, fence provider calls
after crashes. No unresolved claim is automatically expired.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import psycopg2
from psycopg2.extensions import cursor

from phlo.operations.journal import OperationJournalEntry, OperationJournalState
from phlo.plugins.observatory_settings import StorageUnavailableError

RATE_BUCKET_CAP = 4096

_SCHEMA = """
CREATE TABLE IF NOT EXISTS phlo_api_operations (
    namespace TEXT NOT NULL, key_hash TEXT NOT NULL, operation TEXT NOT NULL,
    target TEXT NOT NULL, exclusion_target TEXT NOT NULL,
    state TEXT NOT NULL, response_json TEXT NOT NULL DEFAULT '',
    expires_at TIMESTAMPTZ NOT NULL DEFAULT (now() + interval '24 hours'),
    PRIMARY KEY (namespace, key_hash, operation, target)
);
CREATE UNIQUE INDEX IF NOT EXISTS phlo_api_operations_active_target
    ON phlo_api_operations(namespace, operation, exclusion_target)
    WHERE state IN ('pending', 'unknown');
CREATE TABLE IF NOT EXISTS phlo_api_operation_resolutions (
    id BIGSERIAL PRIMARY KEY, namespace TEXT NOT NULL, key_hash TEXT NOT NULL,
    operation TEXT NOT NULL, target TEXT NOT NULL, resolution TEXT NOT NULL,
    resolved_by TEXT NOT NULL, evidence_json TEXT NOT NULL,
    resolved_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS phlo_api_rate_buckets (
    namespace TEXT NOT NULL, subject_hash TEXT NOT NULL, operation TEXT NOT NULL,
    events DOUBLE PRECISION[] NOT NULL,
    PRIMARY KEY(namespace, subject_hash, operation)
);
CREATE TABLE IF NOT EXISTS phlo_api_operation_journal (
    namespace TEXT NOT NULL, operation_id TEXT NOT NULL,
    action TEXT NOT NULL, target TEXT NOT NULL, state TEXT NOT NULL,
    entry_json TEXT NOT NULL,
    PRIMARY KEY(namespace, operation_id)
);
CREATE UNIQUE INDEX IF NOT EXISTS phlo_api_journal_active_target
    ON phlo_api_operation_journal(namespace, action, target)
    WHERE state IN ('claimed', 'submitted', 'unknown');
"""


class PostgresOperationControls:
    """Shared controls and the core durable-journal protocol for one project.

    Connections are opened per transaction and always closed. The namespace
    lock deliberately serialises short control transactions, not provider I/O.
    """

    def __init__(self, db_url: str, namespace: str) -> None:
        if not namespace.strip():
            raise ValueError("Shared operation controls require a stable namespace")
        self._db_url = db_url
        self._namespace = namespace
        self._schema_ready = False

    @contextmanager
    def _transaction(self) -> Iterator[cursor]:
        conn = None
        try:
            conn = psycopg2.connect(self._db_url, connect_timeout=5)
            with conn, conn.cursor() as cur:
                cur.execute("SET LOCAL lock_timeout = '5s'")
                cur.execute("SET LOCAL statement_timeout = '10s'")
                # Serialise DDL too: concurrent CREATE IF NOT EXISTS can race
                # on PostgreSQL's catalog unique constraints on first startup.
                if not self._schema_ready:
                    cur.execute(
                        "SELECT pg_advisory_xact_lock(hashtext('phlo-api-controls-schema'))"
                    )
                    cur.execute(_SCHEMA)
                cur.execute(
                    "SELECT pg_advisory_xact_lock(hashtext(%s))",
                    (f"phlo-api-controls:{self._namespace}",),
                )
                yield cur
            self._schema_ready = True
        except psycopg2.Error as exc:
            raise StorageUnavailableError("Shared operation controls are unavailable") from exc
        finally:
            if conn is not None:
                conn.close()

    def initialize(self) -> None:
        """Check connectivity and initialise the schema before serving traffic."""
        with self._transaction():
            pass

    def claim_idempotency(
        self,
        key_hash: str,
        operation: str,
        target: str,
        exclusion_target: str | None = None,
    ) -> tuple[bool, str, str]:
        """Claim identity and target together; return replay or a stable conflict."""
        with self._transaction() as cur:
            cur.execute(
                "DELETE FROM phlo_api_operations WHERE namespace=%s "
                "AND state='completed' AND expires_at < now()",
                (self._namespace,),
            )
            cur.execute(
                "SELECT target, state, response_json FROM phlo_api_operations "
                "WHERE namespace=%s AND key_hash=%s AND operation=%s AND (%s OR target=%s)",
                (self._namespace, key_hash, operation, exclusion_target is not None, target),
            )
            row = cur.fetchone()
            if row is not None:
                if row[0] != target:
                    return False, "target_mismatch", ""
                if row[1] != "safe_to_retry":
                    return False, row[1], row[2]
            cur.execute(
                "SELECT state FROM phlo_api_operations WHERE namespace=%s "
                "AND operation=%s AND exclusion_target=%s AND state IN ('pending', 'unknown') LIMIT 1",
                (self._namespace, operation, exclusion_target or target),
            )
            active = cur.fetchone()
            if active is not None:
                return False, "operation_" + active[0], ""
            cur.execute(
                "INSERT INTO phlo_api_operations(namespace,key_hash,operation,target,exclusion_target,state) "
                "VALUES (%s,%s,%s,%s,%s,'pending') ON CONFLICT(namespace,key_hash,operation,target) "
                "DO UPDATE SET state='pending', response_json='', expires_at=now()+interval '24 hours'",
                (self._namespace, key_hash, operation, target, exclusion_target or target),
            )
            return True, "pending", ""

    def finish_idempotency(
        self,
        key_hash: str,
        operation: str,
        target: str,
        state: str,
        response: dict[str, Any] | None = None,
    ) -> None:
        """Record provider completion, or preserve an unknown outcome indefinitely."""
        with self._transaction() as cur:
            cur.execute(
                "UPDATE phlo_api_operations SET state=%s, response_json=%s, "
                "expires_at=now()+interval '24 hours' WHERE namespace=%s AND key_hash=%s "
                "AND operation=%s AND target=%s AND state='pending'",
                (
                    state,
                    json.dumps(response, sort_keys=True) if response is not None else "",
                    self._namespace,
                    key_hash,
                    operation,
                    target,
                ),
            )
            if cur.rowcount != 1:
                raise StorageUnavailableError("Operation claim changed before completion")

    def idempotency_target(self, key_hash: str, operation: str) -> str | None:
        """Read the canonical key binding across all replicas."""
        with self._transaction() as cur:
            cur.execute(
                "SELECT target FROM phlo_api_operations WHERE namespace=%s AND key_hash=%s AND operation=%s",
                (self._namespace, key_hash, operation),
            )
            row = cur.fetchone()
            return row[0] if row is not None else None

    def resolve_idempotency(
        self,
        key_hash: str,
        operation: str,
        target: str,
        resolution: str,
        resolved_by: str,
        evidence: dict[str, Any],
        response: dict[str, Any] | None,
    ) -> None:
        """Retain operator evidence; only explicit safe_to_retry permits another call."""
        state = "completed" if resolution == "succeeded" else resolution
        evidence_json = json.dumps(evidence, sort_keys=True)
        with self._transaction() as cur:
            identity = (self._namespace, key_hash, operation, target)
            cur.execute(
                "SELECT state FROM phlo_api_operations WHERE namespace=%s "
                "AND key_hash=%s AND operation=%s AND target=%s",
                identity,
            )
            row = cur.fetchone()
            if row is None or row[0] not in {"pending", "unknown"}:
                cur.execute(
                    "SELECT resolution,resolved_by,evidence_json FROM phlo_api_operation_resolutions "
                    "WHERE namespace=%s AND key_hash=%s AND operation=%s AND target=%s ORDER BY id DESC LIMIT 1",
                    identity,
                )
                prior = cur.fetchone()
                if (
                    row is not None
                    and row[0] == state
                    and prior == (resolution, resolved_by, evidence_json)
                ):
                    return
                raise ValueError("only pending or unknown idempotency claims can be resolved")
            cur.execute(
                "UPDATE phlo_api_operations SET state=%s,response_json=%s,expires_at=now()+interval '24 hours' "
                "WHERE namespace=%s AND key_hash=%s AND operation=%s AND target=%s",
                (
                    state,
                    json.dumps(response, sort_keys=True) if response is not None else "",
                    *identity,
                ),
            )
            cur.execute(
                "INSERT INTO phlo_api_operation_resolutions(namespace,key_hash,operation,target,resolution,resolved_by,evidence_json) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s)",
                (*identity, resolution, resolved_by, evidence_json),
            )

    def rate_limit(self, subject_hash: str, operation: str, limit: int) -> bool:
        """Bound both bucket cardinality and each sliding window; fail closed at capacity."""
        with self._transaction() as cur:
            cur.execute("SELECT extract(epoch FROM clock_timestamp())::double precision")
            now = cur.fetchone()[0]
            cur.execute(
                "DELETE FROM phlo_api_rate_buckets WHERE namespace=%s AND events[array_length(events,1)] < %s",
                (self._namespace, now - 60),
            )
            identity = (self._namespace, subject_hash, operation)
            cur.execute(
                "SELECT events FROM phlo_api_rate_buckets WHERE namespace=%s AND subject_hash=%s AND operation=%s",
                identity,
            )
            row = cur.fetchone()
            events = [stamp for stamp in row[0] if stamp >= now - 60] if row is not None else []
            if len(events) >= limit:
                return False
            if row is None:
                cur.execute(
                    "SELECT count(*) FROM phlo_api_rate_buckets WHERE namespace=%s",
                    (self._namespace,),
                )
                if cur.fetchone()[0] >= RATE_BUCKET_CAP:
                    return False
            cur.execute(
                "INSERT INTO phlo_api_rate_buckets(namespace,subject_hash,operation,events) VALUES (%s,%s,%s,%s) "
                "ON CONFLICT(namespace,subject_hash,operation) DO UPDATE SET events=EXCLUDED.events",
                (*identity, [*events, now]),
            )
            return True

    def claim(self, entry: OperationJournalEntry) -> bool:
        """Claim a journal operation and its active target across replicas."""
        with self._transaction() as cur:
            cur.execute(
                "SELECT 1 FROM phlo_api_operation_journal WHERE namespace=%s AND "
                "(operation_id=%s OR (action=%s AND target=%s AND state IN ('claimed','submitted','unknown'))) LIMIT 1",
                (self._namespace, entry.operation_id, entry.action, entry.target),
            )
            if cur.fetchone() is not None:
                return False
            cur.execute(
                "INSERT INTO phlo_api_operation_journal(namespace,operation_id,action,target,state,entry_json) "
                "VALUES (%s,%s,%s,%s,%s,%s)",
                (
                    self._namespace,
                    entry.operation_id,
                    entry.action,
                    entry.target,
                    entry.state.value,
                    json.dumps(entry.to_dict()),
                ),
            )
            return True

    def transition(
        self,
        operation_id: str,
        state: OperationJournalState,
        result: dict[str, Any] | None = None,
    ) -> bool:
        """Persist a journal transition without releasing unresolved target claims."""
        with self._transaction() as cur:
            cur.execute(
                "SELECT entry_json FROM phlo_api_operation_journal WHERE namespace=%s AND operation_id=%s",
                (self._namespace, operation_id),
            )
            row = cur.fetchone()
            if row is None:
                return False
            record = json.loads(row[0])
            record.update(state=state.value, result=result)
            cur.execute(
                "UPDATE phlo_api_operation_journal SET state=%s,entry_json=%s WHERE namespace=%s AND operation_id=%s",
                (state.value, json.dumps(record), self._namespace, operation_id),
            )
            return True

    def read(self, operation_id: str) -> OperationJournalEntry | None:
        """Read journal evidence from any replica."""
        with self._transaction() as cur:
            cur.execute(
                "SELECT entry_json FROM phlo_api_operation_journal WHERE namespace=%s AND operation_id=%s",
                (self._namespace, operation_id),
            )
            row = cur.fetchone()
            if row is None:
                return None
            record = json.loads(row[0])
            record["state"] = OperationJournalState(record["state"])
            return OperationJournalEntry(**record)

    @contextmanager
    def exclusion(self, operation: str, target: str) -> Iterator[None]:
        """Non-blocking session advisory lock for a compound provider action.

        Durable idempotency/journal claims must additionally fence effects whose
        outcome may be unknown after connection loss or process termination.
        """
        conn = None
        try:
            conn = psycopg2.connect(self._db_url, connect_timeout=5)
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT pg_try_advisory_lock(hashtextextended(%s,0))",
                    (json.dumps([self._namespace, operation, target]),),
                )
                acquired = cur.fetchone()[0]
            if not acquired:
                raise OperationExclusionConflict("Operation is already in progress")
            yield
        except psycopg2.Error as exc:
            raise StorageUnavailableError("Shared operation exclusion is unavailable") from exc
        finally:
            if conn is not None:
                conn.close()


class OperationExclusionConflict(RuntimeError):
    """Another instance holds the compound-action advisory lock."""
