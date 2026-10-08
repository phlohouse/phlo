"""Validate generated Dagster instance overrides without constructing an instance.

Native schemas check instance fields and the bundled coordinator and launcher.
Environment source values remain unresolved; custom classes receive envelope checks only.
"""

from __future__ import annotations

from typing import Any

import yaml
from dagster import DefaultRunLauncher, QueuedRunCoordinator
from dagster._config import validate_config
from dagster._config.errors import EvaluationError
from dagster._config.stack import EvaluationStackListItemEntry, EvaluationStackPathEntry
from dagster._core.instance.config import dagster_instance_config_schema


def _error_location(error: EvaluationError, prefix: str) -> str:
    parts = [prefix]
    for entry in error.stack.entries:
        if isinstance(entry, EvaluationStackPathEntry):
            parts.append(f".{entry.field_name}")
        elif isinstance(entry, EvaluationStackListItemEntry):
            parts.append(f"[{entry.list_index}]")
        else:
            parts.append(".[map entry]")
    fields = getattr(error.error_data, "field_names", None)
    field = getattr(error.error_data, "field_name", None)
    if fields:
        parts.append("." + ",".join(fields))
    elif field:
        parts.append(f".{field}")
    return "".join(parts)


def _validate(schema: Any, value: Any, prefix: str) -> None:
    result = validate_config(schema, value)
    if not result.success:
        diagnostics = "; ".join(
            f"{_error_location(error, prefix)}: {error.reason.value}"
            for error in result.errors or []
        )
        # Dagster's error.message includes supplied scalar values. Do not echo it.
        raise ValueError(diagnostics)


def validate_service_file(destination: str, content: str) -> None:
    """Validate effective dagster.yaml with native schemas without reading secrets."""
    if destination != "dagster/dagster.yaml":
        return
    config = yaml.safe_load(content)
    _validate(dagster_instance_config_schema(), config, destination)
    if "run_queue" in config and "run_coordinator" in config:
        raise ValueError(f"{destination}.run_queue: incompatible with run_coordinator")
    if "storage" in config and any(
        section in config for section in ("run_storage", "event_log_storage", "schedule_storage")
    ):
        raise ValueError(f"{destination}.storage: incompatible with legacy storage fields")
    known_classes = {
        ("phlo_dagster.daemon_identity", "PhloQueuedRunCoordinator"): QueuedRunCoordinator,
        (
            "dagster._core.run_coordinator.queued_run_coordinator",
            "QueuedRunCoordinator",
        ): QueuedRunCoordinator,
        ("dagster.core.run_coordinator", "QueuedRunCoordinator"): QueuedRunCoordinator,
        ("dagster", "QueuedRunCoordinator"): QueuedRunCoordinator,
        ("dagster.core.launcher", "DefaultRunLauncher"): DefaultRunLauncher,
        ("dagster._core.launcher.default_run_launcher", "DefaultRunLauncher"): DefaultRunLauncher,
        ("dagster", "DefaultRunLauncher"): DefaultRunLauncher,
    }
    for section in ("run_coordinator", "run_launcher"):
        entry = config.get(section)
        if entry is None:
            continue
        cls = known_classes.get((entry["module"], entry["class"]))
        if cls is None:
            continue
        values = entry.get("config", {})
        _validate(cls.config_type(), values, f"{destination}.{section}.config")
        if cls is QueuedRunCoordinator:
            maximum = values.get("max_concurrent_runs")
            if isinstance(maximum, int) and maximum < -1:
                raise ValueError(
                    f"{destination}.{section}.config.max_concurrent_runs: must be at least -1"
                )
