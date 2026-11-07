"""Execute selected dbt models once and attribute outcomes to individual assets.

The project owns test selectors and runtime configuration. Skipped nodes emit no
materialisation or passing check; failed nodes do not hide successful siblings.
"""

from __future__ import annotations

from collections.abc import Mapping
from hashlib import sha256
from pathlib import Path
from typing import Any

from phlo.capabilities.specs import CheckResult, MaterializeResult, RunGroupResult
from phlo.capabilities.runtime import RuntimeContext
from phlo_dbt.asset_checks import extract_dbt_asset_checks
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
    payload = transformer.build_run_results
    checks = extract_dbt_asset_checks(
        run_results=payload or {},
        manifest=manifest,
        translator=translator,
        partition_key=runtime.partition_key,
    )
    by_id = {item["unique_id"]: item for item in (payload or {}).get("results", [])}
    outcomes: dict[str, list[MaterializeResult | CheckResult]] = {}
    for uid, props in nodes.items():
        key = asset_keys[uid]
        node = by_id.get(uid, {})
        if node.get("status") in {"skip", "skipped"}:
            continue
        owned = [
            check
            for check in checks
            if check.asset_key == key and check.metadata.get("status") not in {"skip", "skipped"}
        ]
        missing_tests = [
            test_id
            for test_id, test_props in manifest["nodes"].items()
            if test_props.get("resource_type") == "test"
            and test_props.get("name") in tests.get(key, ())
            and test_id not in by_id
        ]
        failed = (
            node.get("status") != "success"
            or bool(missing_tests)
            or any(
                check.metadata.get("status") in {"fail", "error", "runtime error"}
                for check in owned
            )
        )
        status = "failure" if failed else "success"
        outcomes[key] = [
            *owned,
            MaterializeResult(
                status=status,
                metadata={
                    "model": props["name"],
                    "dbt_target": target,
                    "dbt_status": node.get("status", "missing"),
                    "dbt_metadata": {
                        "invocation_id": (payload or {}).get("metadata", {}).get("invocation_id"),
                        "error": (node.get("message") or build.error) if failed else None,
                        "missing_tests": missing_tests,
                    },
                    "transform_status": status,
                    "transform_run_id": runtime.run_id,
                    "transform_duration_ms": round(float(node.get("execution_time", 0)) * 1000),
                    "transform_counts": {
                        "passed": sum(check.passed for check in owned),
                        "failed": sum(not check.passed for check in owned),
                    },
                },
            ),
        ]
    return RunGroupResult(
        outcomes=outcomes,
        error=(build.error or "dbt invocation failed") if build.status != "success" else None,
    )
