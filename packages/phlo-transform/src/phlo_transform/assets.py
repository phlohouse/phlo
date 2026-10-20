"""Asset specifications backed by a Phlo Transform workspace.

Each compiled model becomes one asset whose dependencies come from the
engine's own graph report. Materialisation shells out to
``phlo-transform run --select <model>`` and translates the run report into a
materialisation plus one asset check per workspace test, so orchestrator
adapters see the same contract they receive from the dbt provider.
"""

from __future__ import annotations

from pathlib import Path

from phlo.capabilities import (
    AssetCheckSpec,
    AssetSpec,
    CheckResult,
    MaterializeResult,
    RunSpec,
    RuntimeContext,
    resolve_capability,
    routing_from_context,
)
from phlo.capabilities.interfaces import RefQueryCatalogManager
from phlo.config.env import resolve_project_root
from phlo.config.transformation import active_transformation_provider
from phlo.exceptions import PhloCapabilitySetupError
from phlo.logging import get_logger
from phlo.transform import get_transform_assets

from phlo_transform.binary import run_json
from phlo_transform.settings import TransformSettings, get_settings
from phlo_transform.workspace import TransformWorkspace, WorkspaceModel, load_workspace

logger = get_logger(__name__)

DEFAULT_TRINO_ENDPOINT = "http://localhost:8080"


def has_dbt_project(project_root: Path) -> bool:
    """Return True when a dbt project exists in the project tree.

    Used only to pick the default owner when the project has not selected a
    transformation provider: existing dbt projects keep the graph they had
    before ``phlo-transform`` was installed. Once selected explicitly, the
    provider marker or environment variable always wins.
    """

    if (project_root / "dbt_project.yml").is_file():
        return True
    for marker in project_root.glob("workflows/**/dbt_project.yml"):
        if "dbt_packages" in marker.parts:
            continue
        return True
    return False


def provider_is_active(project_root: Path) -> bool:
    """Return True when the transform provider should own the asset graph."""

    active = active_transformation_provider(project_root)
    if active == "transform":
        return True
    if active == "dbt":
        return False
    return not has_dbt_project(project_root)


def _asset_key(model: WorkspaceModel, mode: str) -> str:
    return model.leaf_name if mode == "leaf" else model.name


def _key_map(workspace: TransformWorkspace, mode: str) -> dict[str, str]:
    keys: dict[str, str] = {}
    owners: dict[str, str] = {}
    for model in workspace.models:
        key = _asset_key(model, mode)
        owner = owners.get(key)
        if owner is not None and owner != model.name:
            raise PhloCapabilitySetupError(
                capability="transform",
                required=True,
                message=(
                    f"transform asset key collision: '{key}' is produced by both "
                    f"'{owner}' and '{model.name}'"
                ),
                suggestions=[
                    "Set PHLO_TRANSFORM_ASSET_KEY=logical to keep namespace-qualified keys",
                    "Rename one of the models",
                ],
            )
        owners[key] = model.name
        keys[model.name] = key
    return keys


def _check_name(test_name: str) -> str:
    return test_name.replace(".", "_").replace(" ", "_")


def _check_specs(
    workspace: TransformWorkspace, model: WorkspaceModel, key: str
) -> list[AssetCheckSpec]:
    return [
        AssetCheckSpec(
            name=_check_name(test.name),
            asset_key=key,
            blocking=True,
            description=f"phlo-transform test {test.name}",
        )
        for test in workspace.tests_for(model.name)
    ]


def _query_engine_resolution(runtime: RuntimeContext | None):
    if runtime is None:
        return None
    try:
        return resolve_capability("query_engine", runtime=runtime)
    # reason: capability lookup must not break discovery; settings fallback follows.
    except Exception:  # noqa: BLE001 - adapter resolution failure falls back to settings
        logger.warning("phlo_transform_query_engine_resolution_failed", exc_info=True)
        return None


