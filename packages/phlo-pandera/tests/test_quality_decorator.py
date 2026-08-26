"""
Comprehensive tests for the @phlo_pandera decorator.

Tests cover:
- All check types (NullCheck, RangeCheck, FreshnessCheck, UniqueCheck, CountCheck, SchemaCheck, CustomSQLCheck)
- Decorator configuration (asset_key, group, blocking, warn_threshold)
- Error handling and edge cases
- Metadata generation
- Threshold-based results (warn vs fail)
"""

from datetime import datetime, timedelta

import pandas as pd
import pytest

from phlo_pandera import (
    CountCheck,
    CustomSQLCheck,
    FreshnessCheck,
    NullCheck,
    PatternCheck,
    RangeCheck,
    UniqueCheck,
    clear_quality_checks,
    get_quality_checks,
    phlo_pandera,
)
from phlo.contracts import SLA


@pytest.fixture(autouse=True)
def _reset_quality_checks() -> None:
    """Clear quality check registry before each test."""

    clear_quality_checks()


class TestNullCheck:
    """Tests for NullCheck quality check."""

    def test_null_check_passes_with_no_nulls(self):
        """Test that NullCheck passes when no nulls present."""
        check = NullCheck(columns=["id", "name"])
        df = pd.DataFrame({"id": [1, 2, 3], "name": ["a", "b", "c"]})

        result = check.execute(df, context=None)

        assert result.passed is True
        assert result.metric_name == "null_check"
        assert result.metric_value["id"] == 0
        assert result.metric_value["name"] == 0

    def test_null_check_fails_with_nulls(self):
        """Test that NullCheck fails when nulls present."""
        check = NullCheck(columns=["id", "name"])
        df = pd.DataFrame({"id": [1, None, 3], "name": ["a", None, "c"]})

        result = check.execute(df, context=None)

        assert result.passed is False
        assert result.metric_value["id"] == 1
        assert result.metric_value["name"] == 1
        assert result.failure_message is not None

    def test_null_check_with_threshold(self):
        """Test that NullCheck respects allow_threshold."""
        check = NullCheck(columns=["id"], allow_threshold=0.5)
        df = pd.DataFrame({"id": [1, None, None, 4, 5]})

        result = check.execute(df, context=None)

        assert result.passed is True  # 2/5 = 40% < 50% threshold
        assert result.metadata["threshold"] == 0.5

    def test_null_check_missing_column(self):
        """Test that NullCheck handles missing columns."""
        check = NullCheck(columns=["missing"])
        df = pd.DataFrame({"id": [1, 2, 3]})

        result = check.execute(df, context=None)

        assert result.passed is False
        assert result.failure_message is not None
        assert "not found" in result.failure_message


class TestRangeCheck:
    """Tests for RangeCheck quality check."""

    def test_range_check_passes_within_bounds(self):
        """Test that RangeCheck passes when values within range."""
        check = RangeCheck(column="temperature", min_value=-50, max_value=60)
        df = pd.DataFrame({"temperature": [0, 20, -10, 40, 30]})

        result = check.execute(df, context=None)

        assert result.passed
        assert result.metric_value["out_of_range"] == 0

    def test_range_check_fails_out_of_bounds(self):
        """Test that RangeCheck fails when values out of range."""
        check = RangeCheck(column="temperature", min_value=-50, max_value=60)
        df = pd.DataFrame({"temperature": [0, 100, -100, 40, 30]})

        result = check.execute(df, context=None)

        assert not result.passed
        assert result.metric_value["out_of_range"] == 2
        assert result.failure_message is not None

    def test_range_check_with_threshold(self):
        """Test that RangeCheck respects allow_threshold."""
        check = RangeCheck(column="temperature", min_value=0, max_value=100, allow_threshold=0.5)
        df = pd.DataFrame({"temperature": [10, 20, -10, -20, 30]})

        result = check.execute(df, context=None)

        assert result.passed  # 2/5 = 40% violations < 50% threshold allowed
        assert result.metadata["violation_percentage"] == 0.4

    def test_range_check_min_only(self):
        """Test RangeCheck with only min_value."""
        check = RangeCheck(column="age", min_value=0)
        df = pd.DataFrame({"age": [0, 25, 65, 100]})

        result = check.execute(df, context=None)

        assert result.passed

    def test_range_check_max_only(self):
        """Test RangeCheck with only max_value."""
        check = RangeCheck(column="score", max_value=100)
        df = pd.DataFrame({"score": [50, 75, 100, 0]})

        result = check.execute(df, context=None)

        assert result.passed


