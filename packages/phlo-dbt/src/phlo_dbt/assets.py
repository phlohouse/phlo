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
from phlo_dbt.discovery import read_dbt_project_name
from phlo_dbt.runtime_config import ensure_dbt_profile, resolve_dbt_target_name
from phlo_dbt.settings import get_settings

from phlo_dbt.asset_checks import (
    dbt_asset_check_specs,
    dbt_asset_check_names,
    extract_dbt_asset_checks,
)
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
    """Resolve the upstream asset keys for a dbt node from its manifest deps."""
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
    asset_key: str,
    project_dir: Path,
    profiles_dir: Path,
    runtime: RuntimeContext,
    manifest: Mapping[str, Any],
    translator: DbtSpecTranslator,
    key_prefix: str | None = None,
) -> list[MaterializeResult | CheckResult]:
    """Execute a single dbt model and return its materialization result plus
    test-check results."""
    target = resolve_dbt_target_name(runtime)
    partition_key = runtime.partition_key
    test_names = dbt_asset_check_names(manifest, asset_key=asset_key, translator=translator)

    transformer = DbtTransformer(
        context=runtime,
        logger=runtime.logger,
        project_dir=project_dir,
        profiles_dir=profiles_dir,
        target=target,
        key_prefix=key_prefix,
    )

    result = transformer.run_transform(
        partition_key=partition_key,
        parameters={
            "select": [model_name, *test_names],
            "indirect_selection": "empty",
        },
    )

    checks = _read_dbt_asset_checks(
        asset_key=asset_key,
        run_results=transformer.build_run_results,
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
    asset_key: str,
    run_results: Mapping[str, Any] | None,
    manifest: Mapping[str, Any],
    translator: DbtSpecTranslator,
    partition_key: str | None,
) -> list[CheckResult]:
    """Read dbt test outcomes captured from the current asset build."""
    if run_results is None:
        logger.warning("dbt_asset_check_results_unavailable")
        return []
    return [
        check
        for check in extract_dbt_asset_checks(
            run_results,
            manifest,
            translator=translator,
            partition_key=partition_key,
        )
        if check.asset_key == asset_key
    ]


def _build_project_asset_specs(
    *,
    project_path: Path,
    project_name: str,
    key_prefix: str | None,
) -> tuple[list[AssetSpec], dict[str, str], set[str]]:
    """Build asset specs for one dbt project from its manifest metadata.

    Returns the specs, the unique-id-to-asset-key map used for dependency
    resolution (nodes and sources), and the set of explicitly declared
    cross-project reference keys (sources with ``meta.phlo_asset_key``).
    """
    settings = get_settings()
    profiles_path = settings.dbt_profiles_path_for(project_path)
    manifest_path = project_path / "target" / "manifest.json"

    if not project_path.exists() or not (project_path / "dbt_project.yml").exists():
        logger.warning(
            "optional_capability_degraded",
            capability="dbt",
            reason="project_missing",
            dbt_project_path=str(project_path),
        )
        return [], {}, set()

    ensure_dbt_profile(profiles_path, project_dir=project_path)

    if not ensure_dbt_manifest(project_path, profiles_path):
        _raise_required_dbt_setup_error(
            reason="manifest_unavailable",
            dbt_project_path=project_path,
            dbt_profiles_path=profiles_path,
            manifest_path=manifest_path,
        )

    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        _raise_required_dbt_setup_error(
            reason="manifest_read_failed",
            dbt_project_path=project_path,
            dbt_profiles_path=profiles_path,
            manifest_path=manifest_path,
        )

    if not isinstance(manifest, Mapping):
        _raise_required_dbt_setup_error(
            reason="manifest_not_mapping",
            dbt_project_path=project_path,
            dbt_profiles_path=profiles_path,
            manifest_path=manifest_path,
        )

    translator = DbtSpecTranslator(project_dir=project_path, key_prefix=key_prefix)
    nodes = manifest.get("nodes")
    sources = manifest.get("sources")
    if nodes is None:
        nodes = {}
    if sources is None:
        sources = {}
    if not isinstance(nodes, Mapping) or not isinstance(sources, Mapping):
        _raise_required_dbt_setup_error(
            reason="manifest_shape_invalid",
            dbt_project_path=project_path,
            dbt_profiles_path=profiles_path,
            manifest_path=manifest_path,
        )

    asset_keys: dict[str, str] = {}
    cross_refs: set[str] = set()
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
        if str(props.get("resource_type") or "") == "source":
            meta = props.get("meta")
            if isinstance(meta, Mapping) and (meta.get("phlo_asset_key") or meta.get("asset_key")):
                cross_refs.add(str(asset_key))

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
        if key_prefix:
            metadata = {**metadata, "dbt_project": project_name}

        checks = [check for check in check_specs if check.asset_key == asset_key]

        # Bind the model name and asset key via default arguments: a plain
        # closure would capture the loop variable by reference and every spec
        # would end up running whichever node was processed last.
        def _runner(
            runtime: RuntimeContext, model=model_name, key=asset_key
        ) -> list[MaterializeResult | CheckResult]:
            """Execute one dbt-backed asset run and return materialization and
            check results for the bound model."""
            return _run_dbt_model(
                model_name=model,
                asset_key=key,
                project_dir=project_path,
                profiles_dir=profiles_path,
                runtime=runtime,
                manifest=manifest,
                translator=translator,
                key_prefix=key_prefix,
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
        dbt_project_path=str(project_path),
    )
    return specs, asset_keys, cross_refs


