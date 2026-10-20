"""Environment-bound WAP must never borrow another run's backend or target."""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock

import dagster as dg
import pytest
from phlo.capabilities.resolver import ResolutionResult
from phlo.capabilities.specs import AssetSpec, RunSpec
from phlo.capabilities.support import CapabilitySupport
from phlo.config.project import load_project_model

from phlo_dagster import adapter, wap_context, wap_launch, wap_sensors


class Catalog:
    base_url = "http://nessie:19120"

    def __init__(self):
        self.refs = {"main": "production-0", "qa": "qa-0"}
        self.merges = []
        self.deletes = []
        self.unavailable = False

    def get_branch_hash(self, name):
        if self.unavailable:
            raise ConnectionError("disposable backend unavailable")
        return self.refs.get(name)

    def create_branch(self, name, from_ref="main"):
        self.refs[name] = self.refs[from_ref]
        return self.refs[name]

    def list_branches(self):
        return [
            SimpleNamespace(
                name=name, hash=value, created_at=datetime.now(timezone.utc) - timedelta(days=2)
            )
            for name, value in self.refs.items()
        ]

    def delete_branch(self, name, *, expected_hash=None):
        if expected_hash is not None and self.refs.get(name) != expected_hash:
            return False
        self.deletes.append(name)
        return self.refs.pop(name, None) is not None

    def merge_branch(
        self,
        source,
        target="main",
        *,
        message=None,
        expected_target_hash=None,
        expected_source_hash=None,
    ):
        if self.refs[target] != expected_target_hash:
            return False
        self.merges.append((source, target))
        self.refs[target] = expected_source_hash
        return True


@pytest.fixture
def environment(monkeypatch, tmp_path):
    monkeypatch.setenv("PHLO_PROJECT_PATH", str(tmp_path))
    monkeypatch.setenv("PHLO_PROJECT", "warehouse")
    monkeypatch.delenv("PHLO_V1_ENVIRONMENTS", raising=False)
    config = tmp_path / "phlo.yaml"
    config.write_text(
        """name: warehouse
default_environment: stage
environments:
  prod:
    dagster_location: production-code
    nessie_ref: main
  stage:
    dagster_location: warehouse-code
    nessie_ref: qa
wap:
  enabled: true
  repository_location_name: warehouse-code
""",
        encoding="utf-8",
    )
    catalog = Catalog()
    resolution = ResolutionResult(
        "catalog",
        "nessie",
        catalog,
        {},
        CapabilitySupport(supports_refs=True, supports_promote=True),
    )

    def resolve(kind, *args, **kwargs):
        return resolution if kind == "catalog" else None

    for module in (wap_launch, wap_context, wap_sensors):
        monkeypatch.setattr(module, "resolve_capability", resolve)
    monkeypatch.setattr(wap_sensors, "_quality_evidence", lambda *a, **k: ("quality-id", {}))
    monkeypatch.setattr(wap_sensors, "_reconcile_promoted_wap_run", lambda *a: True)
    observations = []
    monkeypatch.setattr(wap_sensors, "_emit_wap_observation", lambda **kw: observations.append(kw))
    return SimpleNamespace(catalog=catalog, config=config, observations=observations)


def run_for(launch, physical="physical-1", location="warehouse-code"):
    return SimpleNamespace(
        run_id=physical,
        tags=dict(launch.tags),
        status=dg.DagsterRunStatus.SUCCESS,
        remote_job_origin=SimpleNamespace(
            repository_origin=SimpleNamespace(
                code_location_origin=SimpleNamespace(location_name=location)
            )
        ),
    )


def sensor_context(runs):
    instance = MagicMock()
    instance.get_runs.return_value = runs
    instance.get_records_for_run.return_value = SimpleNamespace(records=[])
    return MagicMock(instance=instance, cursor=None)


