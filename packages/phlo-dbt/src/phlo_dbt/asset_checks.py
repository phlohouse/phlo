"""dbt test result extraction and conversion to Phlo check results.

This module provides utilities for extracting dbt test results from run results
and converting them into Phlo's CheckResult format. It handles test type detection,
severity mapping, and metadata extraction for quality checks.

Example:
    >>> from phlo_dbt.asset_checks import extract_dbt_asset_checks
    >>> from phlo_dbt.translator import DbtSpecTranslator
    >>> checks = extract_dbt_asset_checks(
    ...     run_results=run_results_data,
    ...     manifest=manifest_data,
    ...     translator=DbtSpecTranslator(),
    ...     partition_key="2024-01-01"
    ... )
    >>> for check in checks:
    ...     print(f"{check.check_name}: {'PASS' if check.passed else 'FAIL'}")

"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from phlo.capabilities import AssetCheckSpec, CheckResult
from phlo.logging import get_logger
from phlo_dbt.translator import DbtSpecTranslator

logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class _CheckContract:
    """Minimal check metadata contract used for dbt test outputs.

    Internal dataclass for normalizing dbt test result data before converting to
    Phlo CheckResult objects. Provides a consistent interface for test metadata
    including failure counts, SQL queries, and sample data. ``source`` identifies
    the origin (always "dbt" for dbt tests); ``failed_count`` counts rows that
    failed the test; ``partition_key`` optionally identifies the test run's
    partition alongside the optional ``total_count``; ``query_or_sql`` carries the
    compiled SQL used for the test; ``repro_sql`` is reproducible SQL for
    debugging (with LIMIT); ``sample`` holds sample failed rows for diagnostics.

    Example:
        >>> contract = _CheckContract(
        ...     source="dbt",
        ...     failed_count=5,
        ...     partition_key="2024-01-01",
        ...     query_or_sql="SELECT * FROM table WHERE condition"
        ... )
        >>> metadata = contract.to_metadata()
    """

    source: str
    failed_count: int
    partition_key: str | None = None
    total_count: int | None = None
    query_or_sql: str | None = None
    repro_sql: str | None = None
    sample: list[Any] | None = None

    def to_metadata(self) -> dict[str, Any]:
        """Export contract fields as metadata dict.

        Converts the check contract into a metadata dictionary suitable for inclusion
        in Phlo CheckResult objects. Only includes non-None values to keep metadata
        clean.
        """
        metadata: dict[str, Any] = {
            "source": self.source,
            "failed_count": self.failed_count,
        }
        if self.partition_key is not None:
            metadata["partition_key"] = self.partition_key
        if self.total_count is not None:
            metadata["total_count"] = self.total_count
        if self.query_or_sql is not None:
            metadata["query_or_sql"] = self.query_or_sql
        if self.repro_sql is not None:
            metadata["repro_sql"] = self.repro_sql
        if self.sample is not None:
            metadata["sample"] = self.sample[:20]
        return metadata


def _sanitize_name(value: str) -> str:
    """Normalize a string into a Dagster-safe identifier segment."""
    cleaned = "".join(char if char.isalnum() else "_" for char in value.strip())
    cleaned = "_".join(part for part in cleaned.split("_") if part)
    return cleaned or "unknown"


def _dbt_check_name(test_type: str, target: str, identity: str) -> str:
    """Build a stable, target-scoped Dagster check name for a dbt test."""
    return "__".join(
        ("dbt", _sanitize_name(test_type), _sanitize_name(target), _sanitize_name(identity))
    )


def _severity_for_dbt_test(*, test_type: str | None, tags: Iterable[str] | None) -> str:
    """Map dbt test metadata to a Phlo severity label.

    Explicit tags take precedence over test-type defaults: ``blocking`` forces
    ``error`` and ``warn``/``anomaly`` forces ``warn``. Without tags, structural
    integrity tests (not_null, unique, relationships) default to ``error`` and
    every other test type warns.
    """
    warn_tags = {"warn", "anomaly"}
    blocking_tags = {"blocking"}
    blocking_test_types = {"not_null", "unique", "relationships"}
    normalized_tags = {tag.strip().lower() for tag in (tags or []) if tag and tag.strip()}
    normalized_test_type = (test_type or "").strip().lower()
    if normalized_tags & blocking_tags:
        return "error"
    if normalized_tags & warn_tags:
        return "warn"
    if normalized_test_type in blocking_test_types:
        return "error"
    return "warn"


def extract_dbt_asset_checks(
    run_results: Mapping[str, Any],
    manifest: Mapping[str, Any],
    *,
    translator: DbtSpecTranslator,
    partition_key: str | None,
    max_sql_chars: int = 100_000,
) -> list[CheckResult]:
    """Convert dbt run results into Phlo check results.

    Extracts test results from dbt run_results.json and manifest.json files,
    converting them into Phlo CheckResult objects. Handles various test types,
    severity mapping, and SQL extraction for quality reporting. ``run_results``
    and ``manifest`` are the parsed dbt payloads, ``translator`` resolves target
    asset keys from dbt nodes, ``partition_key`` optionally tags emitted checks
    (e.g., "2024-01-01"), and ``max_sql_chars`` bounds the SQL length included in
    metadata (default 100,000). Exceptions during asset key translation may occur
    but are logged, not propagated. Returns CheckResult objects derived from dbt
    test nodes.

    Example:
        >>> import json
        >>> from pathlib import Path
        >>> from phlo_dbt.translator import DbtSpecTranslator
        >>>
        >>> run_results = json.loads(Path("target/run_results.json").read_text())
        >>> manifest = json.loads(Path("target/manifest.json").read_text())
        >>>
        >>> checks = extract_dbt_asset_checks(
        ...     run_results=run_results,
        ...     manifest=manifest,
        ...     translator=DbtSpecTranslator(),
        ...     partition_key="2024-01-01"
        ... )
        >>>
        >>> passed = sum(1 for c in checks if c.passed)
        >>> failed = len(checks) - passed
        >>> print(f"Tests: {passed} passed, {failed} failed")
    """
    checks: list[CheckResult] = []
    result_entries = run_results.get("results", []) or []
    logger.info(
        "dbt_asset_checks_extraction_started",
        result_count=len(result_entries) if isinstance(result_entries, list) else 0,
        partition_key=partition_key,
    )

    for result in result_entries:
        unique_id = result.get("unique_id")
        if not isinstance(unique_id, str) or not unique_id.startswith("test."):
            continue

        status = (result.get("status") or "").strip().lower()
        passed = status in {"pass", "skipped", "skip"}

        test_props = _manifest_nodes(manifest).get(unique_id, {})
        if not isinstance(test_props, Mapping):
            continue
        resolved = _resolve_dbt_test(
            manifest, unique_id, test_props, translator, dependency_fallback=result
        )
        if resolved is None:
            continue

        tags = _dbt_tags(test_props)
        failures = _int_or_none(result.get("failures"))
        failed_count = 0 if passed else (failures if failures is not None else 1)

        severity: str | None
        if passed:
            severity = None
        elif status == "fail":
            severity_label = _severity_for_dbt_test(test_type=resolved.test_type, tags=tags)
            severity = severity_label or "error"
        else:
            severity = "error"

        compiled_sql = _dbt_compiled_sql(test_props)
        compiled_sql = _truncate(compiled_sql, max_chars=max_sql_chars)

        contract = _CheckContract(
            source="dbt",
            partition_key=partition_key,
            failed_count=failed_count,
            total_count=None,
            query_or_sql=compiled_sql,
            repro_sql=_repro_sql_from_sql(compiled_sql),
            sample=_sample_for_result(result, passed=passed),
        )

        metadata: dict[str, Any] = {
            **contract.to_metadata(),
            "status": status or "unknown",
            "test_unique_id": unique_id,
            "test_type": resolved.test_type,
            "target_unique_id": resolved.target_unique_id,
            "target_name": resolved.target_name,
        }
        if tags:
            metadata["tags"] = sorted(tags)
        if failures is not None:
            metadata["failed_rows"] = failures

        checks.append(
            CheckResult(
                asset_key=resolved.asset_key,
                check_name=resolved.check_name,
                passed=passed,
                severity=severity,
                metadata=metadata,
            )
        )

    logger.info(
        "dbt_asset_checks_extraction_finished",
        check_count=len(checks),
        partition_key=partition_key,
    )
    return checks


def dbt_asset_check_specs(
    manifest: Mapping[str, Any], *, translator: DbtSpecTranslator
) -> list[AssetCheckSpec]:
    """Build Dagster-declarable check specs for dbt tests in a manifest.

    The dbt asset runner emits the corresponding ``CheckResult`` values after each
    build. Declaring these specs during discovery lets orchestrators accept those
    runtime results as native asset-check events. ``manifest`` is the parsed dbt
    manifest payload and ``translator`` resolves target asset keys from dbt nodes.
    Returns check specifications for dbt tests with resolvable target assets.
    """
    nodes = _manifest_nodes(manifest)
    if not isinstance(nodes, Mapping):
        return []

    specs: list[AssetCheckSpec] = []
    seen: set[tuple[str, str]] = set()
    for unique_id, test_props in nodes.items():
        if not isinstance(unique_id, str) or not unique_id.startswith("test."):
            continue
        if not isinstance(test_props, Mapping):
            continue

        resolved = _resolve_dbt_test(manifest, unique_id, test_props, translator)
        if resolved is None:
            continue
        identity = (resolved.asset_key, resolved.check_name)
        if identity in seen:
            logger.warning(
                "dbt_asset_check_spec_duplicate",
                test_unique_id=unique_id,
                asset_key=resolved.asset_key,
                check_name=resolved.check_name,
            )
            continue
        seen.add(identity)
        severity = _severity_for_dbt_test(test_type=resolved.test_type, tags=_dbt_tags(test_props))
        specs.append(
            AssetCheckSpec(
                name=resolved.check_name,
                asset_key=resolved.asset_key,
                blocking=severity == "error",
                severity=severity,
            )
        )

    return specs


def dbt_asset_check_names(
    manifest: Mapping[str, Any], *, asset_key: str, translator: DbtSpecTranslator
) -> list[str]:
    """Return dbt-selectable test names owned by a dbt asset.

    Ownership uses the same ``attached_node``-first resolution as declared
    asset checks, so relationship tests belong to their attached model rather
    than every model listed in ``depends_on.nodes``.
    """
    test_names: list[str] = []
    for unique_id, test_props in _manifest_nodes(manifest).items():
        if not isinstance(unique_id, str) or not unique_id.startswith("test."):
            continue
        if not isinstance(test_props, Mapping):
            continue
        resolved = _resolve_dbt_test(manifest, unique_id, test_props, translator)
        name = test_props.get("name")
        if (
            resolved is not None
            and resolved.asset_key == asset_key
            and isinstance(name, str)
            and name
        ):
            test_names.append(name)
    return test_names


@dataclass(frozen=True, slots=True)
class _ResolvedDbtTest:
    """Shared target and identity contract for a manifest dbt test."""

    asset_key: str
    check_name: str
    target_name: str
    target_unique_id: str
    test_type: str


def _manifest_nodes(manifest: Mapping[str, Any]) -> Mapping[str, Any]:
    """Return the manifest node mapping when it has the expected shape."""
    nodes = manifest.get("nodes") or {}
    return nodes if isinstance(nodes, Mapping) else {}


def _resolve_dbt_test(
    manifest: Mapping[str, Any],
    test_unique_id: str,
    test_props: Mapping[str, Any],
    translator: DbtSpecTranslator,
    *,
    dependency_fallback: Mapping[str, Any] | None = None,
) -> _ResolvedDbtTest | None:
    """Resolve one dbt test's owning asset and stable Dagster identity.

    dbt's ``attached_node`` identifies the model under test for relationship
    checks, where ``depends_on.nodes`` also contains the referenced model.
    Older manifests without ``attached_node`` retain the dependency fallback.
    Runtime extraction can additionally use the result's dependencies when an
    older manifest omits them from its test node.
    """
    nodes = _manifest_nodes(manifest)
    attached_node = test_props.get("attached_node")
    target_unique_id = attached_node if isinstance(attached_node, str) else None
    if target_unique_id not in nodes:
        depends_on = test_props.get("depends_on") or {}
        if not isinstance(depends_on, Mapping) and dependency_fallback is not None:
            depends_on = dependency_fallback.get("depends_on") or {}
        elif not depends_on and dependency_fallback is not None:
            depends_on = dependency_fallback.get("depends_on") or {}
        depends_nodes = depends_on.get("nodes") if isinstance(depends_on, Mapping) else []
        target_unique_id = (
            _first_str(depends_nodes, prefix="model.")
            if isinstance(depends_nodes, Iterable)
            else None
        ) or (_first_str(depends_nodes) if isinstance(depends_nodes, Iterable) else None)
    if target_unique_id is None:
        return None
    target_props = nodes.get(target_unique_id)
    if not isinstance(target_props, Mapping):
        return None
    try:
        asset_key = translator.get_asset_key(target_props)
    except Exception:
        logger.exception(
            "dbt_asset_check_target_translate_failed",
            test_unique_id=test_unique_id,
            target_unique_id=target_unique_id,
        )
        return None

    test_type = _dbt_test_type(test_props, fallback_unique_id=test_unique_id)
    target_name = str(
        target_props.get("name") or target_props.get("alias") or target_unique_id.split(".")[-1]
    )
    node_name = test_props.get("name")
    identity = (
        node_name.strip() if isinstance(node_name, str) and node_name.strip() else test_unique_id
    )
    return _ResolvedDbtTest(
        asset_key=asset_key,
        check_name=_dbt_check_name(test_type, target_name, identity),
        target_name=target_name,
        target_unique_id=target_unique_id,
        test_type=test_type,
    )


def _first_str(values: Iterable[object], prefix: str | None = None) -> str | None:
    """Return the first string entry in ``values``, optionally filtered by
    ``prefix``; ``None`` when nothing matches.
    """
    for value in values:
        if not isinstance(value, str):
            continue
        if prefix is not None and not value.startswith(prefix):
            continue
        return value
    return None


def _dbt_test_type(test_props: Mapping[str, Any], *, fallback_unique_id: str) -> str:
    """Infer the dbt test type label from node properties.

    Returns the normalized test type string from ``test_props``, falling back to
    ``fallback_unique_id`` when no explicit type is present.
    """
    test_metadata = test_props.get("test_metadata")
    if isinstance(test_metadata, Mapping):
        name = test_metadata.get("name")
        if isinstance(name, str) and name.strip():
            return name.strip()
    resource_type = test_props.get("resource_type")
    if isinstance(resource_type, str) and resource_type.strip():
        return resource_type.strip()
    return fallback_unique_id.split(".")[-1]


def _dbt_tags(test_props: Mapping[str, Any]) -> set[str]:
    """Extract unique, trimmed, non-empty dbt tags from ``test_props``."""
    tags = test_props.get("tags")
    if not isinstance(tags, list):
        return set()
    normalized: set[str] = set()
    for tag in tags:
        if isinstance(tag, str) and tag.strip():
            normalized.add(tag.strip())
    return normalized


def _dbt_compiled_sql(test_props: Mapping[str, Any]) -> str | None:
    """Return compiled SQL text from known dbt node keys in ``test_props``, or
    ``None`` when unavailable.
    """
    for key in ("compiled_code", "compiled_sql", "raw_code"):
        value = test_props.get(key)
        if isinstance(value, str) and value.strip():
            return value
    return None


def _sample_for_result(result: Mapping[str, Any], *, passed: bool) -> list[dict[str, Any]]:
    """Build sample metadata rows for quality diagnostics from a dbt ``result`` entry."""
    if passed:
        return []
    sample: dict[str, Any] = {}
    message = result.get("message")
    if isinstance(message, str) and message.strip():
        sample["message"] = message
    failures = _int_or_none(result.get("failures"))
    if failures is not None:
        sample["failed_rows"] = failures
    return [sample] if sample else []


def _truncate(value: str | None, *, max_chars: int) -> str | None:
    """Trim long strings to a bounded size.

    Returns ``value`` unchanged when short enough, truncated to fit ``max_chars``
    otherwise.
    """
    if value is None:
        return None
    if len(value) <= max_chars:
        return value
    return value[: max_chars - 20] + "\n-- [truncated]"


def _repro_sql_from_sql(sql: str | None) -> str | None:
    """Create a reproducible SQL snippet for debugging failed checks.

    Appends a row limit to ``sql`` when missing; returns ``None`` when no SQL.
    """
    if sql is None:
        return None
    trimmed = sql.strip()
    if not trimmed:
        return None
    lower = trimmed.lower()
    if "limit" in lower:
        return trimmed
    return f"{trimmed}\nLIMIT 500"


def _int_or_none(value: object) -> int | None:
    """Safely coerce ``value`` to ``int``, returning ``None`` when coercion fails."""
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    try:
        return int(str(value))
    except (ValueError, TypeError):
        return None
