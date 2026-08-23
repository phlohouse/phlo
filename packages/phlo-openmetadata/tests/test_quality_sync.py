"""Tests for synchronizing quality checks into OpenMetadata.

Covers mapping each phlo-pandera check type onto an OpenMetadata column or
table test definition with its parameters rendered into the expected form.
"""

from datetime import datetime
from unittest.mock import Mock

import pytest
from phlo_openmetadata.quality_sync import QualityCheckMapper, QualityCheckPublisher

from phlo_pandera.checks import (
    CountCheck,
    FreshnessCheck,
    NullCheck,
    QualityCheckResult,
    RangeCheck,
    UniqueCheck,
)
from phlo_pandera.checks_extra import CustomSQLCheck


class TestQualityCheckMapper:
    """Tests for QualityCheckMapper."""

    def test_map_null_check_to_test_definition(self):
        """Test mapping NullCheck to test definition."""
        check = NullCheck(columns=["id", "timestamp"], allow_threshold=0.0)

        test_def = QualityCheckMapper.map_check_to_openmetadata_test_definition(
            check, "public.users"
        )

        assert test_def["name"] == "null_check_id_timestamp"
        assert test_def["entityType"] == "COLUMN"
        assert "null" in test_def["description"].lower()

    def test_map_range_check_to_test_definition(self):
        """Test mapping RangeCheck to test definition."""
        check = RangeCheck(column="temperature", min_value=-50, max_value=60)

        test_def = QualityCheckMapper.map_check_to_openmetadata_test_definition(
            check, "public.weather"
        )

        assert test_def["name"] == "range_check_temperature"
        assert test_def["entityType"] == "COLUMN"
        assert "-50" in test_def["description"]
        assert "60" in test_def["description"]

    def test_map_freshness_check_to_test_definition(self):
        """Test mapping FreshnessCheck to test definition."""
        check = FreshnessCheck(timestamp_column="created_at", max_age_hours=24)

        test_def = QualityCheckMapper.map_check_to_openmetadata_test_definition(
            check, "public.events"
        )

        assert test_def["name"] == "freshness_check_created_at"
        assert test_def["entityType"] == "COLUMN"
        assert "24" in test_def["description"]

    def test_map_unique_check_to_test_definition(self):
        """Test mapping UniqueCheck to test definition."""
        check = UniqueCheck(columns=["email", "domain"])

        test_def = QualityCheckMapper.map_check_to_openmetadata_test_definition(
            check, "public.accounts"
        )

        assert test_def["name"] == "unique_check_email_domain"
        assert test_def["entityType"] == "TABLE"

    def test_map_count_check_to_test_definition(self):
        """Test mapping CountCheck to test definition."""
        check = CountCheck(min_rows=100, max_rows=10000)

        test_def = QualityCheckMapper.map_check_to_openmetadata_test_definition(
            check, "public.logs"
        )

        assert test_def["name"] == "count_check"
        assert test_def["entityType"] == "TABLE"

    def test_map_custom_sql_check_to_test_definition(self):
        """Test mapping CustomSQLCheck to test definition."""
        check = CustomSQLCheck(
            name_="temp_consistency",
            sql="SELECT (max_temp >= min_temp) FROM weather",
        )

        test_def = QualityCheckMapper.map_check_to_openmetadata_test_definition(
            check, "public.weather"
        )

        assert "temp_consistency" in test_def["name"]
        assert test_def["entityType"] == "TABLE"

    def test_map_check_to_test_case(self):
        """Test mapping check to test case."""
        check = NullCheck(columns=["id"])

        test_case = QualityCheckMapper.map_check_to_test_case(check, "schema.table", "table_suite")

        assert test_case["name"] == "schema_table_null_check_id"
        assert test_case["entityLink"] == "<#E::table::schema.table::columns::id>"
        assert test_case["testSuite"]["name"] == "table_suite"
        assert test_case["testDefinition"]["name"] == "null_check_id"

    def test_map_check_result_to_test_result_passed(self):
        """Test mapping passed check result."""
        result = QualityCheckResult(
            passed=True,
            metric_name="null_check",
            metric_value={"id": 0},
            metadata={"columns_checked": ["id"]},
        )

        om_result = QualityCheckMapper.map_check_result_to_test_result(
            result, "schema.table_null_check_id"
        )

        assert om_result["result"] == "Success"
        assert om_result["testCaseStatus"] == "Success"
        assert om_result["failureDetails"] is None

    def test_map_check_result_to_test_result_failed(self):
        """Test mapping failed check result."""
        result = QualityCheckResult(
            passed=False,
            metric_name="range_check",
            metric_value={"out_of_range": 5},
            metadata={"violation_percentage": 0.05},
            failure_message="Range check failed: 5% out of range",
        )

        om_result = QualityCheckMapper.map_check_result_to_test_result(
            result, "schema.table_range_check"
        )

        assert om_result["result"] == "Failed"
        assert om_result["testCaseStatus"] == "Failed"
        assert om_result["failureDetails"] is not None
        assert "Range check failed" in om_result["failureDetails"]["testFailureMessage"]

    def test_map_check_result_with_custom_timestamp(self):
        """Test mapping result with custom timestamp."""
        timestamp = datetime(2024, 1, 1, 12, 0, 0)
        result = QualityCheckResult(
            passed=True,
            metric_name="count_check",
            metric_value={"row_count": 1000},
        )

        om_result = QualityCheckMapper.map_check_result_to_test_result(
            result, "schema.table_count", timestamp
        )

        expected_ms = int(timestamp.timestamp() * 1000)
        assert om_result["timestamp"] == expected_ms

    def test_map_dbt_test_to_openmetadata(self):
        """Test mapping dbt test to OpenMetadata format."""
        dbt_test = {
            "name": "not_null_id",
            "type": "not_null",
            "description": "User ID must not be null",
            "kwargs": {"column_name": "id"},
        }

        om_test = QualityCheckMapper.map_dbt_test_to_openmetadata(dbt_test, "public.users")

        assert om_test["name"] == "public_users_dbt_not_null_id"
        assert om_test["testDefinition"]["name"] == "dbt_not_null"
        assert len(om_test["parameterValues"]) > 0

    def test_get_test_name_from_check(self):
        """Test getting test name from check."""
        check = NullCheck(columns=["field"])
        name = QualityCheckMapper._get_test_name(check)
        assert name == "null_check_field"

    def test_get_test_description_null_check(self):
        """Test getting description for NullCheck."""
        check = NullCheck(columns=["id", "email"])
        desc = QualityCheckMapper._get_test_description(check)
        assert "null" in desc.lower()

    def test_get_test_description_range_check(self):
        """Test getting description for RangeCheck."""
        check = RangeCheck(column="age", min_value=0, max_value=120)
        desc = QualityCheckMapper._get_test_description(check)
        assert "age" in desc.lower()
        assert "0" in desc
        assert "120" in desc

    def test_get_parameter_definition_null_check(self):
        """Test extracting parameter definitions for NullCheck."""
        check = NullCheck(columns=["id"])
        params = QualityCheckMapper._get_parameter_definition(check)

        assert len(params) > 0
        param_names = [p["name"] for p in params]
        assert "columns" in param_names

    def test_get_parameter_values_null_check(self):
        """Test extracting parameter values from NullCheck."""
        check = NullCheck(columns=["id", "email"], allow_threshold=0.01)
        params = QualityCheckMapper._get_parameter_values(check)

        param_dict = {p["name"]: p["value"] for p in params}
        assert param_dict["columns"] == "id,email"
        assert param_dict["allow_threshold"] == "0.01"

    def test_get_parameter_values_range_check(self):
        """Test extracting parameter values from RangeCheck."""
        check = RangeCheck(column="temp", min_value=-10, max_value=50)
        params = QualityCheckMapper._get_parameter_values(check)

        param_dict = {p["name"]: p["value"] for p in params}
        assert param_dict["column"] == "temp"
        assert param_dict["min_value"] == "-10"
        assert param_dict["max_value"] == "50"


