"""Idempotency claim tests for operation_controls replay/execute helpers.

These tests cover the atomic claim-before-execution contract, stable 409
responses for pending and unknown outcomes, completed replay, backward-compatible
schema migration, and forced-concurrency coverage for both sync and async helpers.
"""

from __future__ import annotations

import asyncio
import json
import multiprocessing
import os
import sqlite3
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from phlo_api.api.operation_controls import (
    IdempotencyConflict,
    MutationSucceededAuditFailed,
    _idempotency_hash,
    _migrate_operations_schema,
    audit_operation,
    replay_or_execute,
    replay_or_execute_async,
    resolve_idempotency_claim,
)


def _process_audit_writer(project_path: str, barrier: Any, index: int) -> None:
    """Write one distinct record after all independent processes are ready."""
    os.environ["PHLO_PROJECT_PATH"] = project_path
    barrier.wait()
    audit_operation(
        operation="process_write",
        target=str(index),
        dry_run=False,
        auth={"subject": "test", "scopes": []},
        result={"index": index},
    )


def _process_idempotency_resolution(project_path: str, barrier: Any) -> None:
    """Resolve the same durable claim from an independent process."""
    os.environ["PHLO_PROJECT_PATH"] = project_path
    barrier.wait()
    resolve_idempotency_claim(
        idempotency_key="process-resolution",
        operation="cancel_run",
        target="run-process",
        resolution="safe_to_retry",
        resolved_by="operator@example.test",
        evidence={"provider_lookup": "request was not applied"},
    )


