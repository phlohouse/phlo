"""Tests for maintenance telemetry aggregation.

Completed maintenance log events aggregate into a latest-per-operation
status snapshot; telemetry.metric run events render as Prometheus
counters labelled by operation, namespace, ref, status, and dry_run.
Both views are fed from hand-written JSONL telemetry files.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from phlo.capabilities.maintenance import load_maintenance_status, render_maintenance_prometheus


def _write_events(path: Path, events: list[dict]) -> None:
    with path.open("w", encoding="utf-8") as handle:
        for event in events:
            handle.write(json.dumps(event) + "\n")


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
    assert "phlo_iceberg_maintenance_runs_total" in output
    assert 'operation="cleanup_orphan_files"' in output
