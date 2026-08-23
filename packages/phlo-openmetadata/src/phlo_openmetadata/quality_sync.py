"""Quality check synchronization to OpenMetadata.

Maps quality checks from @phlo_quality decorator and dbt tests
to OpenMetadata test definitions and publishes results.

This module provides the mapping layer between Phlo's quality framework
and OpenMetadata's data quality testing infrastructure.

Example:
    >>> from phlo_openmetadata.quality_sync import QualityCheckPublisher
    >>> from phlo_openmetadata import OpenMetadataClient
    >>> publisher = QualityCheckPublisher(client)
    >>> publisher.publish_test_definitions(checks, table_fqn)
    {'created': 3, 'failed': 0}

"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Optional, TypeAlias, TypeVar

from phlo.logging import get_logger

# phlo_pandera is an optional dependency. When it is absent, the stub classes
# below keep this module importable and every isinstance() dispatch against
# them simply returns False, degrading mapped checks to OpenMetadata's
# generic customCheck.
try:
    from phlo_pandera.checks import (
        CountCheck as _CountCheck,
        FreshnessCheck as _FreshnessCheck,
        NullCheck as _NullCheck,
        QualityCheckResult as _QualityCheckResult,
        RangeCheck as _RangeCheck,
        UniqueCheck as _UniqueCheck,
    )
    from phlo_pandera.checks_extra import CustomSQLCheck as _CustomSQLCheck
except Exception:  # noqa: BLE001 - optional dependency for quality sync

    class _CountCheck:
        pass

    class _FreshnessCheck:
        pass

    class _NullCheck:
        pass

    class _RangeCheck:
        pass

    class _UniqueCheck:
        pass

    class _CustomSQLCheck:
        pass

    _QualityCheckResult: TypeAlias = Any

CountCheck = _CountCheck
FreshnessCheck = _FreshnessCheck
NullCheck = _NullCheck
RangeCheck = _RangeCheck
UniqueCheck = _UniqueCheck
CustomSQLCheck = _CustomSQLCheck
QualityCheckResult = _QualityCheckResult

if TYPE_CHECKING:
    from phlo_openmetadata.openmetadata import OpenMetadataClient

T = TypeVar("T")

logger = get_logger(__name__)


def _publish_items(
    items: list[T],
    publish_fn: Callable[[T], None],
    item_name_fn: Callable[[T], str],
    context: str,
) -> dict[str, int]:
    """Publish each item through publish_fn, tracking created and failed counts.

    publish_fn is expected to raise on failure; failures are logged with the
    item's display name and counted instead of aborting the loop. Returns
    {'created': n, 'failed': m}.
    """
    stats = {"created": 0, "failed": 0}
    for item in items:
        try:
            publish_fn(item)
            logger.info(
                "openmetadata_publish_item_succeeded",
                context=context,
                item_name=item_name_fn(item),
            )
            stats["created"] += 1
        except Exception as exc:
            logger.error(
                "openmetadata_publish_item_failed",
                context=context,
                item_name=item_name_fn(item),
                error=str(exc),
            )
            stats["failed"] += 1
    return stats


class QualityCheckMapper:
    """Maps Phlo quality checks to OpenMetadata test definitions and test cases.

    Handles parameter mapping for NullCheck, RangeCheck, UniqueCheck,
    CountCheck, FreshnessCheck, and CustomSQLCheck. CHECK_TYPE_MAP translates
    check type names to OpenMetadata test types; unknown types fall back to
    customCheck.

    Example:
        >>> from phlo_pandera.checks import NullCheck
        >>> check = NullCheck(columns=["email"])
        >>> test_def = QualityCheckMapper.map_check_to_openmetadata_test_definition(
        ...     check, "service.db.schema.table"
        ... )

    """

    # Mapping of quality check types to OpenMetadata test types
    CHECK_TYPE_MAP = {
        "NullCheck": "nullCheck",
        "RangeCheck": "rangeCheck",
        "UniqueCheck": "uniqueCheck",
        "CountCheck": "countCheck",
        "FreshnessCheck": "freshnessCheck",
        "SchemaCheck": "schemaCheck",
        "CustomSQLCheck": "customSQLCheck",
    }

    @classmethod
    def map_check_to_openmetadata_test_definition(
        cls,
        check: Any,  # Union of quality check classes
        table_fqn: str,
    ) -> dict[str, Any]:
        """Convert a quality check instance to OpenMetadata test definition format."""
        check_type = type(check).__name__
        om_test_type = cls.CHECK_TYPE_MAP.get(check_type, "customCheck")

        # Get human-readable test name
        test_name = cls._get_test_name(check)

        return {
            "name": test_name,
            "displayName": test_name,
            "description": cls._get_test_description(check),
            "entityType": cls._get_entity_type(check),
            "parameterDefinition": cls._get_parameter_definition(check),
            "testPlatforms": ["OpenMetadata"],
            "testType": om_test_type,
        }

    @classmethod
    def map_check_to_test_case(
        cls,
        check: Any,
        table_fqn: str,
        test_suite_name: Optional[str] = None,
    ) -> dict[str, Any]:
        """Convert a quality check to OpenMetadata test case format.

        When test_suite_name is omitted the suite name is derived from the
        table's last name segment.
        """
        test_name = cls._get_test_name(check)

        if not test_suite_name:
            # Create suite name from table name
            table_name = table_fqn.split(".")[-1]
            test_suite_name = f"{table_name}_quality_suite"

        test_suite_name = cls._sanitize_name(test_suite_name)
        test_case_name = f"{cls._sanitize_name(table_fqn)}_{cls._sanitize_name(test_name)}"

        return {
            "name": test_case_name,
            "entityLink": cls._get_entity_link(check, table_fqn),
            "testDefinition": {
                "name": cls._sanitize_name(test_name),
                "type": "testDefinition",
            },
            "testSuite": {
                "name": test_suite_name,
                "type": "testSuite",
            },
            "parameterValues": cls._get_parameter_values(check),
            "description": cls._get_test_description(check),
        }

    @classmethod
    def map_check_result_to_test_result(
        cls,
        check_result: QualityCheckResult,
        test_case_fqn: str,
        execution_timestamp: Optional[datetime] = None,
    ) -> dict[str, Any]:
        """Convert a check result to OpenMetadata test result format.

        execution_timestamp defaults to the current UTC time. Failure details
        (message and metadata) are included only when the check failed.
        """
        if execution_timestamp is None:
            execution_timestamp = datetime.now(timezone.utc)

        return {
            "result": "Success" if check_result.passed else "Failed",
            "testCaseStatus": "Success" if check_result.passed else "Failed",
            "timestamp": int(execution_timestamp.timestamp() * 1000),
            "result_value": str(check_result.metric_value),
            "failureDetails": {
                "testFailureMessage": check_result.failure_message,
                "testFailureMetadata": json.dumps(check_result.metadata),
            }
            if not check_result.passed
            else None,
        }

    @classmethod
    def map_dbt_test_to_openmetadata(
        cls,
        dbt_test: dict[str, Any],
        table_fqn: str,
    ) -> dict[str, Any]:
        """Convert a dbt manifest test entry to OpenMetadata test case format."""
        test_name = dbt_test.get("name", "unknown_test")
        test_type = (
            dbt_test.get("type") or dbt_test.get("test_metadata", {}).get("name") or "unknown"
        )

        test_def_name = cls._sanitize_name(f"dbt_{test_type}")

        return {
            "name": f"{cls._sanitize_name(table_fqn)}_dbt_{cls._sanitize_name(test_name)}",
            "entityLink": cls._build_entity_link(table_fqn, None),
            "testDefinition": {
                "name": test_def_name,
                "type": "testDefinition",
            },
            "testSuite": {
                "name": cls._sanitize_name(f"{table_fqn.split('.')[-1]}_dbt_suite"),
                "type": "testSuite",
            },
            "parameterValues": cls._get_dbt_test_parameters(dbt_test),
            "description": dbt_test.get("description"),
        }

    @staticmethod
    def _get_test_name(check: Any) -> str:
        """Return an OpenMetadata-friendly sanitized test name for a check."""
        if isinstance(check, NullCheck):
            cols = "_".join(check.columns)
            return QualityCheckMapper._sanitize_name(f"null_check_{cols}")
        if isinstance(check, RangeCheck):
            return QualityCheckMapper._sanitize_name(f"range_check_{check.column}")
        if isinstance(check, UniqueCheck):
            cols = "_".join(check.columns)
            return QualityCheckMapper._sanitize_name(f"unique_check_{cols}")
        if isinstance(check, CountCheck):
            return "count_check"
        if isinstance(check, FreshnessCheck):
            return QualityCheckMapper._sanitize_name(f"freshness_check_{check.timestamp_column}")
        if isinstance(check, CustomSQLCheck):
            return QualityCheckMapper._sanitize_name(check.name_)
        return QualityCheckMapper._sanitize_name(type(check).__name__.lower())

    @staticmethod
    def _sanitize_name(value: str) -> str:
        """Replace every character outside [A-Za-z0-9_] with an underscore.

        Returns "phlo" when nothing remains after stripping, so the name is
        never empty.
        """
        cleaned = re.sub(r"[^A-Za-z0-9_]", "_", value).strip("_")
        return cleaned or "phlo"

    @staticmethod
    def _build_entity_link(table_fqn: str, column: str | None) -> str:
        """Build an OpenMetadata table entity link, or a column-level link when column is given."""
        if column:
            return f"<#E::table::{table_fqn}::columns::{column}>"
        return f"<#E::table::{table_fqn}>"

    @classmethod
    def _get_entity_link(cls, check: Any, table_fqn: str) -> str:
        """Build an entity link scoped to the check's single target column, or to the table."""
        column: str | None = None
        if isinstance(check, NullCheck) and len(check.columns) == 1:
            column = check.columns[0]
        elif isinstance(check, RangeCheck):
            column = check.column
        elif isinstance(check, FreshnessCheck):
            column = check.timestamp_column
        elif isinstance(check, UniqueCheck) and len(check.columns) == 1:
            column = check.columns[0]
        return cls._build_entity_link(table_fqn, column)

    @staticmethod
    def _get_entity_type(check: Any) -> str:
        """Return 'COLUMN' for column-scoped checks and 'TABLE' otherwise."""
        if isinstance(check, (NullCheck, RangeCheck, FreshnessCheck)):
            return "COLUMN"
        if isinstance(check, UniqueCheck) and len(check.columns) == 1:
            return "COLUMN"
        return "TABLE"

    @staticmethod
    def _get_test_description(check: Any) -> str:
        """Describe in prose what the check asserts."""
        if isinstance(check, NullCheck):
            return f"Check that columns {', '.join(check.columns)} have no null values"
        if isinstance(check, RangeCheck):
            return (
                f"Check that column {check.column} values are between "
                f"{check.min_value} and {check.max_value}"
            )
        if isinstance(check, UniqueCheck):
            return f"Check that columns {', '.join(check.columns)} values are unique"
        if isinstance(check, CountCheck):
            return f"Check that row count is between {check.min_rows} and {check.max_rows}"
        if isinstance(check, FreshnessCheck):
            return (
                f"Check that data is not older than {check.max_age_hours} hours based on "
                f"{check.timestamp_column}"
            )
        if isinstance(check, CustomSQLCheck):
            return f"Custom SQL quality check: {check.name_}"
        return "Quality check"

    @staticmethod
    def _get_parameter_definition(check: Any) -> list[dict[str, Any]]:
        """Return the OpenMetadata parameter definitions for a check type.

        Each entry carries name, dataType, and required.
        """
        if isinstance(check, NullCheck):
            return [
                {"name": "columns", "dataType": "STRING", "required": True},
                {"name": "allow_threshold", "dataType": "NUMBER", "required": False},
            ]
        if isinstance(check, RangeCheck):
            return [
                {"name": "column", "dataType": "STRING", "required": True},
                {"name": "min_value", "dataType": "NUMBER", "required": False},
                {"name": "max_value", "dataType": "NUMBER", "required": False},
            ]
        if isinstance(check, UniqueCheck):
            return [
                {"name": "columns", "dataType": "STRING", "required": True},
            ]
        if isinstance(check, CountCheck):
            return [
                {"name": "min_rows", "dataType": "NUMBER", "required": False},
                {"name": "max_rows", "dataType": "NUMBER", "required": False},
            ]
        if isinstance(check, FreshnessCheck):
            return [
                {"name": "timestamp_column", "dataType": "STRING", "required": True},
                {"name": "max_age_hours", "dataType": "NUMBER", "required": True},
            ]
        if isinstance(check, CustomSQLCheck):
            return [
                {"name": "sql", "dataType": "STRING", "required": True},
            ]
        return []

    @staticmethod
    def _get_parameter_values(check: Any) -> list[dict[str, str]]:
        """Extract a check's configured values as OpenMetadata parameter dicts."""
        params: list[dict[str, str]] = []

        if isinstance(check, NullCheck):
            params.append({"name": "columns", "value": ",".join(check.columns)})
            params.append({"name": "allow_threshold", "value": str(check.allow_threshold)})
        elif isinstance(check, RangeCheck):
            params.append({"name": "column", "value": check.column})
            params.append({"name": "min_value", "value": str(check.min_value)})
            params.append({"name": "max_value", "value": str(check.max_value)})
        elif isinstance(check, UniqueCheck):
            params.append({"name": "columns", "value": ",".join(check.columns)})
        elif isinstance(check, CountCheck):
            if check.min_rows is not None:
                params.append({"name": "min_rows", "value": str(check.min_rows)})
            if check.max_rows is not None:
                params.append({"name": "max_rows", "value": str(check.max_rows)})
        elif isinstance(check, FreshnessCheck):
            params.append({"name": "timestamp_column", "value": check.timestamp_column})
            params.append({"name": "max_age_hours", "value": str(check.max_age_hours)})
        elif isinstance(check, CustomSQLCheck):
            params.append({"name": "sql", "value": check.sql})

        return params

    @staticmethod
    def _get_dbt_test_parameters(dbt_test: dict[str, Any]) -> list[dict[str, str]]:
        """Convert a dbt test's kwargs into OpenMetadata parameter value dicts."""
        params: list[dict[str, str]] = []
        kwargs = dbt_test.get("kwargs") or dbt_test.get("test_metadata", {}).get("kwargs", {})

        for key, value in kwargs.items():
            params.append({"name": key, "value": str(value)})

        return params


