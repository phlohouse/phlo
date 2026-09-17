"""Dagster extension emitting terminal ``pipeline.run`` events per run.

Asset/step/check events are emitted inside the run worker by the adapter
instrumentation; this extension adds the run-boundary event. Run-status
sensors evaluate in the Dagster daemon, so the terminal ``pipeline.run`` is
emitted with explicit correlation rather than ambient context.

Run identity is the *physical* Dagster ``run_id`` — one observer run per
attempt. The logical Phlo run id (``phlo/run_id`` tag) and attempt number are
carried as attributes so retries group logically without violating the
observer's monotonic run-status precedence (a failed attempt must not pin a
later successful attempt to "failed").
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import phlo.telemetry as phlo_observe
from phlo.logging import get_logger
from phlo.plugins.base import PluginMetadata
from phlo_dagster.dagster_ext import DagsterExtensionPlugin

logger = get_logger(__name__)

_WAP_RUN_ID_TAG = "phlo/run_id"
_WAP_ATTEMPT_TAG = "phlo/attempt"
_WAP_BRANCH_TAG = "phlo/wap_branch"
_WAP_CATALOG_SYSTEM_TAG = "phlo/catalog_system"
_PARTITION_TAG = "dagster/partition"

_STATUS_OUTCOME = {
    "SUCCESS": "success",
    "FAILURE": "failure",
    "CANCELED": "cancelled",
}


def _ts(epoch: Any) -> datetime | None:
    try:
        return datetime.fromtimestamp(float(epoch), UTC)
    except (TypeError, ValueError, OverflowError):
        return None


def _emit_pipeline_run(context: Any, dagster_status: Any) -> None:
    """Emit the terminal ``pipeline.run`` event for one Dagster run."""
    if not phlo_observe.available():
        return
    try:
        run = context.dagster_run
        tags = dict(run.tags or {})
        run_id = run.run_id
        partition_key = tags.get(_PARTITION_TAG)
        branch = tags.get(_WAP_BRANCH_TAG)
        # DagsterRunStatus is a plain Enum: str() gives 'DagsterRunStatus.X',
        # so normalize through .value to the bare status name.
        status_key = str(getattr(dagster_status, "value", dagster_status))

        entities: dict[str, Any] = {"run": phlo_observe.run_entity_for("dagster", run_id)}
        if branch:
            # The staging ref is owned by the catalog the launch resolved —
            # Nessie for the branch strategy, the snapshot catalog (e.g.
            # polaris) for the snapshot strategy.
            entities["branch"] = phlo_observe.branch_entity_id(
                branch, system=tags.get(_WAP_CATALOG_SYSTEM_TAG) or "nessie"
            )

        ended_at = _ts(getattr(context.dagster_event, "timestamp", None))
        started_at = None
        duration_ms = None
        try:
            stats = context.instance.get_run_stats(run_id)
            started_at = _ts(getattr(stats, "start_time", None)) or _ts(
                getattr(stats, "launch_time", None)
            )
            if started_at and ended_at:
                duration_ms = (ended_at - started_at).total_seconds() * 1000.0
        except Exception:  # noqa: BLE001 - timing is best-effort
            pass

        attributes: dict[str, Any] = {
            "job_name": run.job_name,
            "dagster_status": status_key,
            "phlo_run_id": tags.get(_WAP_RUN_ID_TAG),
            "root_run_id": run.root_run_id,
            "parent_run_id": run.parent_run_id,
        }
        attempt = tags.get(_WAP_ATTEMPT_TAG)
        if attempt:
            attributes["attempt"] = attempt
        selection = getattr(run, "asset_selection", None)
        if selection:
            attributes["asset_keys"] = sorted(str(key) for key in selection)

        phlo_observe.emit(
            "pipeline.run",
            category="pipeline",
            outcome=_STATUS_OUTCOME.get(status_key, "unknown"),
            severity="error" if status_key == "FAILURE" else "info",
            attributes=attributes,
            correlation={
                "run_id": run_id,
                "job_id": run.job_name,
                "partition_key": partition_key,
                "branch": branch,
            },
            entities=entities or None,
            producer="dagster",
            started_at=started_at,
            ended_at=ended_at,
            duration_ms=duration_ms,
        )
    except Exception:  # noqa: BLE001 - telemetry must never fail a sensor tick
        logger.debug("observe_run_status_emit_failed", exc_info=True)


class ObserveDagsterExtension(DagsterExtensionPlugin):
    """Contribute run-status sensors that close observer runs terminally."""

    @property
    def metadata(self) -> PluginMetadata:
        """Return plugin metadata for discovery and identification."""
        return PluginMetadata(
            name="observe",
            version="0.1.0",
            description="phlo-observe run-boundary sensors for Dagster",
        )

    def get_definitions(self) -> Any:
        """Return the run-status sensors as Dagster definitions."""
        import dagster as dg

        def _sensor(status: dg.DagsterRunStatus, suffix: str) -> Any:
            @dg.run_status_sensor(
                run_status=status,
                name=f"observe_run_{suffix}",
                monitor_all_code_locations=True,
                # run_status_sensor defaults to STOPPED: without RUNNING the
                # terminal pipeline.run events this extension exists to emit
                # would never fire in a deployed stack.
                default_status=dg.DefaultSensorStatus.RUNNING,
            )
            def _observe_run(context: Any) -> None:
                _emit_pipeline_run(context, status)

            return _observe_run

        return dg.Definitions(
            sensors=[
                _sensor(dg.DagsterRunStatus.SUCCESS, "success"),
                _sensor(dg.DagsterRunStatus.FAILURE, "failure"),
                _sensor(dg.DagsterRunStatus.CANCELED, "canceled"),
            ]
        )