def test_non_main_publication_and_acknowledged_recovery(environment, monkeypatch):
    launch = wap_launch.prepare_wap_launch(logical_run_id="non-main")
    assert environment.catalog.refs[launch.branch] == "qa-0"
    run = run_for(launch)
    launch.record_launch_result(status="launched", dagster_run_id=run.run_id)
    wap_launch.verify_wap_execution(run)
    environment.catalog.refs[launch.branch] = "audited-rows"
    reconciled = iter([False, True])
    monkeypatch.setattr(wap_sensors, "_reconcile_promoted_wap_run", lambda *a: next(reconciled))
    context = sensor_context([run])
    wap_sensors.wap_auto_promotion_sensor._raw_fn(context)
    assert environment.catalog.refs == {"main": "production-0", "qa": "audited-rows"}
    assert wap_launch.read_wap_report("non-main")["merge_state"] == "merged"
    environment.catalog.refs["qa"] = "later-publication"
    wap_sensors.wap_auto_promotion_sensor._raw_fn(context)
    assert environment.catalog.merges == [(launch.branch, "qa")]
    assert environment.catalog.deletes == [launch.branch]
    report = wap_launch.read_wap_report("non-main")
    assert report["status"] == "promoted"
    assert report["context"]["environment"] == "stage"
    assert report["target_branch"] == "qa"
    assert report["target_hash_after"] == "audited-rows"
    assert [
        row["catalog_ref"] for row in environment.observations if row["operation"] == "promotion"
    ] == ["qa"]


@pytest.mark.parametrize("foreign_first", [True, False])
def test_execution_waits_for_launcher_not_first_copied_tag_run(environment, foreign_first):
    launch = wap_launch.prepare_wap_launch(logical_run_id="copy-race")
    intended = run_for(launch)
    foreign = run_for(launch, physical="copied-physical")
    # Both workers may start before the trusted response is registered.
    with ThreadPoolExecutor(max_workers=2) as pool:
        first, second = (foreign, intended) if foreign_first else (intended, foreign)
        futures = {
            run.run_id: pool.submit(wap_launch.verify_wap_execution, run) for run in (first, second)
        }
        launch.record_launch_result(status="launched", dagster_run_id=intended.run_id)
        assert futures[intended.run_id].result(timeout=5) is None
        with pytest.raises(Exception, match="physical run"):
            futures[foreign.run_id].result(timeout=5)
    # A restarted worker of the registered run needs no ephemeral claim state.
    wap_launch.verify_wap_execution(intended)
    assert environment.catalog.merges == []
    assert environment.catalog.deletes == []


def test_lost_launch_response_blocks_execution_then_explicit_registration_recovers(
    environment, monkeypatch
):
    launch = wap_launch.prepare_wap_launch(logical_run_id="lost-response")
    run = run_for(launch)
    with monkeypatch.context() as clock:
        ticks = iter([0, 31])
        clock.setattr(wap_launch, "time", SimpleNamespace(monotonic=lambda: next(ticks)))
        with pytest.raises(Exception, match="registration unavailable"):
            wap_launch.verify_wap_execution(run)
    assert launch.branch in environment.catalog.refs
    launch.record_launch_result(status="launched", dagster_run_id=run.run_id)
    wap_launch.verify_wap_execution(run)


@pytest.mark.parametrize(
    "mutation", ["environment", "ref", "backend", "location", "tag", "override", "manifest"]
)
def test_drift_and_tampering_block_execution_promotion_and_retention(environment, mutation):
    launch = wap_launch.prepare_wap_launch(logical_run_id="bound-run")
    run = run_for(launch)
    launch.record_launch_result(status="launched", dagster_run_id=run.run_id)
    if mutation == "environment":
        text = (
            environment.config.read_text()
            .replace("default_environment: stage", "default_environment: prod")
            .replace("  stage:", "  renamed:")
        )
        environment.config.write_text(text)
        load_project_model.cache_clear()
    elif mutation == "ref":
        environment.config.write_text(
            environment.config.read_text().replace("nessie_ref: qa", "nessie_ref: redirected")
        )
        load_project_model.cache_clear()
    elif mutation == "backend":
        environment.catalog.base_url = "http://other-backend:19120"
    elif mutation == "location":
        run = run_for(launch, location="foreign-code")
    elif mutation == "tag":
        run.tags["phlo/ref"] = "main"
    elif mutation == "override":
        run.tags["phlo/capability/catalog"] = "other-provider"
    else:
        wap_launch.write_wap_report(
            "bound-run", context={**launch.context.to_dict(), "target_ref": "main"}
        )
    with pytest.raises(Exception):
        wap_launch.verify_wap_execution(run)
    context = sensor_context([run])
    wap_sensors.wap_auto_promotion_sensor._raw_fn(context)
    wap_sensors.wap_branch_cleanup_sensor._raw_fn(context)
    assert environment.catalog.refs == {"main": "production-0", "qa": "qa-0", launch.branch: "qa-0"}
    assert environment.catalog.merges == environment.catalog.deletes == []


