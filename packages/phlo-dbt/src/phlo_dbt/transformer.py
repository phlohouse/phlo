"""dbt transformer implementation for Phlo orchestration.

This module provides the DbtTransformer class which executes dbt commands
within the Phlo transformation framework. It handles dbt build execution,
event emission for lineage and telemetry, and result parsing.

Example:
    >>> from phlo_dbt.transformer import DbtTransformer
    >>> from pathlib import Path
    >>>
    >>> transformer = DbtTransformer(
    ...     context=dagster_context,
    ...     logger=logger,
    ...     project_dir=Path("workflows/transforms/dbt"),
    ...     profiles_dir=Path("workflows/transforms/dbt/profiles"),
    ...     target="prod"
    ... )
    >>>
    >>> result = transformer.run_transform(
    ...     partition_key="2024-01-01",
    ...     parameters={"select": ["mrt_orders"]}
    ... )
    >>>
    >>> print(f"Status: {result.status}")
    >>> print(f"Models built: {result.models_built}")
    >>> print(f"Tests passed: {result.tests_passed}")

"""

from __future__ import annotations

import json
import os
import re
import subprocess
import time
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from phlo.logging import log_event
from phlo.operations.transformation import BaseTransformer, TransformationResult
from phlo.hooks.emitters import (
    LineageEventContext,
    LineageEventEmitter,
    TelemetryEventContext,
    TelemetryEventEmitter,
    TransformEventContext,
    TransformEventEmitter,
)
from phlo.hooks.events import HookCorrelation
from phlo_dbt.lineage_import import collect_asset_lineage, load_dbt_manifest
from phlo_dbt.translator import DbtSpecTranslator
from phlo_dbt.runtime_config import ensure_dbt_profile


def _parse_dbt_summary(stdout: str) -> dict[str, int]:
    """Parse dbt build summary line: PASS=N WARN=N ERROR=N SKIP=N TOTAL=N."""
    match = re.search(
        r"PASS=(\d+)\s+WARN=(\d+)\s+ERROR=(\d+)\s+SKIP=(\d+)\s+TOTAL=(\d+)",
        stdout,
    )
    if not match:
        return {"pass": 0, "warn": 0, "error": 0, "skip": 0, "total": 0}
    return {
        "pass": int(match.group(1)),
        "warn": int(match.group(2)),
        "error": int(match.group(3)),
        "skip": int(match.group(4)),
        "total": int(match.group(5)),
    }


def _resource_type_for_result(result: Mapping[str, Any]) -> str:
    """Return the dbt resource type for a run result, or an empty string if unavailable."""
    resource_type = result.get("resource_type")
    if isinstance(resource_type, str) and resource_type:
        return resource_type
    unique_id = result.get("unique_id")
    if isinstance(unique_id, str) and "." in unique_id:
        return unique_id.split(".", 1)[0]
    return ""


def _parse_run_results_counts(payload: Mapping[str, Any]) -> dict[str, int] | None:
    """Extract model and test pass/fail counts from dbt run results; None if unparsable."""
    results = payload.get("results")
    if not isinstance(results, list):
        return None

    counts = {
        "models_built": 0,
        "models_failed": 0,
        "tests_passed": 0,
        "tests_failed": 0,
    }
    model_types = {"model", "seed", "snapshot"}
    success_statuses = {"pass", "success"}
    skipped_statuses = {"skip", "skipped"}

    for item in results:
        if not isinstance(item, Mapping):
            continue

        status = str(item.get("status") or "").strip().lower()
        resource_type = _resource_type_for_result(item)

        if resource_type in model_types:
            if status in success_statuses:
                counts["models_built"] += 1
            elif status not in skipped_statuses:
                counts["models_failed"] += 1
            continue

        if resource_type == "test":
            if status in success_statuses:
                counts["tests_passed"] += 1
            elif status not in skipped_statuses:
                counts["tests_failed"] += 1

    return counts


def _read_run_results(path: Path) -> Mapping[str, Any] | None:
    """Read a dbt run-results artifact when it contains a JSON object."""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return payload if isinstance(payload, Mapping) else None