def _trino_catalog(
    runtime: RuntimeContext | None, settings: TransformSettings
) -> tuple[str | None, str | None]:
    """Return the target catalog and environment ref for a run."""

    ref: str | None = None
    if runtime is not None:
        routing = routing_from_context(runtime)
        ref = routing.ref
    catalog = settings.trino_catalog
    if catalog is None:
        resolution = _query_engine_resolution(runtime)
        if resolution is not None and resolution.metadata.get("default_catalog"):
            catalog = str(resolution.metadata["default_catalog"])
    if ref and ref != "main":
        resolution = _query_engine_resolution(runtime)
        provider = resolution.provider if resolution is not None else None
        if isinstance(provider, RefQueryCatalogManager):
            catalog = provider.provision_ref_query_catalog(ref)
    return catalog, ref


def adapter_args(
    runtime: RuntimeContext | None,
    settings: TransformSettings,
    *,
    infer: bool = True,
) -> list[str]:
    """Build adapter flags from settings and the resolved query engine.

    ``infer`` distinguishes orchestrated asset runs (where the Phlo query
    engine supplies the target) from the CLI (where the engine's own default
    and error guidance should apply unless an adapter was configured).
    """

    adapter = (settings.adapter or "").strip().lower() or None
    resolution = _query_engine_resolution(runtime)
    metadata = resolution.metadata if resolution is not None else {}
    endpoint = settings.trino_endpoint
    if endpoint is None and metadata.get("host"):
        endpoint = f"http://{metadata['host']}:{metadata.get('port', 8080)}"
    if adapter is None:
        if endpoint:
            adapter = "trino"
        elif infer:
            adapter = "duckdb"
        else:
            return []
    if adapter == "duckdb":
        args = ["--adapter", "duckdb"]
        if settings.duckdb_path:
            args.extend(["--duckdb-path", settings.duckdb_path])
        return args
    if adapter != "trino":
        raise PhloCapabilitySetupError(
            capability="transform",
            required=True,
            message=f"unsupported phlo-transform adapter '{adapter}'",
            suggestions=["Set PHLO_TRANSFORM_ADAPTER to trino or duckdb"],
        )

    args = ["--adapter", "trino", "--trino-endpoint", endpoint or DEFAULT_TRINO_ENDPOINT]
    catalog, ref = _trino_catalog(runtime, settings)
    if catalog:
        args.extend(["--catalog", catalog])
        args.extend(["--trino-catalog", catalog])
    if settings.trino_schema:
        args.extend(["--trino-schema", settings.trino_schema])
    if settings.trino_user:
        args.extend(["--trino-user", settings.trino_user])
    if settings.trino_password:
        args.extend(["--trino-password", settings.trino_password])
    if ref and ref != "main":
        args.extend(["--environment", ref])
    return args


def _run_model(
    workspace: TransformWorkspace,
    model: WorkspaceModel,
    key: str,
    settings: TransformSettings,
    runtime: RuntimeContext | None,
) -> list[MaterializeResult | CheckResult]:
    """Execute one model and translate its run report into Phlo results."""

    payload, invocation = run_json(
        workspace.binary,
        ["run", "--select", model.name, *adapter_args(runtime, settings)],
        settings=settings,
        root=workspace.root,
    )
    if payload is None:
        detail = (invocation.stderr or invocation.stdout).strip().splitlines()
        raise PhloCapabilitySetupError(
            capability="transform",
            required=True,
            message=(
                f"phlo-transform produced no run report for '{model.name}'"
                + (f": {detail[-1]}" if detail else "")
            ),
            suggestions=["Run the same command with --json to inspect the failure"],
        )

    status = str(payload.get("status") or ("passed" if invocation.ok else "failed"))
    counts = payload.get("counts") or {}
    model_report = next(
        (
            item
            for item in payload.get("models") or []
            if isinstance(item, dict) and item.get("model") == model.name
        ),
        {},
    )
    test_results = {
        str(item.get("test")): item
        for item in payload.get("tests") or []
        if isinstance(item, dict) and item.get("test")
    }

    results: list[MaterializeResult | CheckResult] = []
    for test in workspace.tests_for(model.name):
        outcome = test_results.get(test.name)
        passed = outcome is not None and str(outcome.get("status")) == "passed"
        results.append(
            CheckResult(
                check_name=_check_name(test.name),
                asset_key=key,
                passed=passed,
                severity="error",
                metadata={
                    "test": test.name,
                    "status": str(outcome.get("status")) if outcome else "not_reported",
                    "row_count": outcome.get("row_count") if outcome else None,
                    "error": outcome.get("error") if outcome else "test did not report a result",
                },
            )
        )

    results.append(
        MaterializeResult(
            status="success" if status == "passed" and invocation.ok else "failure",
            metadata={
                "transform_model": model.name,
                "transform_target": model.target,
                "transform_materialization": model.materialization,
                "transform_version": model.version,
                "transform_workspace": str(workspace.root),
                "transform_run_id": payload.get("run_id"),
                "transform_plan_id": payload.get("plan_id"),
                "transform_status": status,
                "transform_counts": counts,
                "transform_duration_ms": model_report.get("duration_ms"),
                "transform_query_id": model_report.get("query_id"),
                "transform_tests": len(workspace.tests_for(model.name)),
            },
        )
    )
    return results


