"""Durable, cross-process operation journal stores (ADR 0049 §1).

Core owns the atomic state machine in :mod:`phlo.operations.journal`; this
module supplies a durable provider so the advertised exactly-once contract
holds through the CLI across process boundaries and restarts.

:class:`FileOperationJournalStore` persists one JSON record per operation
under a configured directory using atomic rename, so a second CLI process or
a process restart observes and honours earlier claims instead of silently
re-trying a destructive operation. Production deployments may substitute any
store satisfying :class:`phlo.operations.journal.OperationJournalStore`
(e.g. a PostgreSQL adapter); this file-backed provider is the durable default
for the CLI and is fully testable without live services.
"""

from __future__ import annotations

import fcntl
import json
import os
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from phlo.operations.journal import (
    OperationJournalEntry,
    OperationJournalState,
)


class FileOperationJournalStore:
    """A durable operation journal persisted under a directory.

    Records are written atomically (temporary file + rename) so a crashed
    writer never leaves a torn record. ``claim`` refuses an existing active
    claim for the same ``(action, target)``, matching the core contract, and
    refuses to re-claim an operation that already exists. Records survive the
    process, making replay and exactly-once behaviour hold across restarts.
    """

    def __init__(self, directory: str | os.PathLike[str]) -> None:
        self._directory = Path(directory)
        self._directory.mkdir(parents=True, exist_ok=True)
        self._lock_path = self._directory / ".operation-journal.lock"

    # -- paths -------------------------------------------------------------

    def _path(self, operation_id: str) -> Path:
        return self._directory / f"{self._safe_name(operation_id)}.json"

    @staticmethod
    def _safe_name(operation_id: str) -> str:
        """Map an operation id to a single, flat, filesystem-safe filename segment.

        Operation ids embed the absolute restore/upgrade ``target_id`` (which
        contains ``/``) and the ``:`` separators (which many platforms treat as
        path separators in globbing). Collapse every non-portable character to
        ``_``so a durable record is one flat file and reads back deterministically.
        """
        return "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in operation_id)

    # -- protocol ----------------------------------------------------------

    def claim(self, entry: OperationJournalEntry) -> bool:
        # An atomic rename prevents torn records, but it does not make the
        # read/check/write claim sequence atomic. Serialize that sequence
        # across CLI processes so only one active claim can own a target.
        with self._locked():
            if self._path(entry.operation_id).exists():
                return False
            active_order = {
                OperationJournalState.CLAIMED,
                OperationJournalState.SUBMITTED,
                OperationJournalState.UNKNOWN,
            }
            for record in self._iter_records():
                if (
                    record["action"] == entry.action
                    and record["target"] == entry.target
                    and OperationJournalState(record["state"]) in active_order
                ):
                    return False
            self._write_atomic(entry)
            return True

    def transition(
        self, operation_id: str, state: OperationJournalState, result: dict[str, Any] | None = None
    ) -> bool:
        with self._locked():
            path = self._path(operation_id)
            if not path.is_file():
                return False
            record = json.loads(path.read_text(encoding="utf-8"))
            record["state"] = state.value
            record["result"] = result
            self._write_json_atomic(path, record)
            return True

    def read(self, operation_id: str) -> OperationJournalEntry | None:
        with self._locked():
            path = self._path(operation_id)
            if not path.is_file():
                return None
            record = json.loads(path.read_text(encoding="utf-8"))
        return OperationJournalEntry(
            operation_id=str(record["operation_id"]),
            subject=str(record["subject"]),
            action=str(record["action"]),
            target=str(record["target"]),
            plan_token=str(record["plan_token"]),
            state=OperationJournalState(str(record["state"])),
            claim_expiry=str(record.get("claim_expiry") or ""),
            result=record.get("result"),
            observation_time=str(record.get("observation_time") or ""),
        )

    # -- helpers -----------------------------------------------------------

    @contextmanager
    def _locked(self) -> Any:
        """Hold the journal-wide advisory lock for a state transition."""
        with self._lock_path.open("a+", encoding="utf-8") as lock_file:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)

    def _iter_records(self) -> Any:
        for path in sorted(self._directory.glob("*.json")):
            try:
                yield json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue

    def _write_atomic(self, entry: OperationJournalEntry) -> None:
        self._write_json_atomic(self._path(entry.operation_id), entry.to_dict())

    def _write_json_atomic(self, path: Path, record: dict[str, Any]) -> None:
        temporary = path.with_suffix(".json.tmp")
        temporary.write_text(
            json.dumps(record, sort_keys=True, separators=(",", ":")), encoding="utf-8"
        )
        temporary.replace(path)


__all__ = ["FileOperationJournalStore"]
