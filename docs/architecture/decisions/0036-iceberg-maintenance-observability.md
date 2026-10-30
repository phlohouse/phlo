# ADR 0036: Iceberg Maintenance Observability

## Status

**Proposed**

## Context

Iceberg maintenance jobs live in `packages/phlo-iceberg/src/phlo_iceberg/maintenance.py` and run
snapshot expiration, orphan file cleanup, and table statistics collection on a schedule. Today they
emit human-readable log lines and return summary dicts, but they do not provide:

- Structured log fields for correlation (run, job, namespace, table, ref).
- Consistent metrics for durations, counts, and errors.
- Alerting when maintenance jobs fail.
- A surfaced maintenance status view in Observatory.

The release plan calls for end-to-end observability of maintenance operations across logging,
metrics, alerting, and UI.

## Decision

Implement first-class observability for Iceberg maintenance operations, aligned with ADR 0028
(structured logging) and the hook-based telemetry pipeline.

1. **Structured logging**: emit start/end logs for each maintenance operation and per-table actions
   with `extra` fields: `maintenance_op`, `namespace`, `table_name`, `ref`, `dry_run`, `run_id`,
   `job_name`.
2. **Telemetry events**: use `TelemetryEventEmitter` to emit `telemetry.metric` and
   `telemetry.log` events with tags: `maintenance=true`, `operation`, `namespace`, `ref`, and
   optional `table` for log-level detail.
3. **Prometheus metrics**: aggregate telemetry into Prometheus counters/gauges/histograms using
   core maintenance telemetry aggregation. Prometheus labels stay low-cardinality (operation, namespace, ref, status),
   while table-level detail remains in logs/telemetry events.
4. **Alerting**: on maintenance job failure or non-zero error counts, send alerts via
   `phlo-alerting` with run/job context.
5. **Observatory status**: expose last maintenance run status, duration, and key counters in the
   Observatory UI from metrics/telemetry data.

## Implementation

- Add maintenance telemetry and structured logging to `phlo_dagster.iceberg_maintenance` for
  `expire_table_snapshots`, `cleanup_orphan_files`, and `collect_table_stats`.
- Extend core maintenance telemetry aggregation into Prometheus-compatible metrics.
- Trigger alerts through `phlo-alerting` when maintenance errors occur.
- Surface maintenance status in Observatory using the metrics API.

## Consequences

### Positive

- Clear visibility into maintenance health, duration, and impact.
- Faster detection of failed or risky maintenance runs.
- Consistent observability pattern across Phlo operations.

### Negative

- Additional log volume and telemetry storage.
- Metrics aggregation work required in core maintenance telemetry code.

## Verification

- Run a maintenance job and confirm structured logs include maintenance fields.
- Verify Prometheus exposes maintenance metrics with low-cardinality labels.
- Trigger a failure and confirm an alert is sent and shown in Observatory.