class TestQualityCheckPublisher:
    """Tests for QualityCheckPublisher."""

    @pytest.fixture
    def publisher(self):
        """Create publisher with mocked client."""
        om_client = Mock()
        return QualityCheckPublisher(om_client)

    def test_publisher_initialization(self, publisher):
        """Test publisher initialization."""
        assert publisher.om_client is not None

    def test_publish_test_definitions_success(self, publisher):
        """Test publishing test definitions successfully."""
        checks = [
            NullCheck(columns=["id"]),
            RangeCheck(column="age", min_value=0, max_value=120),
        ]

        publisher.om_client.create_test_definition.return_value = {"id": "123"}

        stats = publisher.publish_test_definitions(checks, "public.users")

        assert stats["created"] == 2
        assert stats["failed"] == 0
        assert publisher.om_client.create_test_definition.call_count == 2

    def test_publish_test_definitions_with_failure(self, publisher):
        """Test publishing test definitions with partial failure."""
        checks = [
            NullCheck(columns=["id"]),
            RangeCheck(column="age", min_value=0, max_value=120),
        ]

        # First call succeeds, second fails
        publisher.om_client.create_test_definition.side_effect = [
            {"id": "123"},
            Exception("API error"),
        ]

        stats = publisher.publish_test_definitions(checks, "public.users")

        assert stats["created"] == 1
        assert stats["failed"] == 1

    def test_publish_test_cases_success(self, publisher):
        """Test publishing test cases successfully."""
        checks = [NullCheck(columns=["id"])]

        publisher.om_client.create_test_case.return_value = {"id": "456"}

        stats = publisher.publish_test_cases(checks, "public.users")

        assert stats["created"] == 1
        assert stats["failed"] == 0

    def test_publish_test_cases_with_custom_suite(self, publisher):
        """Test publishing test cases with custom suite name."""
        checks = [FreshnessCheck(timestamp_column="created_at", max_age_hours=24)]

        publisher.om_client.create_test_case.return_value = {"id": "456"}

        stats = publisher.publish_test_cases(checks, "public.users", test_suite_name="custom_suite")

        assert stats["created"] == 1
        # Verify suite name was used
        call_args = publisher.om_client.create_test_case.call_args
        assert call_args is not None

    def test_publish_test_results_success(self, publisher):
        """Test publishing test results successfully."""
        check_result = QualityCheckResult(
            passed=True,
            metric_name="null_check",
            metric_value={"id": 0},
        )

        results = [
            {
                "test_case_fqn": "public.users_null_check",
                "check_result": check_result,
            }
        ]

        publisher.om_client.publish_test_result.return_value = {"id": "result_123"}

        stats = publisher.publish_test_results(results)

        assert stats["published"] == 1
        assert stats["failed"] == 0

    def test_publish_test_results_invalid_result(self, publisher):
        """Test publishing with invalid result."""
        results = [{}]  # Missing required fields

        stats = publisher.publish_test_results(results)

        assert stats["published"] == 0
        assert stats["failed"] == 0  # Skipped, not failed

    def test_publish_test_results_with_timestamp(self, publisher):
        """Test publishing with custom timestamp."""
        timestamp = datetime(2024, 1, 1, 12, 0, 0)
        check_result = QualityCheckResult(
            passed=False,
            metric_name="range_check",
            metric_value={"out_of_range": 5},
        )

        results = [
            {
                "test_case_fqn": "public.weather_range_check",
                "check_result": check_result,
                "timestamp": timestamp,
            }
        ]

        publisher.om_client.publish_test_result.return_value = {"id": "result_456"}

        stats = publisher.publish_test_results(results)

        assert stats["published"] == 1

    def test_publish_dbt_tests_success(self, publisher):
        """Test publishing dbt tests successfully."""
        dbt_tests = [
            {
                "name": "not_null_id",
                "type": "not_null",
                "kwargs": {"column_name": "id"},
            },
            {
                "name": "unique_email",
                "type": "unique",
                "kwargs": {"column_name": "email"},
            },
        ]

        publisher.om_client.create_test_case.return_value = {"id": "789"}

        stats = publisher.publish_dbt_tests(dbt_tests, "public.users")

        assert stats["created"] == 2
        assert stats["failed"] == 0
        assert publisher.om_client.create_test_case.call_count == 2

    def test_publish_dbt_tests_with_failure(self, publisher):
        """Test publishing dbt tests with failure."""
        dbt_tests = [
            {
                "name": "not_null_id",
                "type": "not_null",
                "kwargs": {"column_name": "id"},
            }
        ]

        publisher.om_client.create_test_case.side_effect = Exception("API error")

        stats = publisher.publish_dbt_tests(dbt_tests, "public.users")

        assert stats["created"] == 0
        assert stats["failed"] == 1

    def test_publish_test_results_with_multiple_failures(self, publisher):
        """Test publishing multiple results with some failures."""
        results = [
            {
                "test_case_fqn": "public.users_null_check",
                "check_result": QualityCheckResult(
                    passed=True,
                    metric_name="null_check",
                    metric_value={"id": 0},
                ),
            },
            {
                "test_case_fqn": "public.users_range_check",
                "check_result": QualityCheckResult(
                    passed=False,
                    metric_name="range_check",
                    metric_value={"out_of_range": 5},
                ),
            },
        ]

        # First succeeds, second fails
        publisher.om_client.publish_test_result.side_effect = [
            {"id": "result_1"},
            Exception("API error"),
        ]

        stats = publisher.publish_test_results(results)

        assert stats["published"] == 1
        assert stats["failed"] == 1
