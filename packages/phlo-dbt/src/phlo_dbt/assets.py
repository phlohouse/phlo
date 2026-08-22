"""dbt asset specification builders for Phlo.

This module provides functionality to discover and build Phlo asset specifications
from dbt project manifests. It handles manifest parsing, dependency resolution,
and runtime execution of dbt models within the Phlo orchestration framework.

Example:
    >>> from phlo_dbt.assets import build_dbt_asset_specs
    >>> specs = build_dbt_asset_specs()
    >>> for spec in specs:
    ...     print(f"Asset: {spec.key}, Group: {spec.group}")

"""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from phlo.capabilities import (
    AssetSpec,
    CheckResult,
    MaterializeResult,
    PartitionSpec,
    RunSpec,
)
from phlo.capabilities.runtime import RuntimeContext
from phlo.exceptions import PhloCapabilitySetupError
from phlo.logging import get_logger
from phlo_dbt.runtime_config import ensure_dbt_profile, resolve_dbt_target_name
from phlo_dbt.settings import get_settings

from phlo_dbt.asset_checks import dbt_asset_check_specs, extract_dbt_asset_checks
from phlo_dbt.transformer import DbtTransformer, ensure_dbt_manifest
from phlo_dbt.translator import DbtSpecTranslator

logger = get_logger(__name__)


def _raise_required_dbt_setup_error(
    *,
    reason: str,
    dbt_project_path: Path,
    dbt_profiles_path: Path,
    manifest_path: Path,
) -> None:
    """Raise a required capability setup error for dbt asset discovery."""
    raise PhloCapabilitySetupError(
        capability="dbt",
        required=True,
        message=f"dbt asset discovery failed: {reason}",
        suggestions=[
            f"Check the dbt project at {dbt_project_path}",
            f"Check generated profiles at {dbt_profiles_path}",
            f"Ensure dbt can parse or compile a valid manifest at {manifest_path}",
        ],
    )


def _asset_deps(unique_id: str, nodes: Mapping[str, Any], asset_keys: dict[str, str]) -> list[str]:
    """Resolve upstream asset dependencies for a dbt node.

    Args:
        unique_id: dbt unique node identifier.
        nodes: Manifest node mapping.
        asset_keys: Mapping of dbt unique IDs to asset keys.

    Returns:
        Upstream asset keys for the node.

    """
    props = nodes.get(unique_id, {})
    depends_on = props.get("depends_on") or {}
    depends_nodes = depends_on.get("nodes") or []
    deps: list[str] = []
    if isinstance(depends_nodes, list):
        for upstream_id in depends_nodes:
            key = asset_keys.get(str(upstream_id))
            if key:
                deps.append(key)
    return deps


def _run_dbt_model(
    *,
    model_name: str,
    project_dir: Path,
    profiles_dir: Path,
    runtime: RuntimeContext,
    manifest: Mapping[str, Any],
    translator: DbtSpecTranslator,
) -> list[MaterializeResult | CheckResult]:
    """Execute a single dbt model and map result to materialization output.

    Args:
        model_name: dbt model name to execute.
        project_dir: dbt project root.
        profiles_dir: dbt profiles directory.
        runtime: Asset runtime context.

    Returns:
        Materialization and test-check results for the model run.

    """
    target = resolve_dbt_target_name(runtime)
    partition_key = runtime.partition_key

    transformer = DbtTransformer(
        context=runtime,
        logger=runtime.logger,
        project_dir=project_dir,
        profiles_dir=profiles_dir,
        target=target,
    )

    result = transformer.run_transform(
        partition_key=partition_key,
        parameters={"select": [model_name]},
    )

    checks = _read_dbt_asset_checks(
        project_dir=project_dir,
        manifest=manifest,
        translator=translator,
        partition_key=partition_key,
    )
    return [
        *checks,
        MaterializeResult(
            status=result.status,
            metadata={
                "model": model_name,
                "dbt_target": target,
                "dbt_status": result.status,
                "dbt_metadata": result.metadata,
            },
        ),
    ]


