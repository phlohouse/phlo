"""Build bounded, repository-local asset-check definition metadata."""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from collections.abc import Callable, Sequence
from typing import Any

import dagster as dg
from dagster._core.definitions.assets.definition.cacheable_assets_definition import (
    AssetsDefinitionCacheableData,
    CacheableAssetsDefinition,
)
from dagster._utils.warnings import disable_dagster_warnings

INVENTORY_METADATA_KEY = "phlo/asset-check-inventory"
INVENTORY_VERSION = 1
MAX_CHECKS_PER_ASSET = 100
MAX_INVENTORY_BYTES = 16_384


class _InventoryCacheableAssetsDefinition(CacheableAssetsDefinition):
    """Preserve cacheable asset loading while adding metadata to built specs."""

    def __init__(
        self,
        wrapped: CacheableAssetsDefinition,
        add_inventory: Callable[[dg.AssetSpec], dg.AssetSpec],
    ) -> None:
        """Retain the provider and its cache identity."""
        super().__init__(wrapped.unique_id)
        self._wrapped = wrapped
        self._add_inventory = add_inventory

    def compute_cacheable_data(self) -> Sequence[AssetsDefinitionCacheableData]:
        """Delegate cache-data calculation to the wrapped asset provider."""
        return list(self._wrapped.compute_cacheable_data())

    def build_definitions(
        self, data: Sequence[AssetsDefinitionCacheableData]
    ) -> Sequence[dg.AssetsDefinition]:
        """Attach inventory metadata after the wrapped provider builds assets."""
        assets = self._wrapped.build_definitions(data)
        return dg.map_asset_specs(self._add_inventory, assets)


def add_asset_check_inventory(definitions: dg.Definitions) -> dg.Definitions:
    """Attach bounded check definitions from the fully resolved local asset graph."""
    asset_graph = definitions.resolve_asset_graph()
    checks_by_key: dict[tuple[str, ...], list[dict[str, str | None]]] = defaultdict(list)
    for check_key in asset_graph.asset_check_keys:
        spec = asset_graph.get_check_spec(check_key)
        key = tuple(spec.asset_key.path)
        checks_by_key[key].append({"name": spec.name, "description": spec.description})

    def inventory_for(key: tuple[str, ...]) -> dict[str, Any]:
        checks = sorted(checks_by_key.get(key, []), key=lambda item: item["name"] or "")
        payload: dict[str, Any] = {
            "version": INVENTORY_VERSION,
            "asset_key": list(key),
            "complete": len(checks) <= MAX_CHECKS_PER_ASSET,
            "checks": checks if len(checks) <= MAX_CHECKS_PER_ASSET else [],
        }

        def with_digest() -> dict[str, Any]:
            encoded_payload = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
            return {**payload, "digest": hashlib.sha256(encoded_payload).hexdigest()}

        result = with_digest()
        if len(json.dumps(result).encode()) > MAX_INVENTORY_BYTES:
            payload["complete"] = False
            payload["checks"] = []
            result = with_digest()
        return result

    def add_inventory(spec: dg.AssetSpec) -> dg.AssetSpec:
        return spec.merge_attributes(
            metadata={INVENTORY_METADATA_KEY: inventory_for(tuple(spec.key.path))}
        )

    assets: list[Any] = []
    known_asset_keys: set[dg.AssetKey] = set()
    for asset in definitions.assets or []:
        if isinstance(asset, dg.AssetsDefinition):
            assets.append(asset.map_asset_specs(add_inventory))
            known_asset_keys.update(asset.keys)
        elif isinstance(asset, dg.AssetSpec):
            assets.append(add_inventory(asset))
            known_asset_keys.add(asset.key)
        elif isinstance(asset, dg.SourceAsset):
            source_options: dict[str, Any] = {
                "key": asset.key,
                "metadata": {
                    **asset.raw_metadata,
                    INVENTORY_METADATA_KEY: inventory_for(tuple(asset.key.path)),
                },
                "io_manager_key": asset.io_manager_key,
                "io_manager_def": asset.io_manager_def,
                "description": asset.description,
                "partitions_def": asset.partitions_def,
                "group_name": asset.group_name,
                "resource_defs": asset.resource_defs,
                "observe_fn": asset.observe_fn,
                "op_tags": asset.op_tags,
                "auto_observe_interval_minutes": asset.auto_observe_interval_minutes,
                "legacy_freshness_policy": asset.legacy_freshness_policy,
                "automation_condition": asset.automation_condition,
                "tags": asset.tags,
                "_required_resource_keys": asset.required_resource_keys,
            }
            with disable_dagster_warnings():
                assets.append(dg.SourceAsset.dagster_internal_init(**source_options))
            known_asset_keys.add(asset.key)
        elif isinstance(asset, CacheableAssetsDefinition):
            assets.append(_InventoryCacheableAssetsDefinition(asset, add_inventory))
            for cacheable_data in asset.compute_cacheable_data():
                if cacheable_data.keys_by_output_name is not None:
                    known_asset_keys.update(cacheable_data.keys_by_output_name.values())

    for key in asset_graph.get_all_asset_keys() - known_asset_keys:
        assets.append(add_inventory(dg.AssetSpec(key)))

    return dg.Definitions(
        assets=assets,
        schedules=definitions.schedules,
        sensors=definitions.sensors,
        resources=definitions.resources,
        jobs=definitions.jobs,
        executor=definitions.executor,
        loggers=definitions.loggers,
        metadata=definitions.metadata,
        component_tree=definitions.component_tree,
        asset_checks=definitions.asset_checks,
    )