def _audit_records(audit_dir: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for path in sorted(audit_dir.glob("operations.jsonl*")):
        if path.name == "operations.lock":
            continue
        records.extend(json.loads(line) for line in path.read_text(encoding="utf-8").splitlines())
    return records


def _assert_rotated_audit_is_complete(audit_dir: Path, expected_targets: set[str]) -> None:
    records = _audit_records(audit_dir)
    assert {record["target"] for record in records} == expected_targets
    assert len(records) == len(expected_targets)


def _seed_legacy_completed_row(
    tmp_path: Path,
    *,
    idempotency_key: str,
    operation: str,
    target: str,
    response: dict[str, Any],
) -> None:
    """Write a pre-migration completed row using the original (state-less) schema."""
    state_dir = tmp_path / ".phlo" / "state"
    state_dir.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(state_dir / "operations.sqlite")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS operations(
            project TEXT NOT NULL,
            key_hash TEXT NOT NULL,
            operation TEXT NOT NULL,
            target TEXT NOT NULL,
            response_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            expires_at TEXT NOT NULL,
            PRIMARY KEY(project, key_hash, operation, target)
        )
        """
    )
    now = datetime.now(UTC)
    expires_at = now + timedelta(hours=24)
    conn.execute(
        "INSERT INTO operations VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            str(tmp_path.resolve()),
            _idempotency_hash(idempotency_key),
            operation,
            target,
            json.dumps(response, sort_keys=True),
            now.isoformat(),
            expires_at.isoformat(),
        ),
    )
    conn.commit()
    conn.close()


def test_concurrent_idempotency_sync_executes_provider_once(monkeypatch, tmp_path: Path) -> None:
    """Two barrier-synchronised sync calls invoke the provider exactly once."""
    monkeypatch.setenv("PHLO_PROJECT_PATH", str(tmp_path))
    barrier = threading.Barrier(2)
    provider_calls: list[int] = []
    provider_lock = threading.Lock()

    def execute() -> dict[str, Any]:
        with provider_lock:
            provider_calls.append(1)
        # Hold the claim long enough for the contender to observe pending state.
        threading.Event().wait(0.3)
        return {"ok": True, "run_id": "run-1"}

    results: dict[str, dict[str, Any]] = {}
    conflicts: dict[str, tuple[int, dict[str, Any]]] = {}
    integrity_errors: list[str] = []

    def worker(name: str) -> None:
        barrier.wait()
        try:
            results[name] = replay_or_execute(
                idempotency_key="same-key",
                operation="retry_failed_run",
                target="run-123",
                execute=execute,
            )
        except IdempotencyConflict as exc:
            conflicts[name] = (exc.status_code, exc.detail)
        except sqlite3.IntegrityError as exc:
            integrity_errors.append(f"{name}: {exc}")

    threads = [threading.Thread(target=worker, args=(name,)) for name in ("a", "b")]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert len(provider_calls) == 1
    assert integrity_errors == []
    assert len(results) == 1
    assert len(conflicts) == 1
    winner = next(iter(results.values()))
    assert winner["run_id"] == "run-1"
    _status, detail = next(iter(conflicts.values()))
    assert _status == 409
    assert detail == {"error": "idempotency_in_progress"}


def test_concurrent_idempotency_async_executes_provider_once(monkeypatch, tmp_path: Path) -> None:
    """Two concurrent async calls invoke the provider exactly once."""
    monkeypatch.setenv("PHLO_PROJECT_PATH", str(tmp_path))
    provider_calls: list[int] = []

    async def execute() -> dict[str, Any]:
        provider_calls.append(1)
        await asyncio.sleep(0.3)
        return {"ok": True, "run_id": "run-async-1"}

    async def caller() -> dict[str, Any]:
        return await replay_or_execute_async(
            idempotency_key="same-key",
            operation="retry_failed_run",
            target="run-456",
            execute=execute,
        )

    async def main() -> tuple[Any, Any]:
        return await asyncio.gather(caller(), caller(), return_exceptions=True)

    r1, r2 = asyncio.run(main())

    assert len(provider_calls) == 1
    winners = [r for r in (r1, r2) if not isinstance(r, BaseException)]
    conflicts = [r for r in (r1, r2) if isinstance(r, IdempotencyConflict)]
    integrity_errors = [r for r in (r1, r2) if isinstance(r, sqlite3.IntegrityError)]
    assert integrity_errors == []
    assert len(winners) + len(conflicts) == 2
    assert 1 <= len(winners) <= 2
    assert all(winner["run_id"] == "run-async-1" for winner in winners)
    assert all(conflict.status_code == 409 for conflict in conflicts)
    assert all(conflict.detail == {"error": "idempotency_in_progress"} for conflict in conflicts)


def test_concurrent_idempotency_pending_409_has_retry_after(monkeypatch, tmp_path: Path) -> None:
    """A pending contender 409 carries a Retry-After header."""
    monkeypatch.setenv("PHLO_PROJECT_PATH", str(tmp_path))
    barrier = threading.Barrier(2)
    release = threading.Event()

    def execute() -> dict[str, Any]:
        release.wait(1.0)
        return {"ok": True}

    captured: list[IdempotencyConflict] = []

    def worker(name: str) -> None:
        barrier.wait()
        try:
            replay_or_execute(
                idempotency_key="key-ra",
                operation="cancel_run",
                target="run-ra",
                execute=execute,
            )
        except IdempotencyConflict as exc:
            captured.append(exc)

    threads = [threading.Thread(target=worker, args=(name,)) for name in ("a", "b")]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    release.set()
    pending_conflicts = [c for c in captured if c.detail == {"error": "idempotency_in_progress"}]
    assert len(pending_conflicts) == 1
    headers = pending_conflicts[0].headers or {}
    assert "retry-after" in {k.lower() for k in headers}


def test_idempotency_replays_completed_response_without_execution(
    monkeypatch, tmp_path: Path
) -> None:
    """A later identical call returns the stored result without provider execution."""
    monkeypatch.setenv("PHLO_PROJECT_PATH", str(tmp_path))
    calls: list[str] = []

    def execute() -> dict[str, Any]:
        calls.append("ran")
        return {"ok": True, "run_id": "run-replay", "n": len(calls)}

    first = replay_or_execute(
        idempotency_key="replay-key",
        operation="materialize_asset",
        target="silver/orders",
        execute=execute,
    )
    second = replay_or_execute(
        idempotency_key="replay-key",
        operation="materialize_asset",
        target="silver/orders",
        execute=execute,
    )
    assert first == {"ok": True, "run_id": "run-replay", "n": 1}
    assert second == first
    assert calls == ["ran"]


def test_idempotency_outcome_unknown_after_provider_exception_sync(
    monkeypatch, tmp_path: Path
) -> None:
    """A sync provider exception leaves an unknown claim; retry does not invoke it."""
    monkeypatch.setenv("PHLO_PROJECT_PATH", str(tmp_path))
    calls: list[str] = []

    def boom() -> dict[str, Any]:
        calls.append("boom")
        raise RuntimeError("provider failed")

    with pytest.raises(RuntimeError):
        replay_or_execute(
            idempotency_key="boom-key",
            operation="retry_failed_run",
            target="run-boom",
            execute=boom,
        )

    def should_not_run() -> dict[str, Any]:
        calls.append("should-not-run")
        return {"ok": True}

    with pytest.raises(IdempotencyConflict) as info:
        replay_or_execute(
            idempotency_key="boom-key",
            operation="retry_failed_run",
            target="run-boom",
            execute=should_not_run,
        )
    assert info.value.status_code == 409
    assert info.value.detail == {"error": "idempotency_outcome_unknown"}
    assert calls == ["boom"]


def test_unresolved_claim_survives_expiry_until_safe_to_retry(monkeypatch, tmp_path: Path) -> None:
    """Expiry never re-invokes a provider until durable reconciliation permits it."""
    monkeypatch.setenv("PHLO_PROJECT_PATH", str(tmp_path))
    calls: list[str] = []

    def boom() -> dict[str, Any]:
        calls.append("boom")
        raise RuntimeError("provider status unknown")

    with pytest.raises(RuntimeError):
        replay_or_execute(
            idempotency_key="expired-unknown",
            operation="retry_failed_run",
            target="run-expired",
            execute=boom,
        )

    database = tmp_path / ".phlo" / "state" / "operations.sqlite"
    with sqlite3.connect(database) as connection:
        connection.execute("UPDATE operations SET expires_at = ?", ("2000-01-01T00:00:00+00:00",))

    with pytest.raises(IdempotencyConflict, match="idempotency_outcome_unknown"):
        replay_or_execute(
            idempotency_key="expired-unknown",
            operation="retry_failed_run",
            target="run-expired",
            execute=lambda: {"should": "not run"},
        )
    assert calls == ["boom"]

    resolve_idempotency_claim(
        idempotency_key="expired-unknown",
        operation="retry_failed_run",
        target="run-expired",
        resolution="safe_to_retry",
        resolved_by="operator@example.test",
        evidence={"provider_lookup": "mutation was not applied"},
    )
    resolve_idempotency_claim(
        idempotency_key="expired-unknown",
        operation="retry_failed_run",
        target="run-expired",
        resolution="safe_to_retry",
        resolved_by="operator@example.test",
        evidence={"provider_lookup": "mutation was not applied"},
    )
    assert replay_or_execute(
        idempotency_key="expired-unknown",
        operation="retry_failed_run",
        target="run-expired",
        execute=lambda: {"ok": True},
    ) == {"ok": True}

    with sqlite3.connect(database) as connection:
        resolution = connection.execute(
            "SELECT resolution, resolved_by, evidence_json FROM idempotency_resolutions"
        ).fetchone()
    assert resolution == (
        "safe_to_retry",
        "operator@example.test",
        '{"provider_lookup": "mutation was not applied"}',
    )


def test_completed_claim_expires_and_can_execute_again(monkeypatch, tmp_path: Path) -> None:
    """Successful replay records retain their configured expiry behavior."""
    monkeypatch.setenv("PHLO_PROJECT_PATH", str(tmp_path))
    calls: list[int] = []

    def execute() -> dict[str, Any]:
        calls.append(1)
        return {"call": len(calls)}

    replay_or_execute(
        idempotency_key="expired-completed",
        operation="retry_failed_run",
        target="run-completed",
        execute=execute,
    )
    with sqlite3.connect(tmp_path / ".phlo" / "state" / "operations.sqlite") as connection:
        connection.execute("UPDATE operations SET expires_at = ?", ("2000-01-01T00:00:00+00:00",))

    assert replay_or_execute(
        idempotency_key="expired-completed",
        operation="retry_failed_run",
        target="run-completed",
        execute=execute,
    ) == {"call": 2}
    assert calls == [1, 1]


def test_failed_resolution_remains_non_reinvokable(monkeypatch, tmp_path: Path) -> None:
    """A reconciliation result of failed remains a terminal provider outcome."""
    monkeypatch.setenv("PHLO_PROJECT_PATH", str(tmp_path))
    with pytest.raises(RuntimeError):
        replay_or_execute(
            idempotency_key="failed-resolution",
            operation="cancel_run",
            target="run-failed",
            execute=lambda: (_ for _ in ()).throw(RuntimeError("provider unavailable")),
        )

    resolve_idempotency_claim(
        idempotency_key="failed-resolution",
        operation="cancel_run",
        target="run-failed",
        resolution="failed",
        resolved_by="operator@example.test",
        evidence={"provider_lookup": "request was rejected"},
    )

    with pytest.raises(IdempotencyConflict, match="idempotency_outcome_failed"):
        replay_or_execute(
            idempotency_key="failed-resolution",
            operation="cancel_run",
            target="run-failed",
            execute=lambda: {"should": "not run"},
        )


def test_resolution_is_idempotent_across_processes(monkeypatch, tmp_path: Path) -> None:
    """Concurrent reconcilers record one resolution and permit one new claim."""
    monkeypatch.setenv("PHLO_PROJECT_PATH", str(tmp_path))
    with pytest.raises(RuntimeError):
        replay_or_execute(
            idempotency_key="process-resolution",
            operation="cancel_run",
            target="run-process",
            execute=lambda: (_ for _ in ()).throw(RuntimeError("provider unavailable")),
        )

    context = multiprocessing.get_context("fork")
    barrier = context.Barrier(2)
    processes = [
        context.Process(target=_process_idempotency_resolution, args=(str(tmp_path), barrier))
        for _ in range(2)
    ]
    for process in processes:
        process.start()
    for process in processes:
        process.join(timeout=10)
        assert process.exitcode == 0

    calls: list[str] = []
    assert replay_or_execute(
        idempotency_key="process-resolution",
        operation="cancel_run",
        target="run-process",
        execute=lambda: calls.append("ran") or {"ok": True},
    ) == {"ok": True}
    assert calls == ["ran"]

    with sqlite3.connect(tmp_path / ".phlo" / "state" / "operations.sqlite") as connection:
        assert connection.execute("SELECT COUNT(*) FROM idempotency_resolutions").fetchone() == (1,)


def test_idempotency_outcome_unknown_after_provider_exception_async(
    monkeypatch, tmp_path: Path
) -> None:
    """An async provider exception leaves an unknown claim; retry does not invoke it."""
    monkeypatch.setenv("PHLO_PROJECT_PATH", str(tmp_path))
    calls: list[str] = []

    async def boom() -> dict[str, Any]:
        calls.append("boom")
        raise RuntimeError("provider failed")

    with pytest.raises(RuntimeError):
        asyncio.run(
            replay_or_execute_async(
                idempotency_key="boom-async",
                operation="retry_failed_run",
                target="run-boom-async",
                execute=boom,
            )
        )

    async def should_not_run() -> dict[str, Any]:
        calls.append("should-not-run")
        return {"ok": True}

    with pytest.raises(IdempotencyConflict) as info:
        asyncio.run(
            replay_or_execute_async(
                idempotency_key="boom-async",
                operation="retry_failed_run",
                target="run-boom-async",
                execute=should_not_run,
            )
        )
    assert info.value.status_code == 409
    assert info.value.detail == {"error": "idempotency_outcome_unknown"}
    assert calls == ["boom"]


def test_idempotency_independent_for_different_keys_operations_targets(
    monkeypatch, tmp_path: Path
) -> None:
    """Different keys, operations, or targets remain independent."""
    monkeypatch.setenv("PHLO_PROJECT_PATH", str(tmp_path))
    calls: list[str] = []

    def execute() -> dict[str, Any]:
        calls.append("x")
        return {"ok": True, "n": len(calls)}

    replay_or_execute(
        idempotency_key="k1", operation="retry_failed_run", target="run-1", execute=execute
    )
    replay_or_execute(
        idempotency_key="k2", operation="retry_failed_run", target="run-1", execute=execute
    )
    replay_or_execute(idempotency_key="k1", operation="cancel_run", target="run-1", execute=execute)
    replay_or_execute(
        idempotency_key="k1", operation="retry_failed_run", target="run-2", execute=execute
    )
    # Replaying the first identity does not execute again.
    replay_or_execute(
        idempotency_key="k1", operation="retry_failed_run", target="run-1", execute=execute
    )
    assert calls == ["x", "x", "x", "x"]


def test_idempotency_migrated_completed_rows_remain_replayable(monkeypatch, tmp_path: Path) -> None:
    """Existing completed rows remain replayable after schema initialisation/migration."""
    monkeypatch.setenv("PHLO_PROJECT_PATH", str(tmp_path))
    legacy_response = {"legacy": True, "run_id": "run-old", "accepted": True}
    _seed_legacy_completed_row(
        tmp_path,
        idempotency_key="legacy-key",
        operation="materialize_asset",
        target="silver/orders",
        response=legacy_response,
    )

    calls: list[str] = []

    def execute() -> dict[str, Any]:
        calls.append("ran")
        return {"legacy": False}

    replayed = replay_or_execute(
        idempotency_key="legacy-key",
        operation="materialize_asset",
        target="silver/orders",
        execute=execute,
    )
    assert replayed == legacy_response
    assert calls == []


def test_idempotency_schema_migration_is_concurrency_safe(tmp_path: Path) -> None:
    """Concurrent first requests serialize migration of a legacy database."""
    _seed_legacy_completed_row(
        tmp_path,
        idempotency_key="legacy-concurrent",
        operation="cancel_run",
        target="run-legacy",
        response={"ok": True},
    )
    database = tmp_path / ".phlo" / "state" / "operations.sqlite"
    barrier = threading.Barrier(2)
    errors: list[BaseException] = []

    def migrate() -> None:
        connection = sqlite3.connect(database)
        connection.execute("PRAGMA busy_timeout = 5000")
        try:
            barrier.wait()
            _migrate_operations_schema(connection)
        except BaseException as exc:
            errors.append(exc)
        finally:
            connection.close()

    threads = [threading.Thread(target=migrate) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert errors == []
    with sqlite3.connect(database) as connection:
        columns = {
            str(row[1]) for row in connection.execute("PRAGMA table_info(operations)").fetchall()
        }
    assert "state" in columns


def test_idempotency_migrated_completed_rows_replayable_async(monkeypatch, tmp_path: Path) -> None:
    """Async helper also replays migrated completed rows without execution."""
    monkeypatch.setenv("PHLO_PROJECT_PATH", str(tmp_path))
    legacy_response = {"legacy": True, "run_id": "run-old-async"}
    _seed_legacy_completed_row(
        tmp_path,
        idempotency_key="legacy-async",
        operation="backfill_asset",
        target="gold/events",
        response=legacy_response,
    )

    calls: list[str] = []

    async def execute() -> dict[str, Any]:
        calls.append("ran")
        return {"legacy": False}

    replayed = asyncio.run(
        replay_or_execute_async(
            idempotency_key="legacy-async",
            operation="backfill_asset",
            target="gold/events",
            execute=execute,
        )
    )
    assert replayed == legacy_response
    assert calls == []


def test_audit_rotation_preserves_barrier_synchronised_thread_writes(
    monkeypatch, tmp_path: Path
) -> None:
    """Locked threshold check, rotation, and append retain every thread's JSONL record."""
    monkeypatch.setenv("PHLO_PROJECT_PATH", str(tmp_path))
    monkeypatch.setenv("PHLO_API_AUDIT_MAX_BYTES", "250")
    monkeypatch.setenv("PHLO_API_AUDIT_MAX_FILES", "20")
    audit_operation(
        operation="seed",
        target="seed",
        dry_run=False,
        auth={"subject": "test", "scopes": []},
        result={"padding": "x" * 500},
    )
    barrier = threading.Barrier(8)

    def write(index: int) -> None:
        barrier.wait()
        audit_operation(
            operation="thread_write",
            target=str(index),
            dry_run=False,
            auth={"subject": "test", "scopes": []},
            result={"index": index},
        )

    threads = [threading.Thread(target=write, args=(index,)) for index in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    _assert_rotated_audit_is_complete(
        tmp_path / ".phlo" / "audit", {"seed", *(str(index) for index in range(8))}
    )


def test_audit_rotation_preserves_barrier_synchronised_process_writes(
    monkeypatch, tmp_path: Path
) -> None:
    """The owned lock also serializes independent process writers across rotation."""
    monkeypatch.setenv("PHLO_PROJECT_PATH", str(tmp_path))
    monkeypatch.setenv("PHLO_API_AUDIT_MAX_BYTES", "250")
    monkeypatch.setenv("PHLO_API_AUDIT_MAX_FILES", "20")
    audit_operation(
        operation="seed",
        target="seed",
        dry_run=False,
        auth={"subject": "test", "scopes": []},
        result={"padding": "x" * 500},
    )
    context = multiprocessing.get_context("fork")
    barrier = context.Barrier(4)
    processes = [
        context.Process(target=_process_audit_writer, args=(str(tmp_path), barrier, index))
        for index in range(4)
    ]
    for process in processes:
        process.start()
    for process in processes:
        process.join(timeout=10)
        assert process.exitcode == 0

    _assert_rotated_audit_is_complete(
        tmp_path / ".phlo" / "audit", {"seed", *(str(index) for index in range(4))}
    )


def test_audit_rotation_remains_valid_after_restart(monkeypatch, tmp_path: Path) -> None:
    """A fresh writer can append valid JSONL after a durable rotation."""
    monkeypatch.setenv("PHLO_PROJECT_PATH", str(tmp_path))
    monkeypatch.setenv("PHLO_API_AUDIT_MAX_BYTES", "1")
    audit_operation(
        operation="before_restart",
        target="before",
        dry_run=False,
        auth={"subject": "test", "scopes": []},
    )
    audit_operation(
        operation="after_restart",
        target="after",
        dry_run=False,
        auth={"subject": "test", "scopes": []},
    )
    _assert_rotated_audit_is_complete(tmp_path / ".phlo" / "audit", {"before", "after"})


def test_post_provider_audit_partial_outcome_is_unknown_and_never_replays(
    monkeypatch, tmp_path: Path
) -> None:
    """A failed audit after success returns the partial outcome and freezes the identity."""
    monkeypatch.setenv("PHLO_PROJECT_PATH", str(tmp_path))
    calls: list[str] = []

    def execute() -> dict[str, Any]:
        calls.append("provider")
        return {"run_id": "run-1"}

    def fail_audit(_result: dict[str, Any]) -> None:
        raise OSError("disk unavailable")

    with pytest.raises(MutationSucceededAuditFailed) as failure:
        replay_or_execute(
            idempotency_key="audit-failure-key",
            operation="materialize_asset",
            target="silver/orders",
            execute=execute,
            audit=fail_audit,
        )
    assert failure.value.detail == {
        "error": "mutation_succeeded_audit_failed",
        "mutation": {"operation": "materialize_asset", "target": "silver/orders"},
    }
    with pytest.raises(IdempotencyConflict) as retry:
        replay_or_execute(
            idempotency_key="audit-failure-key",
            operation="materialize_asset",
            target="silver/orders",
            execute=execute,
        )
    assert retry.value.detail == {"error": "idempotency_outcome_unknown"}
    assert calls == ["provider"]