def test_target_hash_drift_is_not_backend_outage(environment):
    launch = wap_launch.prepare_wap_launch(logical_run_id="target-drift")
    run = run_for(launch)
    launch.record_launch_result(status="launched", dagster_run_id=run.run_id)
    context = sensor_context([run])
    environment.catalog.unavailable = True
    wap_sensors.wap_auto_promotion_sensor._raw_fn(context)
    assert (
        wap_launch.read_wap_report("target-drift")["failure_reason"] == "catalog_hash_unavailable"
    )
    environment.catalog.unavailable = False
    environment.catalog.refs["qa"] = "other-publisher"
    wap_sensors.wap_auto_promotion_sensor._raw_fn(context)
    assert wap_launch.read_wap_report("target-drift")["failure_reason"] == "target_hash_drift"
    assert environment.catalog.merges == environment.catalog.deletes == []


def test_default_change_does_not_redirect_existing_launch(environment):
    launch = wap_launch.prepare_wap_launch(logical_run_id="default-pinned")
    run = run_for(launch)
    launch.record_launch_result(status="launched", dagster_run_id=run.run_id)
    environment.config.write_text(
        environment.config.read_text().replace(
            "default_environment: stage", "default_environment: prod"
        )
    )
    load_project_model.cache_clear()
    wap_launch.verify_wap_execution(run)
    wap_sensors.wap_auto_promotion_sensor._raw_fn(sensor_context([run]))
    assert environment.catalog.merges == [(launch.branch, "qa")]


@pytest.mark.parametrize(
    "selected,message", [("unknown", "Unknown WAP environment"), ("prod", "cross-location")]
)
def test_unknown_or_cross_location_selection_never_creates_branch(environment, selected, message):
    with pytest.raises(ValueError, match=message):
        wap_launch.prepare_wap_launch(logical_run_id="not-created", environment=selected)
    assert environment.catalog.refs == {"main": "production-0", "qa": "qa-0"}


def test_credential_rotation_is_not_backend_drift(environment):
    environment.catalog.base_url = "http://user:old-password@NESSIE:19120/"
    launch = wap_launch.prepare_wap_launch(logical_run_id="rotation")
    run = run_for(launch)
    launch.record_launch_result(status="launched", dagster_run_id=run.run_id)
    environment.catalog.base_url = "http://user:new-password@nessie:19120"
    wap_launch.verify_wap_execution(run)
    report = str(wap_launch.read_wap_report("rotation"))
    assert "password" not in report
    assert "http://" not in report


def test_legacy_main_manifest_is_not_auto_stamped_or_published(environment):
    launch = wap_launch.WapLaunch(
        "legacy",
        "pipeline-run-legacy",
        environment.catalog,
        True,
        "production-0",
        "production-0",
        "warehouse",
        1,
    )
    environment.catalog.refs[launch.branch] = "production-0"
    launch.record_launch_result(status="launched", dagster_run_id="legacy-physical")
    run = run_for(launch, physical="legacy-physical")
    with pytest.raises(Exception, match="legacy_launch_context_missing"):
        wap_launch.verify_wap_execution(run)
    context = sensor_context([run])
    wap_sensors.wap_auto_promotion_sensor._raw_fn(context)
    wap_sensors.wap_branch_cleanup_sensor._raw_fn(context)
    report = wap_launch.read_wap_report("legacy")
    assert report["status"] == "promotion_blocked"
    assert "legacy_launch_context_missing" in report["context_error"]
    assert "context" not in report
    assert launch.branch in environment.catalog.refs
    assert environment.catalog.merges == environment.catalog.deletes == []