def build_workspace_asset_specs(
    project_root: Path,
    settings: TransformSettings,
) -> list[AssetSpec]:
    """Build asset specs for every model in the project's transform workspace."""

    workspace = load_workspace(project_root, settings)
    if workspace is None:
        return []
    mode = settings.asset_key_mode
    keys = _key_map(workspace, mode)
    specs: list[AssetSpec] = []
    for model in workspace.models:
        key = keys[model.name]
        deps = [keys[dep] for dep in model.depends_on if dep in keys]
        source_assets = [
            workspace.source_assets[source]
            for source in model.sources
            if source in workspace.source_assets
        ]
        deps.extend(asset for asset in source_assets if asset not in deps)
        missing = [dep for dep in model.depends_on if dep not in keys]
        if missing:
            logger.warning(
                "phlo_transform_model_dependency_missing",
                model=model.name,
                missing=missing,
            )

        def _runner(
            runtime: RuntimeContext,
            bound_model: WorkspaceModel = model,
            bound_key: str = key,
        ) -> list[MaterializeResult | CheckResult]:
            return _run_model(
                workspace,
                bound_model,
                bound_key,
                settings,
                runtime,
            )

        specs.append(
            AssetSpec(
                key=key,
                group=model.group,
                description=f"Phlo Transform model {model.name}",
                kinds={"transform", "sql", model.materialization},
                tags={"tool": "phlo-transform", "provider": "transform"},
                metadata={
                    "transform_model": model.name,
                    "transform_target": model.target,
                    "transform_materialization": model.materialization,
                    "transform_version": model.version,
                    "transform_path": model.path,
                    "transform_tags": list(model.tags),
                    "transform_sources": list(model.sources),
                    "transform_source_assets": source_assets,
                    "transform_workspace": str(workspace.root),
                },
                deps=deps,
                checks=_check_specs(workspace, model, key),
                run=RunSpec(fn=_runner),
            )
        )
    logger.info(
        "phlo_transform_asset_specs_built",
        workspace_root=str(workspace.root),
        spec_count=len(specs),
    )
    return specs


def build_transform_asset_specs(project_root: Path | None = None) -> list[AssetSpec]:
    """Return every transform asset: Python declarations plus workspace models.

    ``phlo.transform.sql`` declarations are ordinary Python assets and stay
    available regardless of which execution engine owns SQL workspaces. The
    native workspace contributes assets only when it is the active provider.
    """

    root = resolve_project_root(project_root)
    specs = list(get_transform_assets())
    settings = get_settings(root)
    if not provider_is_active(root):
        logger.info(
            "phlo_transform_provider_inactive",
            active_provider=active_transformation_provider(root),
            workspace=settings.root,
        )
        return specs
    specs.extend(build_workspace_asset_specs(root, settings))
    return specs


__all__ = [
    "adapter_args",
    "build_transform_asset_specs",
    "build_workspace_asset_specs",
    "has_dbt_project",
    "provider_is_active",
]
