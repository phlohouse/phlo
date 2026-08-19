"""PostgreSQL settings store capability for Observatory settings.

This module is the sole owner of the durable Observatory settings
implementation: psycopg2 usage, SQL table creation, DSN handling, and
connection-failure sanitisation.  It registers a ``SettingsStoreSpec``
with the phlo capability registry so that core's ``get_settings_service``
can resolve it without importing this package directly.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from jsonschema import ValidationError, validate

from phlo.capabilities import SettingsStoreSpec
from phlo.logging import get_logger
from phlo.plugins.observatory_settings import (
    ObservatorySettingsStorageConfig,
    SettingsRecord,
    SettingsScope,
    StorageUnavailableError,
)
from phlo_postgres.settings import get_settings as get_postgres_settings

logger = get_logger(__name__)


def _get_psycopg2():
    try:
        import psycopg2
        import psycopg2.extras
    except ModuleNotFoundError as exc:
        raise ModuleNotFoundError(
            "psycopg2 is required to use PostgreSQL-backed observatory settings storage."
        ) from exc
    return psycopg2


class PostgresSettingsStore:
    """Durable Observatory settings store backed by PostgreSQL.

    The DSN is resolved at construction time from
    :class:`ObservatorySettingsStorageConfig` (explicit override) or
    :mod:`phlo_postgres.settings` (default).  No database connection is
    opened until ``get`` or ``put`` is called, so a transient outage does
    not poison the instance — the next call retries the connection.
    """

    def __init__(self) -> None:
        config = ObservatorySettingsStorageConfig()
        if config.observatory_settings_db_url:
            self._db_url = config.observatory_settings_db_url
        else:
            postgres_settings = get_postgres_settings()
            self._db_url = postgres_settings.get_postgres_connection_string()
        self._table_ensured = False

    def get(self, scope: SettingsScope, namespace: str) -> SettingsRecord | None:
        """Get settings for a scope and namespace."""
        psycopg2 = _get_psycopg2()
        try:
            with psycopg2.connect(self._db_url) as conn:
                self._ensure_table(conn)
                with conn.cursor() as cursor:
                    cursor.execute(
                        """
                        SELECT settings, updated_at
                        FROM phlo_settings
                        WHERE scope = %s AND namespace = %s
                        """,
                        (scope.value, namespace),
                    )
                    row = cursor.fetchone()
                    if not row:
                        logger.debug(
                            "observatory_settings_not_found",
                            scope=scope.value,
                            namespace=namespace,
                        )
                        return None
                    settings, updated_at = row
                    return SettingsRecord(
                        scope=scope,
                        namespace=namespace,
                        settings=settings,
                        updated_at=updated_at.isoformat() if updated_at else None,
                    )
        except Exception as exc:
            if isinstance(exc, StorageUnavailableError):
                raise
            logger.warning("observatory_settings_storage_unavailable", scope=scope.value)
            raise StorageUnavailableError("Settings storage is unavailable") from exc

    def put(
        self,
        scope: SettingsScope,
        namespace: str,
        settings: dict[str, Any],
        schema: dict[str, Any] | None = None,
    ) -> SettingsRecord:
        """Upsert settings for a scope and namespace."""
        self._validate(settings, schema)
        psycopg2 = _get_psycopg2()
        json_settings = psycopg2.extras.Json(settings)
        try:
            with psycopg2.connect(self._db_url) as conn:
                self._ensure_table(conn)
                with conn.cursor() as cursor:
                    cursor.execute(
                        """
                        INSERT INTO phlo_settings (scope, namespace, settings, updated_at)
                        VALUES (%s, %s, %s, NOW())
                        ON CONFLICT (scope, namespace)
                        DO UPDATE SET settings = EXCLUDED.settings, updated_at = NOW()
                        RETURNING settings, updated_at
                        """,
                        (scope.value, namespace, json_settings),
                    )
                    stored_settings, updated_at = cursor.fetchone()
                    conn.commit()
                    return SettingsRecord(
                        scope=scope,
                        namespace=namespace,
                        settings=stored_settings,
                        updated_at=updated_at.isoformat() if updated_at else None,
                    )
        except Exception as exc:
            if isinstance(exc, (StorageUnavailableError, ValueError)):
                raise
            logger.warning("observatory_settings_storage_unavailable", scope=scope.value)
            raise StorageUnavailableError("Settings storage is unavailable") from exc

    def mutate(
        self,
        scope: SettingsScope,
        namespace: str,
        mutation: Callable[[dict[str, Any] | None], dict[str, Any]],
    ) -> SettingsRecord:
        """Apply ``mutation`` while holding the row lock for one settings record."""
        psycopg2 = _get_psycopg2()
        try:
            with psycopg2.connect(self._db_url) as conn:
                self._ensure_table(conn)
                with conn.cursor() as cursor:
                    # A row lock alone cannot lock an absent record. The advisory
                    # transaction lock also serialises first-write migration and
                    # mutation across independent API processes.
                    cursor.execute(
                        "SELECT pg_advisory_xact_lock(hashtext(%s))",
                        (f"{scope.value}:{namespace}",),
                    )
                    cursor.execute(
                        """
                        SELECT settings FROM phlo_settings
                        WHERE scope = %s AND namespace = %s
                        FOR UPDATE
                        """,
                        (scope.value, namespace),
                    )
                    row = cursor.fetchone()
                    settings = mutation(row[0] if row else None)
                    cursor.execute(
                        """
                        INSERT INTO phlo_settings (scope, namespace, settings, updated_at)
                        VALUES (%s, %s, %s, NOW())
                        ON CONFLICT (scope, namespace)
                        DO UPDATE SET settings = EXCLUDED.settings, updated_at = NOW()
                        RETURNING settings, updated_at
                        """,
                        (scope.value, namespace, psycopg2.extras.Json(settings)),
                    )
                    stored_settings, updated_at = cursor.fetchone()
                    conn.commit()
                    return SettingsRecord(
                        scope=scope,
                        namespace=namespace,
                        settings=stored_settings,
                        updated_at=updated_at.isoformat() if updated_at else None,
                    )
        except Exception as exc:
            if isinstance(exc, StorageUnavailableError):
                raise
            logger.warning("observatory_settings_storage_unavailable", scope=scope.value)
            raise StorageUnavailableError("Settings storage is unavailable") from exc

    def _validate(self, settings: dict[str, Any], schema: dict[str, Any] | None) -> None:
        if not schema:
            return
        try:
            validate(instance=settings, schema=schema)
        except ValidationError as exc:
            logger.warning("observatory_settings_validation_failed", error=str(exc))
            raise ValueError(str(exc)) from exc

    def _ensure_table(self, conn) -> None:
        if self._table_ensured:
            return
        with conn.cursor() as cursor:
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS phlo_settings (
                    scope TEXT NOT NULL,
                    namespace TEXT NOT NULL,
                    settings JSONB NOT NULL,
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    PRIMARY KEY (scope, namespace)
                )
                """
            )
            conn.commit()
        self._table_ensured = True
        logger.debug("observatory_settings_table_ensured")


def get_settings_stores() -> list[SettingsStoreSpec]:
    """Return capability specs for the PostgreSQL settings store."""
    return [
        SettingsStoreSpec(
            name="postgres",
            provider=PostgresSettingsStore(),
        )
    ]
