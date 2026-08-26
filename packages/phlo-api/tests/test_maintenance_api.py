"""Tests for maintenance API capability resolution.

Registers a fake maintenance read-model provider with distinctive payloads so
the assertions pin that status snapshots and metrics are mapped faithfully
from the resolved capability rather than echoed back.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from phlo.capabilities import (
    MaintenanceReadModelSpec,
    clear_all_capabilities,
    register_capability,
)
from phlo_api.api import maintenance


class _OptimizeOperation:
    # Distinctive per-field values so a dropped or mangled mapping fails loudly.
    operation = "optimize"
    namespace = "raw"
    ref = "main"
    status = "success"
    completed_at = datetime(2026, 3, 7, 12, 0, tzinfo=timezone.utc)
    duration_seconds = 12.5
    tables_processed = 42
    errors = 0
    snapshots_deleted = 7
    orphan_files = 3
    total_records = 120000
    total_size_mb = 2048.5
    dry_run = False
    run_id = "run-optimize-77"
    job_name = "optimize_tables_job"


class _ExpireOperation:
    operation = "expire_snapshots"
    namespace = "marts"
    ref = "release-2026.03"
    status = "failed"
    completed_at = datetime(2026, 3, 8, 6, 30, tzinfo=timezone.utc)
    duration_seconds = 301.25
    tables_processed = 9
    errors = 2
    snapshots_deleted = 15
    orphan_files = 0
    total_records = 5400
    total_size_mb = 96.75
    dry_run = True
    run_id = "run-expire-12"
    job_name = "expire_snapshots_job"


class _Snapshot:
    last_updated = datetime(2026, 3, 9, 8, 45, tzinfo=timezone.utc)
    operations = [_OptimizeOperation(), _ExpireOperation()]


_METRICS_PAYLOAD = (
    "# HELP phlo_maintenance_operations_total Total maintenance operations.\n"
    "# TYPE phlo_maintenance_operations_total counter\n"
    'phlo_maintenance_operations_total{namespace="marts"} 2\n'
)


class _ReadModel:
    def load_maintenance_status(self):
        return _Snapshot()

    def render_maintenance_prometheus(self) -> str:
        return _METRICS_PAYLOAD


class _ExplodingReadModel:
    """Read model whose every callable raises, to prove handlers fail soft."""

    def load_maintenance_status(self):
        raise RuntimeError("read model unavailable")

    def render_maintenance_prometheus(self):
        raise RuntimeError("read model unavailable")


def test_get_maintenance_status_maps_the_full_snapshot(monkeypatch) -> None:
    monkeypatch.setattr(maintenance, "_resolve_maintenance_read_model", lambda: _ReadModel())

    payload = maintenance.get_maintenance_status()

    assert isinstance(payload, maintenance.MaintenanceStatusSnapshot)
    assert payload.model_dump() == {
        "last_updated": "2026-03-09T08:45:00+00:00",
        "operations": [
            {
                "operation": "optimize",
                "namespace": "raw",
                "ref": "main",
                "status": "success",
                "completed_at": "2026-03-07T12:00:00+00:00",
                "duration_seconds": 12.5,
                "tables_processed": 42,
                "errors": 0,
                "snapshots_deleted": 7,
                "orphan_files": 3,
                "total_records": 120000,
                "total_size_mb": 2048.5,
                "dry_run": False,
                "run_id": "run-optimize-77",
                "job_name": "optimize_tables_job",
            },
            {
                "operation": "expire_snapshots",
                "namespace": "marts",
                "ref": "release-2026.03",
                "status": "failed",
                "completed_at": "2026-03-08T06:30:00+00:00",
                "duration_seconds": 301.25,
                "tables_processed": 9,
                "errors": 2,
                "snapshots_deleted": 15,
                "orphan_files": 0,
                "total_records": 5400,
                "total_size_mb": 96.75,
                "dry_run": True,
                "run_id": "run-expire-12",
                "job_name": "expire_snapshots_job",
            },
        ],
    }


def test_get_maintenance_metrics_returns_provider_payload_verbatim(monkeypatch) -> None:
    monkeypatch.setattr(maintenance, "_resolve_maintenance_read_model", lambda: _ReadModel())

    response = maintenance.get_maintenance_metrics()

    assert response.status_code == 200
    assert response.body.decode() == _METRICS_PAYLOAD


def test_get_maintenance_status_fails_soft_when_the_read_model_raises(monkeypatch) -> None:
    monkeypatch.setattr(
        maintenance, "_resolve_maintenance_read_model", lambda: _ExplodingReadModel()
    )

    payload = maintenance.get_maintenance_status()

    assert payload == {"error": "read model unavailable"}


def test_get_maintenance_metrics_fails_soft_when_the_read_model_raises(monkeypatch) -> None:
    monkeypatch.setattr(
        maintenance, "_resolve_maintenance_read_model", lambda: _ExplodingReadModel()
    )

    response = maintenance.get_maintenance_metrics()

    assert response.status_code == 500
    assert response.body.decode() == "# error: read model unavailable\n"


def test_resolve_maintenance_read_model_uses_env_selection(monkeypatch) -> None:
    """Environment selection should pick one provider among many."""
    clear_all_capabilities()
    monkeypatch.setattr(maintenance, "discover_capabilities", lambda: None)
    monkeypatch.setenv("PHLO_MAINTENANCE_READ_MODEL", "custom")
    register_capability(
        "maintenance_read_model", MaintenanceReadModelSpec(name="default", provider=object())
    )
    register_capability(
        "maintenance_read_model", MaintenanceReadModelSpec(name="custom", provider=_ReadModel())
    )

    resolved = maintenance._resolve_maintenance_read_model()

    assert isinstance(resolved, _ReadModel)
    clear_all_capabilities()


def test_resolve_maintenance_read_model_requires_selection_when_ambiguous(monkeypatch) -> None:
    """Ambiguous maintenance providers should fail with deterministic guidance."""
    clear_all_capabilities()
    monkeypatch.setattr(maintenance, "discover_capabilities", lambda: None)
    monkeypatch.delenv("PHLO_MAINTENANCE_READ_MODEL", raising=False)
    register_capability(
        "maintenance_read_model", MaintenanceReadModelSpec(name="default", provider=object())
    )
    register_capability(
        "maintenance_read_model", MaintenanceReadModelSpec(name="custom", provider=object())
    )

    with pytest.raises(RuntimeError, match="Multiple maintenance_read_model providers"):
        maintenance._resolve_maintenance_read_model()

    clear_all_capabilities()
