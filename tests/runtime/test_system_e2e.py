"""Structural tests for Dagster definitions built from the market-data example."""

from pathlib import Path

from dagster import ResourceDefinition
from phlo_dagster.framework.definitions import build_definitions

_repository_root = Path(__file__).resolve().parents[2]
_workflows_path = _repository_root / "examples/lakehouses/market-data-fx/workflows"
defs = build_definitions(workflows_path=_workflows_path)

_assets = list(defs.assets or [])


class TestDefinitionsStructure:
    """Validate the assembled Definitions object is wired correctly."""

    def test_definitions_have_assets_and_core_resources(self):
        """Core resources (trino) are present alongside discovered assets."""
        assert _assets, "expected at least one asset"
        resources = defs.resources or {}
        assert isinstance(resources.get("trino"), ResourceDefinition)
        assert isinstance(resources.get("table_store"), ResourceDefinition)

    def test_asset_keys_are_unique(self):
        """Every asset key appears exactly once (no duplicates)."""
        seen: list[str] = []
        for asset in _assets:
            if hasattr(asset, "keys"):
                seen.extend(str(k) for k in asset.keys)
            else:
                k = getattr(asset, "key", None)
                if k:
                    seen.append(str(k))
        assert len(seen) == len(set(seen)), (
            f"duplicate asset keys: {[k for k in seen if seen.count(k) > 1]}"
        )