def test_asset_function_cannot_write_before_physical_registration(environment):
    launch = wap_launch.prepare_wap_launch(logical_run_id="execution-gate")
    launch.record_launch_result(status="launched", dagster_run_id="trusted-physical")
    writes = []
    spec = AssetSpec(
        key="raw.orders",
        group=None,
        description=None,
        run=RunSpec(fn=lambda runtime: writes.append("wrote")),
    )
    context = SimpleNamespace(run=run_for(launch, physical="copied-physical"))
    with pytest.raises(Exception, match="physical run"):
        list(adapter._execute_asset(context, spec))
    assert writes == []


@pytest.mark.parametrize("tag", ["phlo/ref", "phlo/wap_branch"])
def test_canonical_tag_conflict_blocks_public_execution_and_sensors(environment, monkeypatch, tag):
    launch = wap_launch.prepare_wap_launch(logical_run_id="canonical-conflict")
    run = run_for(launch)
    launch.record_launch_result(status="launched", dagster_run_id=run.run_id)
    run.tags[tag] = "main"
    writes = []
    definitions = adapter.DagsterOrchestratorAdapter().build_definitions(
        assets=[
            AssetSpec(
                key="raw.orders",
                group=None,
                description=None,
                run=RunSpec(fn=lambda runtime: writes.append("wrote")),
            )
        ],
        checks=[],
        resources=[],
    )
    with dg.DagsterInstance.ephemeral() as instance:
        result = dg.materialize(
            list(definitions.assets), instance=instance, tags=run.tags, raise_on_error=False
        )
        assert not result.success
        assert "WAP execution binding or capability routing is invalid" in (
            result.failure_data_for_node("raw__orders").error.to_string()
        )
        assert writes == []
        # Present the registered successful physical run to both sensor APIs;
        # rejection must not depend on the failed execution's status or ID.
        monkeypatch.setattr(instance, "get_runs", lambda **kwargs: [run])
        for sensor in (
            wap_sensors.wap_auto_promotion_sensor,
            wap_sensors.wap_branch_cleanup_sensor,
        ):
            with dg.build_sensor_context(instance=instance) as context:
                sensor.evaluate_tick(context)
    assert environment.catalog.refs == {"main": "production-0", "qa": "qa-0", launch.branch: "qa-0"}
    assert environment.catalog.merges == environment.catalog.deletes == []


def test_foreign_run_failure_does_not_stop_supported_publication(environment):
    foreign_launch = wap_launch.prepare_wap_launch(logical_run_id="foreign")
    foreign = run_for(foreign_launch, physical="foreign-physical", location="foreign-code")
    foreign_launch.record_launch_result(status="launched", dagster_run_id=foreign.run_id)
    local_launch = wap_launch.prepare_wap_launch(logical_run_id="local")
    local = run_for(local_launch)
    local_launch.record_launch_result(status="launched", dagster_run_id=local.run_id)
    environment.catalog.refs[local_launch.branch] = "local-rows"
    context = sensor_context([foreign, local])
    wap_sensors.wap_auto_promotion_sensor._raw_fn(context)
    wap_sensors.wap_branch_cleanup_sensor._raw_fn(context)
    assert environment.catalog.refs == {
        "main": "production-0",
        "qa": "local-rows",
        foreign_launch.branch: "qa-0",
    }
    assert environment.catalog.merges == [(local_launch.branch, "qa")]
    assert environment.catalog.deletes == [local_launch.branch]


