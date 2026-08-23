"""Dagster sensors for automated alerting on pipeline failures.

This module provides Dagster sensors that monitor run events and send alerts
when pipeline failures occur. It integrates with Phlo's alerting capabilities
to provide notifications via configured alert sinks (Slack, PagerDuty, etc.).

Key Features:
    - failure_alert_sensor: Polls for failed runs and sends alerts
    - Automatic error message extraction from run events
    - Configurable alert sink resolution
    - Deduplication via cursor tracking

Alert Sink Requirements:
    Requires an alert_sink:alerting capability. Install phlo-alerting or another
    provider exposing that capability.

Example:
    To enable alerting in your Dagster deployment::

        from phlo_dagster.alerting_sensor import failure_alert_sensor

        defs = dg.Definitions(
            sensors=[failure_alert_sensor],
            # ... other definitions
        )

"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from dagster import DagsterEventType, DagsterRunStatus, RunsFilter, sensor

from phlo.capabilities import AlertSink, resolve_capability
from phlo.logging import get_logger

logger = get_logger(__name__)


def _load_alert_sink() -> AlertSink:
    """Resolve the configured alert sink capability.

    Raise RuntimeError when the alert_sink:alerting capability is unavailable.
    """
    resolution = resolve_capability("alert_sink", "alerting")
    if resolution is None:
        raise RuntimeError(
            "Alerting integration requires an alert_sink:alerting capability. "
            "Install phlo-alerting or another provider exposing that capability."
        )
    return resolution.provider


@sensor(
    name="failure_alert_sensor",
    description="Send alerts on run failures",
    minimum_interval_seconds=300,  # Check every 5 minutes
)
def failure_alert_sensor(context):
    """Trigger alerts for asset materialization failures.

    Tracks the last-seen run creation cutoff in the sensor cursor to avoid
    re-alerting across ticks. Re-raises any exception during alert processing.
    """
    instance = context.instance

    # Parse cursor as ISO datetime for dedup across ticks
    cutoff_time = None
    if context.cursor:
        try:
            cutoff_time = datetime.fromisoformat(context.cursor)
        except ValueError:
            cutoff_time = None

    if cutoff_time is None:
        cutoff_time = datetime.now(timezone.utc) - timedelta(minutes=5)

    alerted_count = 0
    scanned_event_count = 0

    logger.info(
        "failure_alert_sensor_scan_started",
        cutoff_time=cutoff_time.isoformat(),
    )
    try:
        alert_sink = _load_alert_sink()
        # Query for failed runs created after the cursor cutoff
        failed_runs = list(
            instance.get_runs(
                filters=RunsFilter(
                    statuses=[DagsterRunStatus.FAILURE],
                    updated_after=cutoff_time,
                )
            )
        )
        for run in failed_runs:
            # Get run events to find failures
            events = list(
                instance.get_event_log_entries(
                    run_id=run.run_id,
                    event_filter_fn=lambda event: (
                        event.event_type == DagsterEventType.PIPELINE_FAILURE
                    ),
                )
            )
            scanned_event_count += len(events)

            for event in events:
                # Build alert
                if alert_sink.send_alert(
                    title=f"Pipeline Run Failed: {run.job_name}",
                    message=f"Run {run.run_id} for job {run.job_name} has failed",
                    severity="ERROR",
                    asset_name=run.job_name,
                    run_id=run.run_id,
                    error_message=_extract_error_message(event),
                ):
                    alerted_count += 1
                    logger.info("failure_alert_sent", run_id=run.run_id, job_name=run.job_name)

        logger.info(
            "failure_alert_sensor_scan_completed",
            cutoff_time=cutoff_time.isoformat(),
            failed_run_count=len(failed_runs),
            failure_event_count=scanned_event_count,
            alerts_sent_count=alerted_count,
        )
    except Exception as exc:
        logger.error(
            "failure_alert_sensor_scan_failed",
            cutoff_time=cutoff_time.isoformat(),
            failure_event_count=scanned_event_count,
            alerts_sent_count=alerted_count,
            error=str(exc),
            exc_info=True,
        )
        raise

    # Advance cursor to now so next tick only sees newer runs
    context.update_cursor(datetime.now(timezone.utc).isoformat())


def _extract_error_message(event) -> str | None:
    """Extract the error message from a Dagster event log entry, or None."""
    if hasattr(event, "step_output_event"):
        return event.step_output_event.get("error")
    return None


# Convenience function for applications to send custom alerts
def send_alert(
    title: str,
    message: str,
    severity: str | None = "ERROR",
    asset_name: str | None = None,
    run_id: str | None = None,
    error_message: str | None = None,
) -> bool:
    """Send a custom alert and return True when delivery succeeds."""
    return _load_alert_sink().send_alert(
        title=title,
        message=message,
        severity=severity,
        asset_name=asset_name,
        run_id=run_id,
        error_message=error_message,
    )
