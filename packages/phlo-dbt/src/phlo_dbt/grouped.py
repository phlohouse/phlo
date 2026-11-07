"""Execute selected dbt models once and attribute outcomes to individual assets.

The project owns test selectors and runtime configuration. Skipped nodes emit no
materialisation or passing check; failed nodes do not hide successful siblings.
"""

from __future__ import annotations

from collections.abc import Mapping
from hashlib import sha256
from pathlib import Path
from typing import Any

from phlo.capabilities.specs import RunGroupResult
from phlo.capabilities.runtime import RuntimeContext
from phlo_dbt.asset_checks import dbt_asset_outcomes
from phlo_dbt.runtime_config import resolve_dbt_target_name
from phlo_dbt.transformer import DbtTransformer
from phlo_dbt.translator import DbtSpecTranslator


def run_dbt_group(
    runtime: RuntimeContext,
    selected: frozenset[str],
    *,
    project_dir: Path,
    profiles_dir: Path,
    manifest: Mapping[str, Any],
    asset_keys: dict[str, str],
    tests: dict[str, tuple[str, ...]],
    translator: DbtSpecTranslator,
) -> RunGroupResult:
    """Build selected models and owned tests using one isolated dbt invocation."""
    nodes = {
        uid: props
        for uid, props in manifest["nodes"].items()
        if asset_keys.get(uid) in selected
        and props.get("resource_type") in {"model", "seed", "snapshot"}
    }
    selectors = [
        name
        for uid, props in nodes.items()
        for name in (props["name"], *tests.get(asset_keys[uid], ()))
    ]
    target = resolve_dbt_target_name(runtime)
    transformer = DbtTransformer(
        context=runtime,
        logger=runtime.logger,
        project_dir=project_dir,
        profiles_dir=profiles_dir,
        target=target,
        translator=translator,
    )
    build = transformer.run_transform(
        partition_key=runtime.partition_key,
        parameters={
            "select": selectors,
            "indirect_selection": "empty",
            "full_refresh": False,
            "execution_key": "group_"
            + sha256("\n".join(sorted(selected)).encode()).hexdigest()[:16],
        },
    )
    outcomes = dbt_asset_outcomes(
        run_results=transformer.build_run_results,
        manifest=manifest,
        selected=selected,
        translator=translator,
        runtime=runtime,
        target=target,
        error=build.error,
    )
    return RunGroupResult(
        outcomes=outcomes,
        error=(build.error or "dbt invocation failed") if build.status != "success" else None,
    )