class TestFreshnessCheck:
    """Tests for FreshnessCheck quality check."""

    def test_freshness_check_passes_recent_data(self):
        """Test that FreshnessCheck passes when data is recent."""
        now = datetime.now()
        recent = now - timedelta(hours=1)

        check = FreshnessCheck(timestamp_column="timestamp", max_age_hours=2, reference_time=now)
        df = pd.DataFrame({"timestamp": [recent, recent, recent]})

        result = check.execute(df, context=None)

        assert result.passed is True
        assert result.metric_value["max_age_hours"] < 2

    def test_freshness_check_fails_stale_data(self):
        """Test that FreshnessCheck fails when data is stale."""
        now = datetime.now()
        stale = now - timedelta(hours=25)

        check = FreshnessCheck(timestamp_column="timestamp", max_age_hours=2, reference_time=now)
        df = pd.DataFrame({"timestamp": [stale, stale]})

        result = check.execute(df, context=None)

        assert result.passed is False
        assert result.metric_value["max_age_hours"] > 2
        assert result.failure_message is not None

    def test_freshness_check_missing_column(self):
        """Test that FreshnessCheck handles missing column."""
        check = FreshnessCheck(timestamp_column="missing", max_age_hours=1)
        df = pd.DataFrame({"timestamp": [datetime.now()]})

        result = check.execute(df, context=None)

        assert result.passed is False


class TestUniqueCheck:
    """Tests for UniqueCheck quality check."""

    def test_unique_check_passes_no_duplicates(self):
        """Test that UniqueCheck passes when no duplicates."""
        check = UniqueCheck(columns=["id"])
        df = pd.DataFrame({"id": [1, 2, 3, 4, 5]})

        result = check.execute(df, context=None)

        assert result.passed
        assert result.metric_value["duplicate_count"] == 0

    def test_unique_check_fails_with_duplicates(self):
        """Test that UniqueCheck fails when duplicates present."""
        check = UniqueCheck(columns=["id"])
        df = pd.DataFrame({"id": [1, 2, 2, 3, 3, 3]})

        result = check.execute(df, context=None)

        assert not result.passed
        assert result.metric_value["duplicate_count"] == 5  # All rows with duplicates
        assert result.failure_message is not None

    def test_unique_check_multi_column(self):
        """Test UniqueCheck with multiple columns."""
        check = UniqueCheck(columns=["id", "date"])
        df = pd.DataFrame(
            {
                "id": [1, 1, 2, 2],
                "date": ["2024-01-01", "2024-01-02", "2024-01-01", "2024-01-02"],
            }
        )

        result = check.execute(df, context=None)

        assert result.passed  # All combinations are unique

    def test_unique_check_with_threshold(self):
        """Test UniqueCheck respects allow_threshold."""
        check = UniqueCheck(columns=["id"], allow_threshold=0.9)
        df = pd.DataFrame({"id": [1, 1, 2, 3, 3]})

        result = check.execute(df, context=None)

        assert result.passed  # 4/5 = 80% duplicates, threshold is 90%


class TestCountCheck:
    """Tests for CountCheck quality check."""

    def test_count_check_passes_within_range(self):
        """Test that CountCheck passes when row count within range."""
        check = CountCheck(min_rows=3, max_rows=10)
        df = pd.DataFrame({"id": [1, 2, 3, 4, 5]})

        result = check.execute(df, context=None)

        assert result.passed is True
        assert result.metric_value["row_count"] == 5

    def test_count_check_fails_below_min(self):
        """Test that CountCheck fails when row count below minimum."""
        check = CountCheck(min_rows=10)
        df = pd.DataFrame({"id": [1, 2, 3]})

        result = check.execute(df, context=None)

        assert result.passed is False
        assert result.failure_message is not None

    def test_count_check_fails_above_max(self):
        """Test that CountCheck fails when row count above maximum."""
        check = CountCheck(max_rows=3)
        df = pd.DataFrame({"id": [1, 2, 3, 4, 5]})

        result = check.execute(df, context=None)

        assert result.passed is False
        assert result.failure_message is not None

    def test_count_check_empty_dataframe(self):
        """Test CountCheck with empty DataFrame."""
        check = CountCheck(min_rows=0)
        df = pd.DataFrame({"id": []})

        result = check.execute(df, context=None)

        assert result.passed is True