def _latest_project_mtime(dbt_project_path: Path) -> float:
    """Return the newest mtime across key dbt project files and asset directories."""
    candidates: list[Path] = [
        dbt_project_path / "dbt_project.yml",
        dbt_project_path / "packages.yml",
        dbt_project_path / "package-lock.yml",
    ]
    candidate_dirs = [
        dbt_project_path / "models",
        dbt_project_path / "macros",
        dbt_project_path / "seeds",
        dbt_project_path / "snapshots",
        dbt_project_path / "tests",
        dbt_project_path / "analysis",
    ]

    latest = 0.0
    for path in candidates:
        if path.exists():
            latest = max(latest, path.stat().st_mtime)

    for directory in candidate_dirs:
        if not directory.exists():
            continue
        for file_path in directory.rglob("*"):
            if file_path.is_file():
                latest = max(latest, file_path.stat().st_mtime)

    return latest


def ensure_dbt_manifest(dbt_project_path: Path, profiles_path: Path) -> bool:
    """Ensure a valid dbt manifest exists for the project; True when present after checks/parse."""
    manifest_path = dbt_project_path / "target" / "manifest.json"
    ensure_dbt_profile(profiles_path)

    needs_parse = not manifest_path.exists()
    if not needs_parse:
        try:
            needs_parse = _latest_project_mtime(dbt_project_path) > manifest_path.stat().st_mtime
        except OSError:
            needs_parse = True

    if not needs_parse:
        try:
            manifest_payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            needs_parse = True
        else:
            if not isinstance(manifest_payload, Mapping):
                needs_parse = True

    if not needs_parse:
        return True

    try:
        # DBT_PROJECT_DIR doubles as phlo-dbt's own project-dir setting; when a
        # project customizes it, the leaked value overrides dbt's default
        # resolution relative to the (already correct) working directory and
        # parse fails with "Path ... does not exist". Pass the project dir
        # explicitly and strip the variable from the child environment.
        result = subprocess.run(
            [
                "dbt",
                "parse",
                "--project-dir",
                str(dbt_project_path),
                "--profiles-dir",
                str(profiles_path),
            ],
            cwd=str(dbt_project_path),
            capture_output=True,
            text=True,
            timeout=60,
            env={k: v for k, v in os.environ.items() if k != "DBT_PROJECT_DIR"},
        )
    except FileNotFoundError:
        return False
    except subprocess.TimeoutExpired:
        return False

    if result.returncode != 0 or not manifest_path.exists():
        return False

    try:
        manifest_payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False

    return isinstance(manifest_payload, Mapping)


def _emit_dbt_lineage(
    manifest_path: Path,
    translator: DbtSpecTranslator,
    *,
    lineage_emitter: LineageEventEmitter,
    logger: Any,
    reader: Callable[[str], Any],
) -> None:
    """Emit lineage edges derived from a dbt manifest via the lineage emitter."""
    manifest = load_dbt_manifest(manifest_path)
    if manifest is None:
        return

    edges, target_keys = collect_asset_lineage(manifest, translator=translator)

    if edges:
        lineage_emitter.emit_edges(
            edges=edges,
            asset_keys=target_keys,
            metadata={"source": "dbt", "manifest_path": str(manifest_path)},
            operation_id=f"manifest:{manifest_path}",
        )


