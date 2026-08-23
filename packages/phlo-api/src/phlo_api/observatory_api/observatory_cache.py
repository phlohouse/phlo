"""Shared TTL cache backing Observatory read model queries.

Entries are keyed by project and read model name; loads are single-flight
per key so concurrent callers share one loader run instead of stampeding
it. clear() bumps a generation counter so an in-flight load never
republishes state the caller asked to forget. When db_path is configured,
values also persist to SQLite with wall-clock expiry as a cross-process
fallback source.
"""

from __future__ import annotations

from contextlib import closing
import json
from pathlib import Path
import sqlite3
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel


@dataclass(slots=True)
class ReadModelCache:
    """Small TTL cache scoped by project and read model name."""

    project_key: Callable[[], str]
    db_path: Callable[[], Path] | None = None
    _values: dict[tuple[str, str], tuple[float, Any]] = field(default_factory=dict)
    _lock: threading.RLock = field(default_factory=threading.RLock)
    _in_flight: dict[tuple[str, str], threading.Event] = field(default_factory=dict)
    _generation: int = 0

    def cached(self, name: str, ttl_seconds: float, loader: Callable[[], Any]) -> Any:
        """Return the cached value for ``name`` when within ``ttl_seconds``,
        otherwise run ``loader`` single-flight and repopulate the entry."""
        project = self.project_key()
        key = (project, name)
        epoch_now = time.time()

        # Single-flight per key: exactly one caller claims the key and runs the
        # loader while the rest block on its event, then re-enter the loop to
        # pick up the published value instead of stampeding the loader.
        while True:
            with self._lock:
                cached = self._values.get(key)
                if cached is not None and cached[0] > time.monotonic():
                    return cached[1]
                in_flight = self._in_flight.get(key)
                if in_flight is None:
                    in_flight = threading.Event()
                    self._in_flight[key] = in_flight
                    generation = self._generation
                    break
            in_flight.wait()

        try:
            stored = self._load_stored(project, name, epoch_now)
            if stored is not None:
                expires_at, value = stored
            else:
                value = loader()
                expires_at = time.time() + ttl_seconds
            with self._lock:
                # clear() bumps _generation under the lock. If invalidation ran
                # while this load was in flight, drop the result instead of
                # republishing state the caller asked to forget. Waiters wake,
                # find nothing published, and reload for themselves.
                if generation == self._generation:
                    # Expiry lives as wall clock time in SQLite but as a
                    # monotonic deadline here, so wall-clock adjustments cannot
                    # extend or cut short in-memory entries.
                    self._values[key] = (
                        time.monotonic() + max(0, expires_at - time.time()),
                        value,
                    )
                    if stored is None:
                        self._store(project, name, expires_at, value)
            return value
        finally:
            with self._lock:
                self._in_flight.pop(key, None)
                in_flight.set()

    def clear(self) -> None:
        """Drop in-memory entries, bump the generation counter, and clear
        any SQLite persistence so stale values cannot resurface."""
        with self._lock:
            self._generation += 1
            self._values.clear()
            path = self._db_path()
            if path is None or not path.exists():
                return
            with closing(sqlite3.connect(path)) as connection, connection:
                connection.execute("delete from read_models")

    def _db_path(self) -> Path | None:
        if self.db_path is None:
            return None
        return self.db_path()

    def _connect(self) -> sqlite3.Connection | None:
        path = self._db_path()
        if path is None:
            return None
        path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(path)
        connection.execute(
            """
            create table if not exists read_models (
              project_key text not null,
              name text not null,
              expires_at real not null,
              payload blob not null,
              primary key (project_key, name)
            )
            """
        )
        return connection

    def _load_stored(self, project: str, name: str, now: float) -> tuple[float, Any] | None:
        connection = self._connect()
        if connection is None:
            return None
        with closing(connection), connection:
            row = connection.execute(
                "select expires_at, payload from read_models where project_key = ? and name = ?",
                (project, name),
            ).fetchone()
            if row is None:
                return None
            expires_at = float(row[0])
            if expires_at <= now:
                connection.execute(
                    "delete from read_models where project_key = ? and name = ?",
                    (project, name),
                )
                return None
            # An unreadable payload (schema drift, partial write) is a miss, not
            # an error: drop the row so the next cached() call reloads from the
            # source instead of failing every request on this key.
            try:
                return expires_at, _deserialize_value(row[1])
            except (TypeError, ValueError, json.JSONDecodeError):
                connection.execute(
                    "delete from read_models where project_key = ? and name = ?",
                    (project, name),
                )
                return None

    def _store(self, project: str, name: str, expires_at: float, value: Any) -> None:
        connection = self._connect()
        if connection is None:
            return
        # A value JSON cannot represent is simply not persisted; it still lives
        # in the in-memory layer for this process.
        try:
            payload = _serialize_value(value)
        except (TypeError, ValueError):
            return
        with closing(connection), connection:
            connection.execute(
                """
                insert into read_models(project_key, name, expires_at, payload)
                values (?, ?, ?, ?)
                on conflict(project_key, name)
                do update set expires_at = excluded.expires_at, payload = excluded.payload
                """,
                (project, name, expires_at, payload),
            )


def _serialize_value(value: Any) -> str:
    if isinstance(value, BaseModel):
        value = {
            "__pydantic_model__": value.__class__.__name__,
            "data": value.model_dump(mode="json"),
        }
    return json.dumps(value, allow_nan=False, separators=(",", ":"))


def _deserialize_value(payload: str | bytes) -> Any:
    value = json.loads(payload)
    if not isinstance(value, dict) or set(value) != {"__pydantic_model__", "data"}:
        return value
    model_name = value["__pydantic_model__"]
    if not isinstance(model_name, str):
        raise ValueError("Invalid cached Pydantic model name")
    from phlo_api.observatory_api import observatory_models

    model = getattr(observatory_models, model_name, None)
    if not isinstance(model, type) or not issubclass(model, BaseModel):
        raise ValueError("Cached Pydantic model is not allow-listed")
    return model.model_validate(value["data"])