class TestCustomSQLCheck:
    """Tests for CustomSQLCheck quality check."""

    def test_custom_sql_check_passes(self):
        """Test that CustomSQLCheck passes with valid SQL."""
        check = CustomSQLCheck(
            name_="test_check",
            sql="SELECT (id > 0) FROM data",
        )
        df = pd.DataFrame({"id": [1, 2, 3, 4, 5]})

        result = check.execute(df, context=None)

        assert result.passed is True
        assert result.metric_value["failures"] == 0

    def test_custom_sql_check_fails(self):
        """Test that CustomSQLCheck fails with invalid data."""
        check = CustomSQLCheck(
            name_="positive_check",
            sql="SELECT (id > 0) FROM data",
        )
        df = pd.DataFrame({"id": [1, -2, 3, -4, 5]})

        result = check.execute(df, context=None)

        assert result.passed is False
        assert result.metric_value["failures"] == 2
        assert result.failure_message is not None

    def test_custom_sql_check_with_threshold(self):
        """Test CustomSQLCheck respects allow_threshold."""
        check = CustomSQLCheck(
            name_="positive_check",
            sql="SELECT (id > 0) FROM data",
            allow_threshold=0.5,
        )
        df = pd.DataFrame({"id": [1, -2, 3, -4, 5]})

        result = check.execute(df, context=None)

        assert result.passed is True  # 2/5 = 40% failures < 50% threshold


class TestPhloQualityDecorator:
    """Tests for @phlo_pandera decorator."""

    def test_decorator_creates_asset_check(self):
        """Test that decorator creates a valid Dagster asset check."""
        # Get baseline count
        baseline = len(get_quality_checks())

        @phlo_pandera(
            table="test.table",
            checks=[NullCheck(columns=["id"])],
        )
        def test_check():
            """No-op asset function used to register a test check."""

            pass

        # Verify a new check was registered
        checks = get_quality_checks()
        assert len(checks) > baseline

    def test_decorator_with_multiple_checks(self):
        """Test decorator with multiple quality checks."""

        @phlo_pandera(
            table="test.table",
            checks=[
                NullCheck(columns=["id"]),
                RangeCheck(column="value", min_value=0, max_value=500),
            ],
        )
        def multi_check():
            """No-op asset function used to register multiple checks."""

            pass

        # Verify the decorator works
        checks = get_quality_checks()
        assert len(checks) > 0

    def test_decorator_with_warn_threshold(self):
        """Test decorator with warn_threshold parameter."""

        @phlo_pandera(
            table="test.table",
            checks=[NullCheck(columns=["id"])],
            warn_threshold=0.5,
        )
        def warn_check():
            """No-op asset function used to register warn-threshold checks."""

            pass

        checks = get_quality_checks()
        assert len(checks) > 0

    def test_decorator_with_custom_description(self):
        """Test decorator with custom description."""

        @phlo_pandera(
            table="test.table",
            checks=[NullCheck(columns=["id"])],
            description="Custom quality check description",
        )
        def described_check():
            """No-op asset function used to register described checks."""

            pass

        checks = get_quality_checks()
        assert len(checks) > 0

    def test_decorator_with_custom_asset_key(self):
        """Test decorator with custom asset key."""

        @phlo_pandera(
            table="test.table",
            checks=[NullCheck(columns=["id"])],
            asset_key="custom.path",
        )
        def custom_key_check():
            """No-op asset function used to register custom asset-key checks."""

            pass

        checks = get_quality_checks()
        assert len(checks) > 0

    def test_decorator_contract_metadata_tags(self):
        """Contract metadata is propagated to check tags."""

        @phlo_pandera(
            table="test.table",
            checks=[NullCheck(columns=["id"])],
            owner="platform-team",
            consumers=["analytics", "ml-pipeline"],
            sla=SLA(quality_threshold=0.97),
        )
        def contract_meta_check():
            """No-op asset function used to register contract metadata tags."""

            pass

        checks = get_quality_checks()
        quality_check = next(spec for spec in checks if spec.name == "contract_meta_check")
        assert quality_check.tags["contract_owner"] == "platform-team"
        assert quality_check.tags["contract_consumers"] == "analytics,ml-pipeline"
        assert '"quality_threshold": 0.97' in quality_check.tags["contract_sla"]


