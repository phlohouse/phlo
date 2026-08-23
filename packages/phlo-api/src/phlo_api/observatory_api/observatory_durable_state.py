"""Transactional durable state for project-scoped Observatory collections.

Collections live in durable state namespaced by a hashed project root, so
different projects never share records. A schema_version mismatch or unreadable
state raises StorageCorruptionError instead of degrading to empty data; legacy
JSON files are imported into the store exactly once on first load.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from hashlib import sha256
import json
from pathlib import Path
from typing import Any

from phlo.logging import get_logger
from phlo.plugins.observatory_settings import (
    SettingsScope,
    StorageCorruptionError,
    get_settings_service,
)

logger = get_logger(__name__)

STATE_SCHEMA_VERSION = 1


def state_namespace(project_root: Path, collection: str) -> str:
    """Return a stable, non-sensitive namespace for one project's collection."""
    project_id = sha256(str(project_root.resolve()).encode()).hexdigest()
    return f"observatory.{collection}.{project_id}"


def load_collection(project_root: Path, collection: str, legacy_path: Path) -> list[dict[str, Any]]:
    """Load a collection, importing its legacy JSON file exactly once when needed."""
    record = get_settings_service().get(
        SettingsScope.GLOBAL, state_namespace(project_root, collection)
    )
    if record is not None:
        return _items_from_state(record.settings, collection)
    return _mutate_collection(project_root, collection, legacy_path, lambda items: items)


def mutate_collection(
    project_root: Path,
    collection: str,
    legacy_path: Path,
    mutation: Callable[[list[dict[str, Any]]], list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    """Apply a collection mutation under the durable store's transaction."""
    return _mutate_collection(project_root, collection, legacy_path, mutation)


def _mutate_collection(
    project_root: Path,
    collection: str,
    legacy_path: Path,
    mutation: Callable[[list[dict[str, Any]]], list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    namespace = state_namespace(project_root, collection)
    result_items: list[dict[str, Any]] = []

    def apply(current: dict[str, Any] | None) -> dict[str, Any]:
        """Seed items from the legacy JSON on the first transaction pass, apply the
        mutation, and return the wrapped durable payload.
        """
        nonlocal result_items
        # current is None only while the namespace has never been persisted; seed
        # from the legacy JSON file on that first pass. Once this transaction
        # commits, every later load finds the durable record and never reads the
        # legacy file again, making the import exactly-once.
        items = (
            _items_from_state(current, collection)
            if current is not None
            else _legacy_items(legacy_path, collection)
        )
        result_items = mutation(items)
        return {"schema_version": STATE_SCHEMA_VERSION, "items": result_items}

    get_settings_service().mutate(SettingsScope.GLOBAL, namespace, apply)
    return result_items


def _items_from_state(state: dict[str, Any], collection: str) -> list[dict[str, Any]]:
    if state.get("schema_version") != STATE_SCHEMA_VERSION or not isinstance(
        state.get("items"), list
    ):
        _raise_corruption(collection, "durable_payload")
    items = state["items"]
    if not all(isinstance(item, dict) for item in items):
        _raise_corruption(collection, "durable_items")
    return items


def _legacy_items(path: Path, collection: str) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        _raise_corruption(collection, "legacy_json")
    if not isinstance(payload, Mapping) or not isinstance(payload.get("items"), list):
        _raise_corruption(collection, "legacy_shape")
    items = payload["items"]
    if not all(isinstance(item, dict) for item in items):
        _raise_corruption(collection, "legacy_items")
    return items


def _raise_corruption(collection: str, location: str) -> None:
    logger.error("observatory_durable_state_corrupt", collection=collection, location=location)
    raise StorageCorruptionError("Observatory durable state is unavailable")
