"""Neutral settings storage contracts and resolution for Observatory backends.

Core defines the ``SettingsStore`` capability protocol, configuration, and
capability resolution.  The durable PostgreSQL implementation lives in
``phlo-postgres`` and is registered through the capability registry; core
never imports a provider package and contains no database driver or SQL code.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from threading import RLock
from typing import Any, Literal, Protocol, runtime_checkable

from jsonschema import ValidationError, validate
from pydantic import AliasChoices, Field

from phlo.config.base import BaseConfig
from phlo.logging import get_logger

logger = get_logger(__name__)


class StorageUnavailableError(RuntimeError):
    """Sanitised error raised when durable settings storage is unavailable.

    The API boundary maps this to HTTP 503.  The message never contains
    a DSN, password, or other credential.
    """


class StorageCorruptionError(StorageUnavailableError):
    """Sanitised error raised when durable Observatory state is malformed."""


class ObservatorySettingsStorageConfig(BaseConfig):
    """Configuration for Observatory settings storage.

    The default backend is ``postgres`` (durable).  ``memory`` is permitted
    only through explicit development/test configuration and is rejected
    during regulated startup validation.
    """

    observatory_settings_backend: Literal["postgres", "memory"] = Field(
        default="postgres",
        validation_alias=AliasChoices("PHLO_OBSERVATORY_SETTINGS_BACKEND"),
        description="Settings storage backend: 'postgres' (durable, default) or 'memory' (dev/test only)",
    )

    observatory_settings_db_url: str | None = Field(
        default=None,
        validation_alias=AliasChoices("PHLO_OBSERVATORY_SETTINGS_DB_URL"),
        description="PostgreSQL DSN override for Observatory settings storage",
    )


class SettingsScope(StrEnum):
    """Supported settings scopes."""

    GLOBAL = "global"
    EXTENSION = "extension"


@dataclass(frozen=True)
class SettingsRecord:
    """Stored settings payload and metadata."""

    scope: SettingsScope
    namespace: str
    settings: dict[str, Any]
    updated_at: str | None


@runtime_checkable
class SettingsStore(Protocol):
    """Neutral capability contract for durable settings storage.

    Both global and extension settings endpoints resolve the same
    ``settings_store`` capability; there is no separate per-scope backend.
    """

    def get(self, scope: SettingsScope, namespace: str) -> SettingsRecord | None:
        """Return the stored record for a scope and namespace, or None."""
        ...

    def put(
        self,
        scope: SettingsScope,
        namespace: str,
        settings: dict[str, Any],
        schema: dict[str, Any] | None = None,
    ) -> SettingsRecord:
        """Validate settings against the schema when given, then store and return them."""
        ...

    def mutate(
        self,
        scope: SettingsScope,
        namespace: str,
        mutation: Callable[[dict[str, Any] | None], dict[str, Any]],
    ) -> SettingsRecord:
        """Atomically replace one JSON record using its latest stored value."""
        ...


class InMemorySettingsService:
    """In-memory settings service for explicit development/test configuration.

    This backend is never selected by default.  It is returned only when
    ``observatory_settings_backend`` is explicitly set to ``memory`` and
    is rejected during regulated startup validation.
    """

    def __init__(self) -> None:
        self._store: dict[tuple[SettingsScope, str], SettingsRecord] = {}
        self._lock = RLock()

    def get(self, scope: SettingsScope, namespace: str) -> SettingsRecord | None:
        """Return the stored record for a scope and namespace, or None."""
        return self._store.get((scope, namespace))

    def put(
        self,
        scope: SettingsScope,
        namespace: str,
        settings: dict[str, Any],
        schema: dict[str, Any] | None = None,
    ) -> SettingsRecord:
        """Validate settings against the schema when given (ValueError on failure), then store."""
        if schema:
            try:
                validate(instance=settings, schema=schema)
            except ValidationError as exc:
                raise ValueError(str(exc)) from exc
        with self._lock:
            record = SettingsRecord(
                scope=scope,
                namespace=namespace,
                settings=settings,
                updated_at=None,
            )
            self._store[(scope, namespace)] = record
            return record

    def mutate(
        self,
        scope: SettingsScope,
        namespace: str,
        mutation: Callable[[dict[str, Any] | None], dict[str, Any]],
    ) -> SettingsRecord:
        """Atomically replace the record with one computed from its latest stored value."""
        with self._lock:
            current = self._store.get((scope, namespace))
            settings = mutation(current.settings if current else None)
            record = SettingsRecord(
                scope=scope, namespace=namespace, settings=settings, updated_at=None
            )
            self._store[(scope, namespace)] = record
            return record


# Module-level singleton for memory mode so that writes persist across
# requests within a single dev/test process.  Durable (postgres) mode
# does NOT use this cache — it resolves the capability on every call.
_memory_service: InMemorySettingsService | None = None


def get_settings_service() -> SettingsStore:
    """Resolve the settings store for the configured backend.

    - ``postgres`` (default): resolves the ``settings_store`` capability
      registered by ``phlo-postgres``.  If the capability is not registered,
      :class:`StorageUnavailableError` is raised so the API boundary can
      return 503.  A later call retries capability resolution and recovers
      without a process restart.  The provider reads the DSN override from
      :class:`ObservatorySettingsStorageConfig` when present.

    - ``memory`` (explicit dev/test): returns a process-local
      :class:`InMemorySettingsService` singleton.  Rejected in regulated mode.

    This function is NOT cached: each call performs a fresh capability
    resolution so that transient failures are never sticky.
    """
    config = ObservatorySettingsStorageConfig()
    backend = config.observatory_settings_backend

    if backend == "memory":
        global _memory_service
        if _memory_service is None:
            _memory_service = InMemorySettingsService()
            logger.debug("observatory_settings_service_initialized", backend="memory")
        return _memory_service

    # postgres mode (default) — resolve the durable settings store through
    # the neutral capability registry.  Core never imports a provider
    # package directly.  The DSN override is read by the provider.
    from phlo.capabilities import resolve_capability

    result = resolve_capability("settings_store")
    if result is None:
        logger.warning("observatory_settings_storage_unavailable", reason="no_provider")
        raise StorageUnavailableError("Durable settings storage backend is not available")
    logger.debug(
        "observatory_settings_service_initialized",
        backend="postgres_capability",
        provider=result.name,
    )
    return result.provider


def _reset_memory_service() -> None:
    """Clear the memory-mode singleton (test helper)."""
    global _memory_service
    _memory_service = None
