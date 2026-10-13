"""Neutral settings storage contracts and resolution for Observatory backends.

Core defines the ``SettingsStore`` capability protocol, configuration, and
capability resolution.  The durable PostgreSQL implementation lives in
``phlo-postgres`` and is registered through the capability registry; core
never imports a provider package and contains no database driver or SQL code.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from datetime import UTC, datetime
from threading import RLock
from typing import Any, Literal

from jsonschema import ValidationError, validate
from pydantic import AliasChoices, BaseModel, ConfigDict, Field, PrivateAttr, field_validator
from pydantic import ValidationError as ModelValidationError

from phlo.capabilities.settings import SettingsRecord, SettingsScope, SettingsStore
from phlo.config.base import BaseConfig
from phlo.config.process import get_process_settings
from phlo.logging import get_logger

logger = get_logger(__name__)


class StorageUnavailableError(RuntimeError):
    """Sanitised error raised when durable settings storage is unavailable.

    The API boundary maps this to HTTP 503.  The message never contains
    a DSN, password, or other credential.
    """


class StorageCorruptionError(StorageUnavailableError):
    """Sanitised error raised when durable Observatory state is malformed."""


class ObservatoryDatabaseSettings(BaseConfig):
    """Declare the shared UI and durable-storage DSN without backend validation."""

    observatory_settings_db_url: str | None = Field(
        default=None,
        validation_alias=AliasChoices("PHLO_OBSERVATORY_SETTINGS_DB_URL"),
        description="PostgreSQL DSN override shared by Observatory UI and settings storage. Default absent; storage resolves its database fallback at use.",
    )


class ObservatorySettingsStorageConfig(ObservatoryDatabaseSettings, BaseConfig):
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
    from phlo.capabilities.resolver import resolve_capability

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


ADMIN_SETTINGS_NAMESPACE = "phlo.identity.admin-settings"
OPERATIONAL_SETTINGS_PREFIX = "observatory.settings."


class OperationalSettings(BaseModel):
    """Validated settings read by owning services on every evaluation.

    Empty numeric inputs mean no default, never a fabricated SLA. Credentials
    remain in provider configuration; these values contain no secret material.
    """

    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    _revision: int = PrivateAttr(default=0)
    bronze_minutes: int | None = Field(None, alias="freshness.bronze_minutes", ge=1, le=525600)
    silver_minutes: int | None = Field(None, alias="freshness.silver_minutes", ge=1, le=525600)
    gold_minutes: int | None = Field(None, alias="freshness.gold_minutes", ge=1, le=525600)
    open_incident_on_breach: bool = Field(
        False, alias="freshness.open_incident_on_breach", strict=True
    )
    hold_downstream: bool = Field(False, alias="freshness.hold_downstream", strict=True)
    chat_channel: str = Field("", alias="alerts.chat_channel", max_length=120)
    email_digest: str = Field("", alias="alerts.email_digest", max_length=80)
    notify_owners: bool = Field(False, alias="alerts.notify_owners", strict=True)
    notify_consumers: bool = Field(False, alias="alerts.notify_consumers", strict=True)
    target_file_size_mb: int | None = Field(
        None, alias="maintenance.target_file_size_mb", ge=1, le=4096
    )
    expire_snapshots_days: int | None = Field(
        None, alias="maintenance.expire_snapshots_days", ge=1, le=3650
    )
    orphan_cleanup: str = Field("", alias="maintenance.orphan_cleanup", max_length=80)
    keep_tagged_snapshots: bool = Field(
        False, alias="maintenance.keep_tagged_snapshots", strict=True
    )
    compact_nightly: bool = Field(False, alias="maintenance.compact_nightly", strict=True)
    protect_main: bool = Field(False, alias="audit.protect_main", strict=True)
    require_merge_reason: bool = Field(False, alias="audit.require_merge_reason", strict=True)
    sign_release_tags: bool = Field(False, alias="audit.sign_release_tags", strict=True)
    second_gold_reviewer: bool = Field(False, alias="audit.second_gold_reviewer", strict=True)
    retention_years: int | None = Field(None, alias="audit.retention_years", ge=1, le=100)

    @property
    def settings_revision(self) -> int:
        """Revision evaluated by the consumer, not an initiating actor identity."""
        return self._revision

    @field_validator(
        "bronze_minutes",
        "silver_minutes",
        "gold_minutes",
        "target_file_size_mb",
        "expire_snapshots_days",
        "retention_years",
        mode="before",
    )
    @classmethod
    def numeric_input(cls, value: object) -> object:
        if value == "" or value is None:
            return None
        if type(value) is int:
            return value
        if isinstance(value, str) and value.isascii() and value.isdigit():
            return int(value)
        raise ValueError("Use a positive whole number or leave the value empty.")

    @field_validator("chat_channel")
    @classmethod
    def channel_input(cls, value: str) -> str:
        if value and (not value.startswith("#") or any(c.isspace() for c in value)):
            raise ValueError("Chat channel must be a channel name such as #data-platform.")
        return value

    @field_validator("email_digest", "orphan_cleanup")
    @classmethod
    def schedule_input(cls, value: str) -> str:
        if value and not re.fullmatch(
            r"(?:Daily|Weekdays|Sundays) (?:at )?(?:[01][0-9]|2[0-3]):[0-5][0-9]", value
        ):
            raise ValueError("Use Daily, Weekdays or Sundays followed by HH:MM (UTC).")
        return value

    def freshness_sla_seconds(self, layer: str | None) -> int | None:
        """Return a declared layer default; unknown layers have no SLA."""
        if layer is None:
            return None
        minutes = {
            "bronze": self.bronze_minutes,
            "silver": self.silver_minutes,
            "gold": self.gold_minutes,
        }.get(layer)
        return minutes * 60 if minutes is not None else None


def parse_operational_settings(values: dict[str, Any]) -> OperationalSettings:
    """Validate the owned keys without changing other admin settings."""
    return OperationalSettings.model_validate(
        {
            key.removeprefix(OPERATIONAL_SETTINGS_PREFIX): value
            for key, value in values.items()
            if key.startswith(OPERATIONAL_SETTINGS_PREFIX)
        }
    )


def get_operational_settings() -> OperationalSettings:
    """Read the current authorised revision, failing closed on malformed storage."""
    record = get_settings_service().get(SettingsScope.GLOBAL, ADMIN_SETTINGS_NAMESPACE)
    if record is None:
        return OperationalSettings()
    try:
        values = record.settings["values"]
        revision = record.settings.get("version")
        if not isinstance(values, dict) or type(revision) is not int or revision < 0:
            raise ValueError("invalid settings values")
        settings = parse_operational_settings(values)
        settings._revision = revision
        return settings
    except (KeyError, ValueError, ModelValidationError) as exc:
        raise StorageCorruptionError("Stored operational settings are malformed.") from exc


def operational_schedule_slot(schedule: str, at: datetime) -> datetime | None:
    """Return today's due UTC slot for a validated schedule, or no slot."""
    if not schedule:
        return None
    OperationalSettings.schedule_input(schedule)
    now = at.astimezone(UTC)
    if schedule.startswith("Weekdays") and now.weekday() >= 5:
        return None
    if schedule.startswith("Sundays") and now.weekday() != 6:
        return None
    hour, minute = map(int, schedule.split()[-1].split(":"))
    slot = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    return slot if slot <= now else None


def operational_environment_target(env: str) -> tuple[str, str]:
    """Resolve an operator-bound consumer location/ref, never a default branch."""
    try:
        targets = json.loads(get_process_settings()["PHLO_V1_ENVIRONMENTS"])
        if env not in {"prod", "staging"} or set(targets) != {"prod", "staging"}:
            raise ValueError
        if (
            len({targets[name]["dagster_location"] for name in targets}) != 2
            or len({targets[name]["nessie_ref"] for name in targets}) != 2
        ):
            raise ValueError
        location, ref = targets[env]["dagster_location"], targets[env]["nessie_ref"]
        if (
            not isinstance(location, str)
            or not location
            or not isinstance(ref, str)
            or not re.fullmatch(r"[A-Za-z0-9_.-]+", ref)
        ):
            raise ValueError
        return location, ref
    except (KeyError, TypeError, ValueError) as exc:
        raise StorageUnavailableError(
            "Operational consumer environment binding is unavailable."
        ) from exc