def test_unknown_publication_response_requires_receipt_even_when_hash_matches(
    environment, monkeypatch
):
    launch = wap_launch.prepare_wap_launch(logical_run_id="unknown-publication")
    run = run_for(launch)
    launch.record_launch_result(status="launched", dagster_run_id=run.run_id)
    environment.catalog.refs[launch.branch] = "audited-rows"
    publish = environment.catalog.merge_branch

    def lose_response(*args, **kwargs):
        assert publish(*args, **kwargs)
        raise ConnectionError("response lost after conditional assignment")

    monkeypatch.setattr(environment.catalog, "merge_branch", lose_response)
    context = sensor_context([run])
    wap_sensors.wap_auto_promotion_sensor._raw_fn(context)
    assert environment.catalog.refs["qa"] == "audited-rows"
    wap_sensors.wap_auto_promotion_sensor._raw_fn(context)
    report = wap_launch.read_wap_report(launch.logical_run_id)
    assert report["failure_reason"] == "merge_outcome_unknown"
    assert environment.catalog.merges == [(launch.branch, "qa")]
    assert environment.catalog.deletes == []
    # Model an independently reviewed receipt, not automatic inference from HEAD.
    wap_launch.write_wap_report(
        launch.logical_run_id,
        merge_state="merged",
        target_hash_after="audited-rows",
    )
    wap_sensors.wap_auto_promotion_sensor._raw_fn(context)
    assert wap_launch.read_wap_report(launch.logical_run_id)["status"] == "promoted"
    assert environment.catalog.merges == [(launch.branch, "qa")]
    assert environment.catalog.deletes == [launch.branch]


def test_configured_main_still_works_with_new_binding(environment):
    environment.config.write_text(
        environment.config.read_text().replace(
            "repository_location_name: warehouse-code", "repository_location_name: production-code"
        )
    )
    load_project_model.cache_clear()
    launch = wap_launch.prepare_wap_launch(logical_run_id="bound-main", environment="prod")
    run = run_for(launch, location="production-code")
    launch.record_launch_result(status="launched", dagster_run_id=run.run_id)
    wap_launch.verify_wap_execution(run)
    environment.catalog.refs[launch.branch] = "production-rows"
    wap_sensors.wap_auto_promotion_sensor._raw_fn(sensor_context([run]))
    assert environment.catalog.refs == {"main": "production-rows", "qa": "qa-0"}


def test_process_writer_backend_mismatch_cannot_create_a_branch(environment, monkeypatch):
    catalog_resolution = wap_context.resolve_capability("catalog")
    writer = SimpleNamespace(wap_backend_identity=lambda: {"uri": "http://foreign/iceberg/main"})
    monkeypatch.setattr(
        wap_context,
        "resolve_capability",
        lambda kind: (
            catalog_resolution
            if kind == "catalog"
            else SimpleNamespace(name="iceberg", provider=writer)
            if kind == "table_store"
            else None
        ),
    )
    with pytest.raises(ValueError, match="different backend endpoints"):
        wap_launch.prepare_wap_launch(logical_run_id="wrong-writer")
    assert environment.catalog.refs == {"main": "production-0", "qa": "qa-0"}


def test_changed_source_is_not_deleted_after_pinned_publication(environment, monkeypatch):
    launch = wap_launch.prepare_wap_launch(logical_run_id="changed-source")
    run = run_for(launch)
    launch.record_launch_result(status="launched", dagster_run_id=run.run_id)
    environment.catalog.refs[launch.branch] = "audited-rows"
    publish = environment.catalog.merge_branch

    def move_source(*args, **kwargs):
        assert publish(*args, **kwargs)
        environment.catalog.refs[launch.branch] = "unreviewed-new-head"
        return True

    monkeypatch.setattr(environment.catalog, "merge_branch", move_source)
    context = sensor_context([run])
    wap_sensors.wap_auto_promotion_sensor._raw_fn(context)
    wap_sensors.wap_branch_cleanup_sensor._raw_fn(context)
    assert environment.catalog.refs == {
        "main": "production-0",
        "qa": "audited-rows",
        launch.branch: "unreviewed-new-head",
    }
    assert environment.catalog.deletes == []
