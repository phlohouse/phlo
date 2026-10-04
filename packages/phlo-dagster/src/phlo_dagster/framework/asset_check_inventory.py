"""Build bounded, repository-local asset-check definition metadata."""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from typing import Any

import dagster as dg

INVENTORY_METADATA_KEY = "phlo/asset-check-inventory"
INVENTORY_VERSION = 1
MAX_CHECKS_PER_ASSET = 100
MAX_INVENTORY_BYTES = 16_384


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

    assets = dg.map_asset_specs(add_inventory, asset_graph.assets_defs)
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
    )
