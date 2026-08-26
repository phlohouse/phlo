"""Tests for maintenance telemetry aggregation.

Completed maintenance log events aggregate into a latest-per-operation
status snapshot; telemetry.metric run events render as Prometheus
counters labelled by operation, namespace, ref, status, and dry_run.
Both views are fed from hand-written JSONL telemetry files.
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from pathlib import Path

from phlo.capabilities.maintenance import load_maintenance_status, render_maintenance_prometheus


def _write_events(path: Path, events: list[dict]) -> None:
    with path.open("w", encoding="utf-8") as handle:
        for event in events:
            handle.write(json.dumps(event) + "\n")


_PROMETHEUS_SAMPLE_RE = re.compile(
    r"^(?P<name>[a-zA-Z_:][a-zA-Z0-9_:]*)(?:\{(?P<labels>[^}]*)\})?\s+(?P<value>\S+)$"
)
_PROMETHEUS_LABEL_RE = re.compile(r'(?P<key>[a-zA-Z_][a-zA-Z0-9_]*)="(?P<value>[^"]*)"')


def _parse_exposition_samples(output: str) -> list[tuple[str, dict[str, str], str]]:
    """Parse metric samples from Prometheus text exposition, skipping HELP/TYPE lines."""
    samples: list[tuple[str, dict[str, str], str]] = []
    for line in output.splitlines():
        if not line or line.startswith("#"):
            continue
        match = _PROMETHEUS_SAMPLE_RE.match(line)
        assert match is not None, f"unparsable exposition line: {line!r}"
        labels = {
            label["key"]: label["value"]
            for label in _PROMETHEUS_LABEL_RE.finditer(match["labels"] or "")
        }
        samples.append((match["name"], labels, match["value"]))
    return samples


def test_load_maintenance_status(tmp_path: Path) -> None:
    timestamp = datetime(2025, 12, 30, tzinfo=UTC).isoformat()
    events = [
        {
            "event_type": "telemetry.log",
            "name": "iceberg.maintenance.complete",
            "timestamp": timestamp,
            "tags": {
                "maintenance": "true",
                "operation": "expire_snapshots",
                "namespace": "raw",
                "ref": "main",
            },
            "payload": {
                "status": "success",
                "duration_seconds": 12.5,
                "tables_processed": 3,
                "snapshots_deleted": 9,
                "errors": 0,
                "run_id": "run-123",
                "job_name": "iceberg_maintenance_job",
            },
        }
    ]
    path = tmp_path / "events.jsonl"
    _write_events(path, events)

    snapshot = load_maintenance_status(path)
    assert snapshot.operations
    op = snapshot.operations[0]
    assert op.operation == "expire_snapshots"
    assert op.namespace == "raw"
    assert op.ref == "main"
    assert op.status == "success"
    assert op.tables_processed == 3
    assert op.snapshots_deleted == 9
    assert op.errors == 0
    assert op.run_id == "run-123"


def test_render_maintenance_prometheus(tmp_path: Path) -> None:
    timestamp = datetime(2025, 12, 30, tzinfo=UTC).isoformat()
    events = [
        {
            "event_type": "telemetry.metric",
            "name": "iceberg.maintenance.run",
            "timestamp": timestamp,
            "value": 1,
            "tags": {
                "maintenance": "true",
                "operation": "cleanup_orphan_files",
                "namespace": "raw",
                "ref": "main",
                "status": "success",
                "dry_run": "true",
            },
        }
    ]
    path = tmp_path / "events.jsonl"
    _write_events(path, events)

    output = render_maintenance_prometheus(path)
    assert _parse_exposition_samples(output) == [
        (
            "phlo_iceberg_maintenance_runs_total",
            {
                "operation": "cleanup_orphan_files",
                "namespace": "raw",
                "ref": "main",
                "status": "success",
                "dry_run": "true",
            },
            "1.0",
        )
    ]


def test_load_maintenance_status_supersedes_older_operation_records(tmp_path: Path) -> None:
    older_timestamp = datetime(2025, 12, 29, tzinfo=UTC).isoformat()
    newer_timestamp = datetime(2025, 12, 30, tzinfo=UTC).isoformat()

    def complete_event(timestamp: str, status: str, run_id: str) -> dict:
        return {
            "event_type": "telemetry.log",
            "name": "iceberg.maintenance.complete",
            "timestamp": timestamp,
            "tags": {
                "maintenance": "true",
                "operation": "expire_snapshots",
                "namespace": "raw",
                "ref": "main",
            },
            "payload": {
                "status": status,
                "duration_seconds": 10.0,
                "tables_processed": 5,
                "snapshots_deleted": 7,
                "errors": 0,
                "run_id": run_id,
                "job_name": "iceberg_maintenance_job",
            },
        }

    path = tmp_path / "events.jsonl"
    _write_events(
        path,
        [
            complete_event(newer_timestamp, "success", "run-456"),
            complete_event(older_timestamp, "failed", "run-123"),
        ],
    )

    snapshot = load_maintenance_status(path)
    assert len(snapshot.operations) == 1
    op = snapshot.operations[0]
    assert op.operation == "expire_snapshots"
    assert op.namespace == "raw"
    assert op.ref == "main"
    assert op.status == "success"
    assert op.run_id == "run-456"
    assert op.tables_processed == 5
    assert op.errors == 0
    assert op.completed_at == datetime.fromisoformat(newer_timestamp)
    assert snapshot.last_updated == datetime.fromisoformat(newer_timestamp)


def test_render_maintenance_prometheus_skips_malformed_records(tmp_path: Path) -> None:
    timestamp = datetime(2025, 12, 30, tzinfo=UTC).isoformat()

    def metric_event(name: str, value) -> dict:
        return {
            "event_type": "telemetry.metric",
            "name": name,
            "timestamp": timestamp,
            "value": value,
            "tags": {
                "maintenance": "true",
                "operation": "cleanup_orphan_files",
                "namespace": "raw",
                "ref": "main",
                "status": "success",
                "dry_run": "false",
            },
        }

    path = tmp_path / "events.jsonl"
    _write_events(
        path,
        [
            {"event_type": "telemetry.log", "name": "iceberg.maintenance.complete"},
            metric_event("iceberg.maintenance.not_a_known_metric", 1),
            metric_event("iceberg.maintenance.run", "not-a-number"),
            metric_event("iceberg.maintenance.run", 1),
        ],
    )
    with path.open("a", encoding="utf-8") as handle:
        handle.write("{not valid json\n")

    output = render_maintenance_prometheus(path)
    assert _parse_exposition_samples(output) == [
        (
            "phlo_iceberg_maintenance_runs_total",
            {
                "operation": "cleanup_orphan_files",
                "namespace": "raw",
                "ref": "main",
                "status": "success",
                "dry_run": "false",
            },
            "1.0",
        )
    ]
