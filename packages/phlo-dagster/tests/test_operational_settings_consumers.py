"""Exercise real maintenance/alert consumers on isolated catalog data."""

import json
from datetime import UTC, datetime
from types import SimpleNamespace

import dagster as dg
import pyarrow as pa
import pytest
from pyiceberg.catalog.sql import SqlCatalog
from pyiceberg.partitioning import PartitionField, PartitionSpec
from pyiceberg.schema import Schema
from pyiceberg.transforms import IdentityTransform
from pyiceberg.types import LongType, NestedField

from phlo.plugins import observatory_settings as storage
from phlo_dagster import alerting_sensor, maintenance_sensor, iceberg_maintenance_utils
from phlo_iceberg import resource, tables
from phlo_api.observatory_api.settings import get_operational_maintenance_windows


@pytest.fixture
def configuration(monkeypatch, tmp_path):
    monkeypatch.setenv("PHLO_OBSERVATORY_SETTINGS_BACKEND", "memory")
    monkeypatch.setenv("PHLO_OBSERVATORY_ENVIRONMENT", "prod")
    monkeypatch.setenv(
        "PHLO_V1_ENVIRONMENTS",
        json.dumps(
            {
                "prod": {"dagster_location": "production", "nessie_ref": "main"},
                "staging": {"dagster_location": "testing", "nessie_ref": "candidate"},
            }
        ),
    )
    policy = tmp_path / "maintenance.yaml"
    policy.write_text(
        "policies:\n  - namespace: raw\n    ref: main\n  - namespace: not_owned\n    ref: candidate\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("PHLO_MAINTENANCE_POLICY_PATH", str(policy))
    storage._reset_memory_service()

    def save(**values):
        storage.get_settings_service().put(
            storage.SettingsScope.GLOBAL,
            storage.ADMIN_SETTINGS_NAMESPACE,
            {
                "version": 4,
                "values": {f"observatory.settings.{name}": value for name, value in values.items()},
            },
        )

    yield save
    storage._reset_memory_service()


def test_nightly_sensor_uses_disposable_iceberg_files_and_real_provider(
    configuration, tmp_path, monkeypatch
):
    catalog = SqlCatalog(
        "disposable",
        uri=f"sqlite:///{tmp_path}/catalog.db",
        warehouse=f"file://{tmp_path}/warehouse",
    )
    catalog.create_namespace("raw")
    schema = Schema(NestedField(1, "device", LongType(), required=False))
    spec = PartitionSpec(
        PartitionField(source_id=1, field_id=1000, transform=IdentityTransform(), name="device")
    )
    for name, count in (("eligible", 200), ("below_boundary", 199)):
        table = catalog.create_table(f"raw.{name}", schema=schema, partition_spec=spec)
        table.append(pa.table({"device": pa.array(range(count), type=pa.int64())}))
    monkeypatch.setattr(
        resource,
        "get_catalog",
        lambda ref: catalog if ref == "main" else pytest.fail("cross-environment catalog access"),
    )
    monkeypatch.setattr(
        tables,
        "get_catalog",
        lambda ref: catalog if ref == "main" else pytest.fail("cross-environment catalog access"),
    )
    provider = resource.IcebergResource(ref="main")
    monkeypatch.setattr(
        iceberg_maintenance_utils,
        "resolve_capability",
        lambda *args: SimpleNamespace(provider=provider),
    )
    monkeypatch.setattr(maintenance_sensor, "_load_optimize_table_store", lambda: provider)

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 10, 4, 3, 15, tzinfo=UTC)

    monkeypatch.setattr(maintenance_sensor, "datetime", Clock)
    configuration(
        **{
            "maintenance.compact_nightly": True,
            "maintenance.target_file_size_mb": "2",
            "maintenance.expire_snapshots_days": "7",
            "maintenance.orphan_cleanup": "Sundays 03:00",
            "maintenance.keep_tagged_snapshots": True,
        }
    )
    with dg.build_sensor_context() as context:
        requests = list(maintenance_sensor.maintenance_policy_sensor._raw_fn(context))
    optimized = [request for request in requests if request.job_name == "optimize_tables_job"]
    assert len(optimized) == 1
    assert optimized[0].run_config["ops"]["optimize_table_files"]["config"] == {
        "table_names": ["raw.eligible"],
        "ref": "main",
        "dry_run": True,
        "settings_revision": 4,
    }
    assert any(request.job_name == "orphan_cleanup_job" for request in requests)
    assert all(request.tags["phlo/ref"] == "main" for request in requests)
    before = catalog.load_table("raw.eligible").current_snapshot().snapshot_id
    result = maintenance_sensor.optimize_tables_job.execute_in_process(
        run_config=optimized[0].run_config
    )
    assert result.success
    evidence = result.output_for_node("optimize_table_files")["results"][0]
    assert evidence["status"] == "planned"
    assert evidence["before_revision"] == before
    assert evidence["planned"]["file_count"] == 200
    assert catalog.load_table("raw.eligible").current_snapshot().snapshot_id == before
    with dg.build_sensor_context() as context:
        again = list(maintenance_sensor.maintenance_policy_sensor._raw_fn(context))
    assert [request.run_key for request in requests] == [request.run_key for request in again]
    configuration(**{"maintenance.compact_nightly": False})
    with dg.build_sensor_context() as context:
        assert list(maintenance_sensor.maintenance_policy_sensor._raw_fn(context)) == []
    with pytest.raises(RuntimeError, match="Maintenance settings changed"):
        maintenance_sensor.optimize_tables_job.execute_in_process(
            run_config=optimized[0].run_config
        )