def build_dbt_asset_specs() -> list[AssetSpec]:
    """Build asset specifications for supported dbt nodes across all activated projects.

    Activates exactly one project by default (existing single-project
    behavior). When ``dbt_project_dirs`` (env ``DBT_PROJECT_DIRS``) lists
    several projects, each is compiled and merged into one asset graph.
    With ``dbt_namespaced_asset_keys`` (env ``DBT_NAMESPACED_ASSET_KEYS``),
    dbt-derived keys are prefixed with the project name to prevent
    cross-domain collisions. Collisions that survive namespacing raise
    ``PhloCapabilitySetupError``; cross-project source references that do not
    resolve to any known dbt asset key are logged loudly.
    """
    settings = get_settings()
    project_paths = settings.dbt_project_paths
    namespaced = settings.dbt_namespaced_asset_keys

    if len(project_paths) > 1:
        logger.info(
            "dbt_multi_project_activation",
            project_count=len(project_paths),
            projects=[str(path) for path in project_paths],
        )

    all_specs: list[AssetSpec] = []
    known_keys: set[str] = set()
    key_owner: dict[str, Path] = {}
    cross_refs: set[str] = set()
    seen_paths: set[Path] = set()
    seen_project_names: set[str] = set()
    for project_path in project_paths:
        resolved_path = project_path.resolve()
        if resolved_path in seen_paths:
            raise PhloCapabilitySetupError(
                capability="dbt",
                required=True,
                message=f"dbt project directory listed more than once: '{project_path}'",
                suggestions=[
                    "Remove the duplicate entry from DBT_PROJECT_DIRS",
                ],
            )
        seen_paths.add(resolved_path)
        project_name = read_dbt_project_name(project_path)
        if project_name in seen_project_names:
            raise PhloCapabilitySetupError(
                capability="dbt",
                required=True,
                message=(
                    f"dbt project name '{project_name}' is declared by more than one "
                    "activated project"
                ),
                suggestions=[
                    "Give each activated dbt project a unique name in its dbt_project.yml",
                ],
            )
        seen_project_names.add(project_name)
        key_prefix = project_name if namespaced else None
        specs, asset_keys, project_refs = _build_project_asset_specs(
            project_path=project_path,
            project_name=project_name,
            key_prefix=key_prefix,
        )

        # A same-named model in two projects would silently merge into one
        # asset; namespacing prevents it, and any surviving collision must
        # fail loudly instead. Source-derived keys are exempt: two projects
        # declaring the same foreign source (e.g. another domain's dlt_ table)
        # is the intended cross-domain reference pattern.
        for asset_key in sorted({spec.key for spec in specs}):
            owner = key_owner.get(asset_key)
            if owner is not None and owner != resolved_path:
                raise PhloCapabilitySetupError(
                    capability="dbt",
                    required=True,
                    message=(
                        f"dbt asset key collision across projects: '{asset_key}' is "
                        f"produced by both '{owner.name}' and '{project_name}'"
                    ),
                    suggestions=[
                        "Set DBT_NAMESPACED_ASSET_KEYS=1 to prefix dbt asset keys "
                        "with the dbt project name",
                        "Rename the colliding dbt model or source table",
                    ],
                )
            key_owner[asset_key] = resolved_path
        known_keys.update(asset_keys.values())
        cross_refs.update(project_refs)
        all_specs.extend(specs)

    unresolved = sorted(key for key in cross_refs if key not in known_keys)
    if unresolved:
        logger.warning(
            "dbt_cross_project_reference_unresolved",
            asset_keys=unresolved,
            hint=(
                "Referenced asset keys were not found in any activated dbt "
                "project; verify they are owned by another provider (e.g. "
                "phlo-dlt) or that the owning project is activated."
            ),
        )

    logger.info(
        "dbt_asset_specs_built",
        spec_count=len(all_specs),
        project_count=len(project_paths),
    )
    return all_specs
