"""Tests the repository-local, bounded asset-check metadata export."""

from __future__ import annotations

import json

import dagster as dg
from dagster._core.definitions.assets.definition.cacheable_assets_definition import (
    AssetsDefinitionCacheableData,
    CacheableAssetsDefinition,
)
from dagster._core.workspace.context import WorkspaceProcessContext
from dagster._core.workspace.load_target import CompositeTarget, PythonFileTarget
from dagster_graphql.test.utils import execute_dagster_graphql
from dagster_shared.utils.warnings import BetaWarning
import pytest

from phlo_dagster.framework.asset_check_inventory import (
    INVENTORY_METADATA_KEY,
    MAX_CHECKS_PER_ASSET,
    add_asset_check_inventory,
)

# Dagster rebuilds check-only definitions with its beta execution_type parameter.
_CHECK_GRAPH_WARNING = r"Parameter `execution_type` of function `AssetsDefinition\.__init__`"


def _repository(check_name: str, *, include_check_only_target: bool = False) -> dg.Definitions:
    key = dg.AssetKey(["shared"])
    assets = [dg.AssetSpec(key, metadata={"existing": "kept"})]
    if include_check_only_target:
        assets.append(dg.AssetSpec("check_only"))

    @dg.asset_check(asset=key, name=check_name, description=f"{check_name} check")
    def check() -> dg.AssetCheckResult:
        return dg.AssetCheckResult(passed=True)

    checks = [check]
    if include_check_only_target:

        @dg.asset_check(asset=dg.AssetKey("check_only"), name="check_only_check")
        def check_only() -> dg.AssetCheckResult:
            return dg.AssetCheckResult(passed=True)

        checks.append(check_only)
    return add_asset_check_inventory(dg.Definitions(assets=assets, asset_checks=checks))


def test_duplicate_key_repositories_keep_each_local_definition_complete() -> None:
    production = _repository("shared_check")
    staging = _repository("shared_check")
    another_staging = _repository("staging_only")

    with pytest.warns(BetaWarning, match=_CHECK_GRAPH_WARNING):
        production_graph = production.get_repository_def().asset_graph
    with pytest.warns(BetaWarning, match=_CHECK_GRAPH_WARNING):
        staging_graph = staging.get_repository_def().asset_graph
    with pytest.warns(BetaWarning, match=_CHECK_GRAPH_WARNING):
        another_staging_graph = another_staging.get_repository_def().asset_graph
    assert {key.name for key in production_graph.asset_check_keys} == {"shared_check"}
    assert {key.name for key in staging_graph.asset_check_keys} == {"shared_check"}
    assert {key.name for key in another_staging_graph.asset_check_keys} == {"staging_only"}

    production_inventory = (
        production.resolve_asset_graph().get(dg.AssetKey("shared")).metadata[INVENTORY_METADATA_KEY]
    )
    staging_inventory = (
        another_staging.resolve_asset_graph()
        .get(dg.AssetKey("shared"))
        .metadata[INVENTORY_METADATA_KEY]
    )
    assert production_inventory["complete"] is True
    assert production_inventory["checks"] == [
        {"name": "shared_check", "description": "shared_check check"}
    ]
    assert staging_inventory["checks"] == [
        {"name": "staging_only", "description": "staging_only check"}
    ]
    assert production.resolve_asset_graph().get(dg.AssetKey("shared")).metadata["existing"] == (
        "kept"
    )


@pytest.mark.parametrize("staging_check", ["shared_check", "staging_only"])
def test_graphql_export_is_local_for_overlapping_repository_keys(tmp_path, staging_check) -> None:
    targets = []
    for location, check_name in (
        ("iot_prod", "shared_check"),
        ("iot_staging", staging_check),
    ):
        directory = tmp_path / location
        directory.mkdir()
        source = directory / "definitions.py"
        source.write_text(
            f"""import dagster as dg
from phlo_dagster.framework.asset_check_inventory import add_asset_check_inventory

@dg.asset(
    key="shared",
    check_specs=[dg.AssetCheckSpec(name={check_name!r}, asset="shared", description={location!r})],
)
def shared():
    return 1

defs = add_asset_check_inventory(dg.Definitions(assets=[shared]))
""",
            encoding="utf-8",
        )
        targets.append(PythonFileTarget(str(source), "defs", str(directory), location))

    query = """query LocalInventory($includeLegacy: Boolean!) {
      repositoriesOrError {
        ... on RepositoryConnection {
          nodes {
            name
            location { name }
            assetNodes {
              assetKey { path }
              metadataEntries {
                label
                ... on JsonMetadataEntry { jsonString }
              }
              assetChecksOrError(limit: 100) @include(if: $includeLegacy) {
                ... on AssetChecks { checks { name description } }
              }
            }
          }
        }
      }
    }"""
    with dg.instance_for_test() as instance:
        with WorkspaceProcessContext(instance, CompositeTarget(targets)) as workspace:
            result = execute_dagster_graphql(
                workspace.create_request_context(), query, {"includeLegacy": False}
            )
    assert not result.errors, result.errors
    repositories = result.data["repositoriesOrError"]["nodes"]
    inventories = {}
    for repository in repositories:
        asset_node = next(
            node for node in repository["assetNodes"] if node["assetKey"]["path"] == ["shared"]
        )
        assert "assetChecksOrError" not in asset_node
        entry = next(
            item
            for item in asset_node["metadataEntries"]
            if item["label"] == INVENTORY_METADATA_KEY
        )
        inventories[repository["location"]["name"]] = json.loads(entry["jsonString"])
    assert inventories["iot_prod"]["checks"] == [
        {"name": "shared_check", "description": "iot_prod"}
    ]
    assert inventories["iot_staging"]["checks"] == [
        {"name": staging_check, "description": "iot_staging"}
    ]