def _read_dbt_asset_checks(
    *,
    project_dir: Path,
    manifest: Mapping[str, Any],
    translator: DbtSpecTranslator,
    partition_key: str | None,
) -> list[CheckResult]:
    """Read dbt test outcomes produced by the current asset build."""
    run_results_path = project_dir / "target" / "run_results.json"
    try:
        run_results = json.loads(run_results_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        logger.warning("dbt_asset_check_results_unavailable", path=str(run_results_path))
        return []
    if not isinstance(run_results, Mapping):
        logger.warning("dbt_asset_check_results_invalid", path=str(run_results_path))
        return []
    return extract_dbt_asset_checks(
        run_results,
        manifest,
        translator=translator,
        partition_key=partition_key,
    )


def build_dbt_asset_specs() -> list[AssetSpec]:
    """Build asset specifications from dbt manifest metadata.

    Returns:
        Asset specs representing supported dbt nodes.

    """
    settings = get_settings()

    dbt_project_path = settings.dbt_project_path
    dbt_profiles_path = settings.dbt_profiles_path
    manifest_path = dbt_project_path / "target" / "manifest.json"

    if not dbt_project_path.exists() or not (dbt_project_path / "dbt_project.yml").exists():
        logger.warning(
            "optional_capability_degraded",
            capability="dbt",
            reason="project_missing",
            dbt_project_path=str(dbt_project_path),
        )
        return []

    ensure_dbt_profile(dbt_profiles_path)

    if not ensure_dbt_manifest(dbt_project_path, dbt_profiles_path):
        _raise_required_dbt_setup_error(
            reason="manifest_unavailable",
            dbt_project_path=dbt_project_path,
            dbt_profiles_path=dbt_profiles_path,
            manifest_path=manifest_path,
        )

    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        _raise_required_dbt_setup_error(
            reason="manifest_read_failed",
            dbt_project_path=dbt_project_path,
            dbt_profiles_path=dbt_profiles_path,
            manifest_path=manifest_path,
        )

    if not isinstance(manifest, Mapping):
        _raise_required_dbt_setup_error(
            reason="manifest_not_mapping",
            dbt_project_path=dbt_project_path,
            dbt_profiles_path=dbt_profiles_path,
            manifest_path=manifest_path,
        )

    translator = DbtSpecTranslator()
    nodes = manifest.get("nodes")
    sources = manifest.get("sources")
    if nodes is None:
        nodes = {}
    if sources is None:
        sources = {}
    if not isinstance(nodes, Mapping) or not isinstance(sources, Mapping):
        _raise_required_dbt_setup_error(
            reason="manifest_shape_invalid",
            dbt_project_path=dbt_project_path,
            dbt_profiles_path=dbt_profiles_path,
            manifest_path=manifest_path,
        )

    asset_keys: dict[str, str] = {}
    for unique_id, props in {**nodes, **sources}.items():
        if not isinstance(props, Mapping):
            continue
        try:
            asset_key = translator.get_asset_key(props)
        except Exception:
            logger.exception(
                "dbt_asset_specs_asset_key_translate_failed",
                unique_id=str(unique_id),
            )
            continue
        asset_keys[str(unique_id)] = str(asset_key)

    specs: list[AssetSpec] = []
    check_specs = dbt_asset_check_specs(manifest, translator=translator)
    for unique_id, props in nodes.items():
        if not isinstance(props, Mapping):
            continue
        resource_type = str(props.get("resource_type") or "")
        if resource_type not in {"model", "seed", "snapshot"}:
            continue
        asset_key = asset_keys.get(str(unique_id))
        if not asset_key:
            continue
        model_name = str(props.get("name") or asset_key)
        deps = _asset_deps(str(unique_id), nodes, asset_keys)
        description = translator.get_description(props)
        group = translator.get_group_name(props)
        kinds = translator.get_kinds(props)
        metadata = translator.get_metadata(props)
        tags = {"tool": "dbt"}

        checks = [check for check in check_specs if check.asset_key == asset_key]

        def _runner(
            runtime: RuntimeContext, model=model_name
        ) -> list[MaterializeResult | CheckResult]:
            """Execute one dbt-backed asset run.

            Args:
                runtime: Asset runtime context.
                model: Bound dbt model name for this spec.

            Returns:
                Materialization results for the selected dbt model.

            """
            return _run_dbt_model(
                model_name=model,
                project_dir=dbt_project_path,
                profiles_dir=dbt_profiles_path,
                runtime=runtime,
                manifest=manifest,
                translator=translator,
            )

        specs.append(
            AssetSpec(
                key=asset_key,
                group=group,
                description=description,
                kinds=kinds,
                tags=tags,
                metadata=metadata,
                partitions=PartitionSpec(kind="daily"),
                deps=deps,
                checks=checks,
                run=RunSpec(fn=_runner),
            )
        )

    logger.info(
        "dbt_asset_specs_built",
        spec_count=len(specs),
        dbt_project_path=str(dbt_project_path),
    )
    return specs