def test_digest_consumes_cadence_and_does_not_advance_on_failure(configuration, monkeypatch):
    configuration(**{"alerts.email_digest": "Weekdays at 08:00"})

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 10, 2, 8, 1, tzinfo=UTC)

    monkeypatch.setattr(alerting_sensor, "datetime", Clock)
    calls = []
    confirmed = False

    class Sink:
        def send_alert(self, **kwargs):
            calls.append(kwargs)
            return confirmed

    monkeypatch.setattr(alerting_sensor, "_load_alert_sink", lambda name: Sink())

    def origin(location):
        return SimpleNamespace(
            repository_origin=SimpleNamespace(
                code_location_origin=SimpleNamespace(location_name=location)
            )
        )

    def run(identity, location):
        return SimpleNamespace(
            run_id=identity, job_name="controlled", remote_job_origin=origin(location)
        )

    class Instance:
        def get_runs(self, filters):
            assert filters.tags == {"phlo/ref": "main"}
            return [run("included", "production"), run("excluded", "testing")]

    context = SimpleNamespace(instance=Instance(), cursor=None)
    context.update_cursor = lambda value: setattr(context, "cursor", value)
    with pytest.raises(RuntimeError, match="not confirmed"):
        alerting_sensor.email_digest_sensor._raw_fn(context)
    assert context.cursor is None
    confirmed = True
    alerting_sensor.email_digest_sensor._raw_fn(context)
    assert context.cursor == "2026-10-02T08:00:00+00:00"
    assert json.loads(calls[-1]["message"])["failed_runs"] == [
        {"run_id": "included", "job": "controlled"}
    ]
    assert isinstance(alerting_sensor.email_digest_sensor._raw_fn(context), dg.SkipReason)
    assert len(calls) == 2


def test_maintenance_annotations_require_binding_and_follow_durable_settings(
    configuration, monkeypatch
):
    configuration(
        **{"maintenance.compact_nightly": True, "maintenance.orphan_cleanup": "Sundays 03:00"}
    )
    windows = get_operational_maintenance_windows("prod")
    assert windows and all(item["id"].startswith("observatory:prod:") for item in windows)
    assert all(
        datetime.fromisoformat(item["ends_at"]) > datetime.fromisoformat(item["starts_at"])
        for item in windows
    )
    assert any("discovery only" in item["description"] for item in windows)
    assert get_operational_maintenance_windows("staging") is None
    monkeypatch.delenv("PHLO_OBSERVATORY_ENVIRONMENT")
    assert get_operational_maintenance_windows("prod") is None
    assert (
        storage.operational_schedule_slot(
            "Weekdays at 08:00", datetime(2026, 10, 3, 12, tzinfo=UTC)
        )
        is None
    )
    assert (
        storage.operational_schedule_slot(
            "Daily at 08:00", datetime(2026, 10, 2, 7, 59, tzinfo=UTC)
        )
        is None
    )
