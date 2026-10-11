"""Provider-neutral settings storage contract."""

from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Protocol, runtime_checkable


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
