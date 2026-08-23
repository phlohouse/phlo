"""Severity mapping utilities for quality checks.

Maps quality-check results to severities consumed by orchestration: omitted
for passes, ``warn`` for non-blocking failures, and ``error`` for blocking
failures. Pandera schema contract checks always map to ``error``; quality
checks use configurable warn thresholds; dbt tests use tags and test types.

Example:
    ```python
    from phlo_pandera.severity import severity_for_quality_check

    # Determine severity based on failure rate
    severity = severity_for_quality_check(
        passed=False,
        failure_fraction=0.15,  # 15% of rows failed
        warn_threshold=0.10,     # Warn threshold is 10%
    )
    # Returns: "error" (exceeds warn threshold)

    # Below warn threshold
    severity = severity_for_quality_check(
        passed=False,
        failure_fraction=0.05,  # 5% of rows failed
        warn_threshold=0.10,     # Warn threshold is 10%
    )
    # Returns: "warn"
    ```

See Also:
    - ``contract.py``: Quality check contract definitions
    - ``decorator.py``: ``@phlo_pandera`` which uses these severity mappings
"""

from __future__ import annotations

from collections.abc import Iterable

DBT_WARN_TAGS = {"warn", "anomaly"}
DBT_BLOCKING_TAGS = {"blocking"}
DBT_BLOCKING_TEST_TYPES = {"not_null", "unique", "relationships"}


def normalize_dbt_tags(tags: Iterable[str] | None) -> set[str]:
    """Normalize dbt tags to lowercase, stripped, non-empty strings.

    Example:
        ```python
        normalize_dbt_tags(["WARN", " blocking ", ""])
        # Returns: {"warn", "blocking"}

        normalize_dbt_tags(None)
        # Returns: set()
        ```
    """

    if tags is None:
        return set()
    return {tag.strip().lower() for tag in tags if tag and tag.strip()}


def severity_for_pandera_contract(*, passed: bool) -> str | None:
    """Map a Pandera contract evaluation to severity: None on pass, else error.

    Pandera schema contract failures mean the data does not conform to the
    expected schema, so they are always blocking.

    Example:
        ```python
        severity_for_pandera_contract(passed=True)
        # Returns: None

        severity_for_pandera_contract(passed=False)
        # Returns: "error"
        ```
    """

    return None if passed else "error"


def severity_for_quality_check(
    *, passed: bool, failure_fraction: float, warn_threshold: float
) -> str | None:
    """Map a quality-check outcome to None, warn, or error by failure fraction.

    A pass returns None. A failure at or below a positive ``warn_threshold``
    returns ``warn``; any other failure returns ``error``.

    Example:
        ```python
        # Passed check
        severity_for_quality_check(passed=True, failure_fraction=0.0, warn_threshold=0.1)
        # Returns: None

        # Failed but within warn threshold
        severity_for_quality_check(passed=False, failure_fraction=0.05, warn_threshold=0.1)
        # Returns: "warn"

        # Failed and exceeds warn threshold
        severity_for_quality_check(passed=False, failure_fraction=0.15, warn_threshold=0.1)
        # Returns: "error"
        ```
    """

    if passed:
        return None
    if failure_fraction <= 0:
        return "error"
    if warn_threshold > 0 and failure_fraction <= warn_threshold:
        return "warn"
    return "error"


def severity_for_dbt_test(*, test_type: str | None, tags: Iterable[str] | None) -> str:
    """Map dbt test type and tags to severity.

    ``tag:blocking`` forces ``error``; ``tag:warn``/``tag:anomaly`` force
    ``warn``. Without tag overrides, blocking test types (``not_null``,
    ``unique``, ``relationships``) yield ``error``; all others yield ``warn``.

    Example:
        ```python
        # Blocking test type
        severity_for_dbt_test(test_type="not_null", tags=[])
        # Returns: "error"

        # Non-blocking test type
        severity_for_dbt_test(test_type="accepted_values", tags=[])
        # Returns: "warn"

        # Tag override to blocking
        severity_for_dbt_test(test_type="accepted_values", tags=["blocking"])
        # Returns: "error"

        # Tag override to warn
        severity_for_dbt_test(test_type="not_null", tags=["warn"])
        # Returns: "warn"
        ```
    """

    normalized_tags = normalize_dbt_tags(tags)
    normalized_test_type = (test_type or "").strip().lower()

    if normalized_tags & DBT_BLOCKING_TAGS:
        return "error"
    if normalized_tags & DBT_WARN_TAGS:
        return "warn"
    if normalized_test_type in DBT_BLOCKING_TEST_TYPES:
        return "error"
    return "warn"
