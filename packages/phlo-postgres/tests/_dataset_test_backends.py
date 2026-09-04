"""Shared test backends for Dataset state store tests (not collected by pytest).

``FileLockSettingsStore`` is a test double for the neutral ``SettingsStore``
capability: a JSON file whose ``mutate`` transaction is serialized by an
``fcntl`` exclusive lock, standing in for the PostgreSQL advisory-lock
transaction the real provider uses. It exists so the provider-owned Dataset
state store can be proven atomic across real operating-system processes.
"""

from __future__ import annotations

import contextlib
import fcntl
import json
import os
from collections.abc import Callable
from pathlib import Path
from typing import Any, Iterator

from phlo.plugins.observatory_settings import (
    SettingsRecord,
    SettingsScope,
)


class FileLockSettingsStore:
    """File-backed settings store with a process-spanning locked mutate.

    Writes are atomic (temp file + ``os.replace``) and serialized by an
    ``fcntl`` lock on a dedicated lock file, so a reader — locked or not —
    never observes a torn document, mirroring the transactional guarantees
    the real PostgreSQL backend derives from its advisory row lock.
    """

    def __init__(self, path: Path) -> None:
        self._path = path
        self._lock_path = path.with_suffix(path.suffix + ".lock")
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.touch(exist_ok=True)
        self._lock_path.touch(exist_ok=True)

    def get(self, scope: SettingsScope, namespace: str) -> SettingsRecord | None:
        # Reads take the lock too: the durable PostgreSQL backend reads inside
        # a transaction, so the double must never observe a torn write.
        with self._transaction() as entries:
            entry = entries.get(self._key(scope, namespace))
        if entry is None:
            return None
        return SettingsRecord(
            scope=scope,
            namespace=namespace,
            settings=entry["settings"],
            updated_at=entry.get("updated_at"),
        )

    def put(
        self,
        scope: SettingsScope,
        namespace: str,
        settings: dict[str, Any],
        schema: dict[str, Any] | None = None,
    ) -> SettingsRecord:
        with self._transaction() as entries:
            entries[self._key(scope, namespace)] = {"settings": settings, "updated_at": None}
        return SettingsRecord(scope=scope, namespace=namespace, settings=settings, updated_at=None)

    def mutate(
        self,
        scope: SettingsScope,
        namespace: str,
        mutation: Callable[[dict[str, Any] | None], dict[str, Any]],
    ) -> SettingsRecord:
        key = self._key(scope, namespace)
        with self._transaction() as entries:
            entry = entries.get(key)
            settings = mutation(entry["settings"] if entry else None)
            entries[key] = {"settings": settings, "updated_at": None}
        return SettingsRecord(scope=scope, namespace=namespace, settings=settings, updated_at=None)

    @contextlib.contextmanager
    def _transaction(self) -> Iterator[dict[str, dict[str, Any]]]:
        """Hold the lock across read, mutation, and atomic write."""
        with open(self._lock_path, "r+") as lock_handle:
            fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX)
            try:
                entries = json.loads(self._path.read_text() or "{}")
                yield entries
                temp_path = self._path.with_suffix(self._path.suffix + ".tmp")
                temp_path.write_text(json.dumps(entries), encoding="utf-8")
                os.replace(temp_path, self._path)
            finally:
                fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)

    @staticmethod
    def _key(scope: SettingsScope, namespace: str) -> str:
        return f"{scope.value}:{namespace}"

    def _read_all(self) -> dict[str, dict[str, Any]]:
        return json.loads(self._path.read_text(encoding="utf-8") or "{}")
