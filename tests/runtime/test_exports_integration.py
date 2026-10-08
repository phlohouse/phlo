"""Exercise exports through the supported workflow discovery and Dagster adapter."""

import json
import sys
from pathlib import Path

import dagster as dg
import pytest
from phlo_dagster.framework.discovery import discover_user_workflows

from phlo.capabilities.registry import CAPABILITY_FAMILIES, get_capability_registry
from phlo.exports import resolve_export_manifest
from phlo.helpers.artifacts import verify_manifest_checksums


@pytest.fixture(autouse=True)
def restore_discovery_state():
    registry = get_capability_registry()
    capabilities = {family: registry.list(family) for family in CAPABILITY_FAMILIES}
    modules = {name: module for name, module in sys.modules.items() if name.startswith("workflows")}
    search_path = sys.path[:]
    yield
    registry.clear_all()
    for family, specs in capabilities.items():
        for spec in specs:
            registry.register(family, spec)
    for name in list(sys.modules):
        if name.startswith("workflows"):
            sys.modules.pop(name)
    sys.modules.update(modules)
    sys.path[:] = search_path


@pytest.mark.parametrize("failure", ["writer", "missing"])
def test_discovered_export_failure_and_retry(tmp_path, failure):
    workflows = tmp_path / "workflows"
    workflows.mkdir()
    destination = tmp_path / "exports"
    attempts_path = tmp_path / "attempts.jsonl"
    (workflows / "survey.py").write_text(
        f"""
import json
from pathlib import Path
import phlo
from dagster import asset

@asset
def observed_stars():
    return None

@phlo.export(
    name="survey", destination={str(destination)!r},
    depends_on=["observed_stars"],
    outputs={{"catalog": "catalog.csv", "summary": "summary.json"}},
    max_retries=1, retry_delay_seconds=1,
)
def survey(context):
    assert list(context.staging_dir.iterdir()) == []
    with Path({str(attempts_path)!r}).open("a", encoding="utf-8") as log:
        log.write(json.dumps({{"run_id": context.run_id, "staging": str(context.staging_dir)}}) + "\\n")
    context.upstream_versions["observed_stars"] = "snapshot-42"
    (context.staging_dir / "catalog.csv").write_text(context.run_id, encoding="utf-8")
    mode = context.runtime.tags.get("test/mode", "success")
    attempts = Path({str(attempts_path)!r}).read_text(encoding="utf-8").splitlines()
    same_run_attempts = sum(json.loads(line)["run_id"] == context.run_id for line in attempts)
    if mode == "fail" or (mode == "retry" and same_run_attempts == 1):
        if {failure!r} == "writer":
            raise RuntimeError("summary writer failed")
        return
    (context.staging_dir / "summary.json").write_text(context.run_id, encoding="utf-8")
""",
        encoding="utf-8",
    )
    definitions = discover_user_workflows(workflows, clear_registries=True)
    graph = definitions.resolve_asset_graph()
    assert dg.AssetKey("observed_stars") in graph.get(dg.AssetKey("survey")).parent_keys
    assets = [
        asset
        for asset in definitions.assets
        if isinstance(asset, dg.AssetsDefinition)
        and asset.keys & {dg.AssetKey("survey"), dg.AssetKey("observed_stars")}
    ]
    with dg.DagsterInstance.ephemeral() as instance:
        prior_result = dg.materialize(assets, instance=instance)
        assert prior_result.success
        prior = resolve_export_manifest(destination, "survey")
        failed = dg.materialize(
            assets, instance=instance, tags={"test/mode": "fail"}, raise_on_error=False
        )
        assert not failed.success
        assert failed.get_asset_materialization_events()  # upstream still succeeded
        assert not failed.asset_materializations_for_node("survey")
        assert resolve_export_manifest(destination, "survey") == prior
        result = dg.materialize(assets, instance=instance, tags={"test/mode": "retry"})
        assert result.success
    manifest = resolve_export_manifest(destination, "survey")
    assert manifest.metadata["run_id"] == result.run_id
    assert manifest.metadata["upstream_versions"] == {"observed_stars": "snapshot-42"}
    assert all(verify_manifest_checksums(manifest).values())
    assert [Path(entry.uri).read_text() for entry in manifest.artifacts] == [result.run_id] * 2
    event = result.asset_materializations_for_node("survey")[0]
    assert event.metadata["phlo/export_manifest"].value["metadata"] == manifest.metadata
    assert Path(event.metadata["phlo/export_manifest_path"].value).is_file()
    attempts = [json.loads(line) for line in attempts_path.read_text().splitlines()]
    retried = [attempt for attempt in attempts if attempt["run_id"] == result.run_id]
    assert len(retried) == 2
    assert retried[0]["staging"] != retried[1]["staging"]
    assert all(not Path(attempt["staging"]).exists() for attempt in attempts)
    assert all(verify_manifest_checksums(prior).values())
    assert len(list((destination / "survey/runs").iterdir())) == 2
    # Reloading imports registers the executable spec again after clearing.
    reloaded = discover_user_workflows(workflows, clear_registries=True)
    assert dg.AssetKey("survey") in reloaded.resolve_asset_graph().get_all_asset_keys()