class QualityCheckPublisher:
    """Publishes quality checks, cases, and results to OpenMetadata.

    Example:
        >>> publisher = QualityCheckPublisher(om_client)
        >>> publisher.publish_test_definitions(checks, "service.db.schema.table")
        {'created': 5, 'failed': 0}

    """

    def __init__(self, om_client: OpenMetadataClient):
        """Store the client used for all definition, case, and result operations."""
        self.om_client = om_client

    def publish_test_definitions(
        self,
        checks: list[Any],
        table_fqn: str,
    ) -> dict[str, int]:
        """Map each check to an OpenMetadata test definition and create it.

        Returns {'created': n, 'failed': m}; a failed creation is logged, not raised.
        """
        # Pre-map all checks to avoid duplicate mapping
        mapped_defs = [
            (check, QualityCheckMapper.map_check_to_openmetadata_test_definition(check, table_fqn))
            for check in checks
        ]

        def publish(item: tuple[Any, dict[str, Any]]) -> None:
            """Create one OpenMetadata test definition from a mapped check."""
            _check, test_def = item
            self.om_client.create_test_definition(
                test_name=test_def["name"],
                test_type=test_def.get("testType"),
                description=test_def.get("description"),
                entity_type=test_def.get("entityType"),
                parameter_definition=test_def.get("parameterDefinition"),
                test_platforms=test_def.get("testPlatforms"),
            )

        def get_name(item: tuple[Any, dict[str, Any]]) -> str:
            """Return the definition name used for deduplication and reporting."""
            _check, test_def = item
            return test_def["name"]

        return _publish_items(mapped_defs, publish, get_name, "test definition")

    def publish_test_cases(
        self,
        checks: list[Any],
        table_fqn: str,
        test_suite_name: Optional[str] = None,
    ) -> dict[str, int]:
        """Map each check to an OpenMetadata test case and create it.

        Returns {'created': n, 'failed': m}; a failed creation is logged, not raised.
        """
        # Pre-map all checks to avoid duplicate mapping
        mapped_cases = [
            (check, QualityCheckMapper.map_check_to_test_case(check, table_fqn, test_suite_name))
            for check in checks
        ]

        def publish(item: tuple[Any, dict[str, Any]]) -> None:
            """Create one OpenMetadata test case from a mapped check."""
            _check, test_case = item
            self.om_client.create_test_case(
                test_case_name=test_case["name"],
                table_fqn=table_fqn,
                test_definition_name=test_case["testDefinition"]["name"],
                parameters={p["name"]: p["value"] for p in test_case.get("parameterValues", [])},
                description=test_case.get("description"),
                entity_link=test_case.get("entityLink"),
                test_suite_name=test_case.get("testSuite", {}).get("name"),
            )

        def get_name(item: tuple[Any, dict[str, Any]]) -> str:
            """Return the test-case name used for deduplication and reporting."""
            _check, test_case = item
            return test_case["name"]

        return _publish_items(mapped_cases, publish, get_name, "test case")

    def publish_test_results(
        self,
        results: list[dict[str, Any]],
    ) -> dict[str, int]:
        """Publish mapped test results to OpenMetadata.

        Entries missing test_case_fqn or check_result are skipped with a
        warning. Returns {'published': n, 'failed': m}.
        """
        stats = {"published": 0, "failed": 0}

        for result in results:
            try:
                test_case_fqn = result.get("test_case_fqn")
                check_result = result.get("check_result")
                timestamp = result.get("timestamp")

                if not test_case_fqn or not check_result:
                    logger.warning("invalid_test_result_skipped", result=result)
                    continue

                om_result = QualityCheckMapper.map_check_result_to_test_result(
                    check_result, test_case_fqn, timestamp
                )

                self.om_client.publish_test_result(
                    test_case_fqn=test_case_fqn,
                    result=om_result["result"],
                    test_execution_date=datetime.fromtimestamp(om_result["timestamp"] / 1000),
                    result_value=om_result.get("result_value"),
                )

                logger.info("test_result_published", test_case_fqn=test_case_fqn)
                stats["published"] += 1

            except Exception as exc:
                logger.error("test_result_publish_failed", error=str(exc))
                stats["failed"] += 1

        return stats

    def publish_dbt_tests(
        self,
        dbt_tests: list[dict[str, Any]],
        table_fqn: str,
    ) -> dict[str, int]:
        """Map dbt manifest tests to OpenMetadata test cases and create them.

        Returns {'created': n, 'failed': m}.
        """
        # Pre-map all dbt tests to avoid duplicate mapping
        mapped_tests = [
            (dbt_test, QualityCheckMapper.map_dbt_test_to_openmetadata(dbt_test, table_fqn))
            for dbt_test in dbt_tests
        ]

        def publish(item: tuple[dict[str, Any], dict[str, Any]]) -> None:
            """Create one OpenMetadata test case from a mapped dbt test."""
            _dbt_test, test_case = item
            self.om_client.create_test_case(
                test_case_name=test_case["name"],
                table_fqn=table_fqn,
                test_definition_name=test_case["testDefinition"]["name"],
                parameters={p["name"]: p["value"] for p in test_case.get("parameterValues", [])},
                description=test_case.get("description"),
                entity_link=test_case.get("entityLink"),
                test_suite_name=test_case.get("testSuite", {}).get("name"),
            )

        def get_name(item: tuple[dict[str, Any], dict[str, Any]]) -> str:
            """Return the dbt-derived test-case name for deduplication."""
            _dbt_test, test_case = item
            return test_case["name"]

        return _publish_items(mapped_tests, publish, get_name, "dbt test case")