class TestQualityCheckIntegration:
    """Integration tests for quality checks."""

    def test_quality_check_with_missing_data(self):
        """Test quality checks handle missing data gracefully."""
        check = NullCheck(columns=["id"])
        df = pd.DataFrame({"id": []})

        result = check.execute(df, context=None)

        assert isinstance(result, object)  # Should return valid result
        assert result.metric_name == "null_check"

    def test_quality_check_with_null_values(self):
        """Test quality checks handle dataframes with all nulls."""
        check = NullCheck(columns=["id"])
        df = pd.DataFrame({"id": [None, None, None]})

        result = check.execute(df, context=None)

        assert result.passed is False
        assert result.metric_value["id"] == 3

    def test_quality_check_with_mixed_types(self):
        """Test quality checks handle mixed data types."""
        df = pd.DataFrame(
            {
                "id": [1, 2, 3],
                "name": ["a", "b", "c"],
                "value": [1.5, 2.5, 3.5],
            }
        )

        check = NullCheck(columns=["id", "name", "value"])
        result = check.execute(df, context=None)

        assert result.passed is True

    def test_multiple_checks_on_same_dataframe(self):
        """Test executing multiple checks on the same dataframe."""
        df = pd.DataFrame(
            {
                "id": [1, 2, 3],
                "temperature": [20, 25, 30],
                "timestamp": [datetime.now()] * 3,
            }
        )

        checks = [
            NullCheck(columns=["id"]),
            RangeCheck(column="temperature", min_value=0, max_value=100),
            CountCheck(min_rows=3),
        ]

        results = [check.execute(df, context=None) for check in checks]

        assert all(r.passed for r in results)
        assert len(results) == 3


class TestPatternCheck:
    """Tests for PatternCheck quality check."""

    def test_pattern_check_passes_with_all_matches(self):
        """Test that PatternCheck passes when all values match pattern."""
        check = PatternCheck(
            column="email", pattern=r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$"
        )
        df = pd.DataFrame({"email": ["test@example.com", "user@domain.org", "admin@site.net"]})

        result = check.execute(df, context=None)

        assert result.passed
        assert result.metric_name == "pattern_check"
        assert result.metadata["match_count"] == 3
        assert result.metadata["non_match_count"] == 0

    def test_pattern_check_fails_with_non_matches(self):
        """Test that PatternCheck fails when values don't match pattern."""
        check = PatternCheck(
            column="email", pattern=r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$"
        )
        df = pd.DataFrame({"email": ["test@example.com", "invalid-email", "another@bad"]})

        result = check.execute(df, context=None)

        assert not result.passed
        assert result.metadata["non_match_count"] == 2
        assert result.failure_message is not None
        assert "invalid-email" in result.failure_message

    def test_pattern_check_with_threshold(self):
        """Test that PatternCheck respects allow_threshold."""
        check = PatternCheck(
            column="postal_code",
            pattern=r"^\d{5}$",
            allow_threshold=0.20,  # Allow 20% invalid
        )
        df = pd.DataFrame({"postal_code": ["12345", "67890", "ABCDE", "XYZ", "11111"]})

        result = check.execute(df, context=None)

        # 2 out of 5 invalid = 40%, exceeds 20% threshold
        assert not result.passed
        assert result.metadata["non_match_percentage"] == 0.4

    def test_pattern_check_case_insensitive(self):
        """Test that PatternCheck respects case_sensitive flag."""
        check = PatternCheck(column="code", pattern=r"^ABC\d+$", case_sensitive=False)
        df = pd.DataFrame({"code": ["ABC123", "abc456", "AbC789"]})

        result = check.execute(df, context=None)

        assert result.passed
        assert result.metadata["match_count"] == 3

    def test_pattern_check_with_missing_column(self):
        """Test that PatternCheck handles missing column."""
        check = PatternCheck(column="nonexistent", pattern=r".*")
        df = pd.DataFrame({"other_column": [1, 2, 3]})

        result = check.execute(df, context=None)

        assert not result.passed
        assert result.failure_message is not None
        assert "not found" in result.failure_message


class TestQualityCheckMetadata:
    """Tests for quality check metadata generation."""

    def test_metadata_includes_check_details(self):
        """Test that metadata includes detailed check information."""
        check = RangeCheck(column="temperature", min_value=-50, max_value=60)
        df = pd.DataFrame({"temperature": [0, 20, 100]})

        result = check.execute(df, context=None)

        assert "expected_min" in result.metadata
        assert "expected_max" in result.metadata
        assert "actual_min" in result.metadata
        assert "actual_max" in result.metadata

    def test_null_check_metadata_includes_percentages(self):
        """Test that NullCheck metadata includes percentages."""
        check = NullCheck(columns=["id"])
        df = pd.DataFrame({"id": [1, None, 3]})

        result = check.execute(df, context=None)

        assert "null_percentages" in result.metadata
        assert "null_counts" in result.metadata

    def test_unique_check_metadata_includes_duplicate_info(self):
        """Test that UniqueCheck metadata includes duplicate information."""
        check = UniqueCheck(columns=["id"])
        df = pd.DataFrame({"id": [1, 1, 2, 3, 3, 3]})

        result = check.execute(df, context=None)

        assert "duplicate_count" in result.metadata
        assert "duplicate_percentage" in result.metadata