class DbtTransformer(BaseTransformer):
    """Phlo Transformer implementation for dbt.

    Encapsulates dbt execution logic and Phlo event emission. This class
    handles:
    - dbt command execution (build, compile, docs generate)
    - Profile and target management
    - Partition-aware execution
    - Event emission for lineage, telemetry, and transform tracking
    - Result parsing and transformation result creation

    The transformer integrates with Phlo's hook system to emit events for
    lineage tracking, performance metrics, and execution monitoring. It
    supports both local and containerized dbt execution environments.

    Example:
        >>> transformer = DbtTransformer(
        ...     context=dagster_context,
        ...     logger=logger,
        ...     project_dir=Path("/app/workflows/transforms/dbt"),
        ...     profiles_dir=Path("/app/profiles"),
        ...     target="prod"
        ... )
        >>>
        >>> # Run specific models
        >>> result = transformer.run_transform(
        ...     partition_key="2024-01-01",
        ...     parameters={"select": ["mrt_orders", "mrt_customers"]}
        ... )
        >>>
        >>> # Check results
        >>> if result.status == "success":
        ...     print(f"Built {result.models_built} models")
        ...     print(f"Passed {result.tests_passed} tests")

    """

    def __init__(
        self,
        context: Any,
        logger: Any,
        project_dir: Path,
        profiles_dir: Path,
        target: str = "dev",
        dbt_executable: str = "dbt",
    ):
        """Initialize dbt transformer runtime configuration."""
        super().__init__(context, logger)
        self.project_dir = project_dir
        self.profiles_dir = profiles_dir
        self.target = target
        self.dbt_executable = dbt_executable
        self.build_run_results: Mapping[str, Any] | None = None

    @staticmethod
    def _sanitize_command_args_for_logging(args: list[str]) -> list[str]:
        """Redact sensitive argument values before logging command invocations."""
        sensitive_flags = {
            "--vars",
            "--password",
            "--token",
            "--secret",
            "--key",
            "--access-token",
            "--api-key",
        }
        sensitive_prefixes = tuple(f"{flag}=" for flag in sensitive_flags)
        redacted_args: list[str] = []
        redact_next = False

        for arg in args:
            if redact_next:
                redacted_args.append("<redacted>")
                redact_next = False
                continue

            normalized = arg.lower()
            if normalized in sensitive_flags:
                redacted_args.append(arg)
                redact_next = True
                continue

            if normalized.startswith(sensitive_prefixes):
                key = arg.split("=", 1)[0]
                redacted_args.append(f"{key}=<redacted>")
                continue

            redacted_args.append(arg)

        return redacted_args

    def _run_command(
        self, args: list[str], env: dict[str, str] | None = None
    ) -> subprocess.CompletedProcess:
        """Run a dbt subprocess command in the configured project with optional env overrides."""
        full_env = os.environ.copy()
        if env:
            full_env.update(env)

        log_event(
            self.logger,
            "info",
            "dbt_command_running",
            command_name=self.dbt_executable,
            command_args=self._sanitize_command_args_for_logging(args),
        )
        # DBT_PROJECT_DIR is phlo-dbt's own setting (see ensure_dbt_manifest);
        # leaked into the child process it overrides dbt's project resolution.
        full_env.pop("DBT_PROJECT_DIR", None)

        return subprocess.run(
            [self.dbt_executable] + args,
            cwd=str(self.project_dir),
            env=full_env,
            capture_output=True,
            text=True,
            check=False,
        )

    def run_transform(
        self, partition_key: str | None = None, parameters: dict[str, Any] | None = None
    ) -> TransformationResult:
        """Execute dbt build/docs flow and emit transform telemetry events."""
        parameters = parameters or {}
        self.build_run_results = None
        select_args = parameters.get("select", [])
        exclude_args = parameters.get("exclude", [])
        indirect_selection = parameters.get("indirect_selection")
        skip_build = parameters.get("skip_build", False)
        ensure_dbt_profile(self.profiles_dir, runtime=self.context, target=self.target)

        build_args = [
            "build",
            "--profiles-dir",
            str(self.profiles_dir),
            "--target",
            self.target,
        ]

        if select_args:
            build_args.append("--select")
            build_args.extend(select_args)

        if exclude_args:
            build_args.append("--exclude")
            build_args.extend(exclude_args)

        if indirect_selection:
            build_args.extend(["--indirect-selection", str(indirect_selection)])

        if partition_key:
            build_args.extend(["--vars", f'{{"partition_date_str": "{partition_key}"}}'])
            log_event(
                self.logger,
                "info",
                "dbt_partition_execution_started",
                partition_key=partition_key,
            )

        # Model names for event context are approximated from --select; the
        # resolved dbt selection is not known before the build runs.
        model_names = select_args if select_args else ["all"]
        run_id = getattr(self.context, "run_id", None)
        asset_key = getattr(self.context, "asset_key", None)
        resolved_asset_key = None
        if asset_key is not None:
            if hasattr(asset_key, "to_user_string"):
                resolved_asset_key = str(asset_key.to_user_string())
            else:
                resolved_asset_key = str(asset_key)
        correlation = HookCorrelation(
            run_id=run_id,
            asset_key=resolved_asset_key,
            partition_key=partition_key,
            job_name=getattr(self.context, "job_name", None),
        )

        emitter = TransformEventEmitter(
            TransformEventContext(
                tool="dbt",
                project_dir=str(self.project_dir),
                target=self.target,
                partition_key=partition_key,
                asset_key=resolved_asset_key,
                run_id=run_id,
                model_names=model_names,
                tags={"tool": "dbt"},
                correlation=correlation,
            )
        )
        telemetry = TelemetryEventEmitter(
            TelemetryEventContext(
                tags={"tool": "dbt", "target": self.target},
                correlation=correlation,
            )
        )
        lineage = LineageEventEmitter(
            LineageEventContext(
                tags={"tool": "dbt", "target": self.target}, correlation=correlation
            )
        )

        start_time = time.time()
        elapsed = 0.0
        result_stdout = ""
        if not skip_build:
            emitter.emit_start()

        try:
            # 1. Run dbt build (unless skipped)
            if not skip_build:
                result = self._run_command(build_args)
                result_stdout = result.stdout
                self.build_run_results = _read_run_results(
                    self.project_dir / "target" / "run_results.json"
                )

                if result.returncode != 0:
                    raise RuntimeError(
                        f"dbt build failed: {result.stderr}\nSTDOUT: {result.stdout}"
                    )

                elapsed = time.time() - start_time

                # 2. Emit success metrics.
                emitter.emit_end(status="success", metrics={"dbt_args": build_args})
                telemetry.emit_metric(
                    name="transform.duration_seconds",
                    value=elapsed,
                    unit="seconds",
                    payload={"models": model_names},
                )

            # 3. Emit lineage from the manifest left by the build.
            manifest_path = self.project_dir / "target" / "manifest.json"
            translator = DbtSpecTranslator()

            _emit_dbt_lineage(
                manifest_path,
                translator,
                lineage_emitter=lineage,
                logger=self.logger,
                reader=json.loads,
            )

            # 4. Generate Docs. On by default to preserve legacy behavior;
            # a docs failure must not fail the transform, so its result is ignored.
            if parameters.get("generate_docs", True):
                docs_args = [
                    "docs",
                    "generate",
                    "--profiles-dir",
                    str(self.profiles_dir),
                    "--target",
                    self.target,
                ]
                self._run_command(docs_args)

            if skip_build:
                elapsed = time.time() - start_time

            summary = _parse_dbt_summary(result_stdout)
            counts_source = "skip_build"
            counts = {
                "models_built": 0,
                "models_failed": 0,
                "tests_passed": 0,
                "tests_failed": 0,
            }
            if not skip_build:
                counts_source = "run_results"
                counts = (
                    _parse_run_results_counts(self.build_run_results)
                    if self.build_run_results
                    else None
                )
                counts = counts or {
                    "models_built": -1,
                    "models_failed": -1,
                    "tests_passed": -1,
                    "tests_failed": -1,
                }
                if counts["models_built"] < 0:
                    counts_source = "summary_only_combined"

            return TransformationResult(
                status="success",
                models_built=counts["models_built"],
                models_failed=counts["models_failed"],
                tests_passed=counts["tests_passed"],
                tests_failed=counts["tests_failed"],
                metadata={
                    "total_elapsed_seconds": elapsed,
                    "dbt_output": result_stdout,
                    "dbt_summary": summary,
                    "counts_source": counts_source,
                },
            )

        except Exception as exc:
            elapsed = time.time() - start_time
            emitter.emit_end(status="failure", error=str(exc))
            telemetry.emit_log(
                name="transform.failure",
                level="error",
                payload={
                    "error": str(exc),
                    "elapsed_seconds": elapsed,
                    "models": model_names,
                },
            )
            return TransformationResult(
                status="failure",
                models_built=0,
                models_failed=0,
                tests_passed=0,
                tests_failed=0,
                metadata={"error": str(exc)},
                error=str(exc),
            )