def test_check_only_nonmaterializable_target_is_included() -> None:
    definitions = _repository("shared_check", include_check_only_target=True)
    with pytest.warns(BetaWarning, match=_CHECK_GRAPH_WARNING):
        graph = definitions.resolve_asset_graph()
    inventory = graph.get(dg.AssetKey("check_only")).metadata[INVENTORY_METADATA_KEY]
    assert inventory["checks"] == [{"name": "check_only_check", "description": None}]


def test_inline_asset_check_is_included_in_complete_inventory() -> None:
    @dg.asset(check_specs=[dg.AssetCheckSpec(name="inline_quality", asset="embedded")])
    def embedded() -> tuple[int, dg.AssetCheckResult]:
        return 1, dg.AssetCheckResult(passed=True)

    definitions = add_asset_check_inventory(dg.Definitions(assets=[embedded]))
    graph = definitions.resolve_asset_graph()
    assert {key.name for key in graph.asset_check_keys} == {"inline_quality"}
    inventory = graph.get(dg.AssetKey("embedded")).metadata[INVENTORY_METADATA_KEY]
    assert inventory["complete"] is True
    assert inventory["checks"] == [{"name": "inline_quality", "description": None}]


def test_resolved_cacheable_asset_checks_are_exported() -> None:
    class CacheableAssets(CacheableAssetsDefinition):
        def __init__(self) -> None:
            super().__init__("cacheable-test")

        def compute_cacheable_data(self) -> list[AssetsDefinitionCacheableData]:
            return [
                AssetsDefinitionCacheableData(keys_by_output_name={"result": dg.AssetKey("cached")})
            ]

        def build_definitions(self, data: list[AssetsDefinitionCacheableData]):
            del data
            return [dg.AssetsDefinition(specs=[dg.AssetSpec("cached")])]

    @dg.asset_check(asset=dg.AssetKey("cached"), name="cached_quality")
    def cached_quality() -> dg.AssetCheckResult:
        return dg.AssetCheckResult(passed=True)

    definitions = add_asset_check_inventory(
        dg.Definitions(assets=[CacheableAssets()], asset_checks=[cached_quality])
    )
    with pytest.warns(BetaWarning, match=_CHECK_GRAPH_WARNING):
        graph = definitions.resolve_asset_graph()
    inventory = graph.get(dg.AssetKey("cached")).metadata[INVENTORY_METADATA_KEY]
    assert inventory["complete"] is True
    assert inventory["checks"] == [{"name": "cached_quality", "description": None}]


def test_source_asset_metadata_and_check_target_are_preserved() -> None:
    # Keep the deprecated input to verify compatibility with existing user definitions.
    with pytest.warns(DeprecationWarning, match=r"Class `SourceAsset` is deprecated"):
        source = dg.SourceAsset("source", metadata={"owner": "team"}, description="external")

    @dg.asset_check(asset=dg.AssetKey("source"), name="source_check")
    def source_check() -> dg.AssetCheckResult:
        return dg.AssetCheckResult(passed=True)

    definitions = add_asset_check_inventory(
        dg.Definitions(assets=[source], asset_checks=[source_check])
    )
    with pytest.warns(BetaWarning, match=_CHECK_GRAPH_WARNING):
        graph = definitions.resolve_asset_graph()
    source_node = graph.get(dg.AssetKey("source"))
    assert source_node.is_external
    assert source_node.description == "external"
    assert source_node.metadata["owner"] == "team"
    assert source_node.metadata[INVENTORY_METADATA_KEY]["checks"] == [
        {"name": "source_check", "description": None}
    ]
    assert definitions.get_repository_def().asset_graph.asset_check_keys == {
        dg.AssetCheckKey(dg.AssetKey("source"), "source_check")
    }


def test_inventory_marks_bounded_out_definitions_incomplete() -> None:
    key = dg.AssetKey("shared")
    checks = []
    for index in range(MAX_CHECKS_PER_ASSET + 1):

        @dg.asset_check(asset=key, name=f"check_{index:03}")
        def check() -> dg.AssetCheckResult:
            return dg.AssetCheckResult(passed=True)

        checks.append(check)
    definitions = add_asset_check_inventory(
        dg.Definitions(assets=[dg.AssetSpec(key)], asset_checks=checks)
    )
    with pytest.warns(BetaWarning, match=_CHECK_GRAPH_WARNING):
        graph = definitions.resolve_asset_graph()
    inventory = graph.get(dg.AssetKey("shared")).metadata[INVENTORY_METADATA_KEY]
    assert inventory["complete"] is False
    assert inventory["checks"] == []
