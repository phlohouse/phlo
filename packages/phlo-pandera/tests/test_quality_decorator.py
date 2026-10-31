"""
Comprehensive tests for the @phlo_pandera decorator.

Tests cover:
- All check types (NullCheck, RangeCheck, FreshnessCheck, UniqueCheck, CountCheck, SchemaCheck, CustomSQLCheck)
- Decorator configuration (asset_key, group, blocking, warn_threshold)
- Error handling and edge cases
- Metadata generation
- Threshold-based results (warn vs fail)
"""

import logging
from contextlib import closing
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock

import duckdb
import pandas as pd
import pandera.pandas as pa
import pytest
from phlo.capabilities import RuntimeRouting
from phlo.contracts import SLA
from phlo.logging import get_logger

from phlo_pandera import (
    CountCheck,
    CustomSQLCheck,
    FreshnessCheck,
    NullCheck,
    PatternCheck,
    RangeCheck,
    SchemaCheck,
    UniqueCheck,
    clear_quality_checks,
    get_quality_checks,
    phlo_pandera,
)


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

    def test_decorator_propagates_description_and_custom_asset_key(self):
        """The description and asset_key options reach the registered check spec."""

        @phlo_pandera(
            table="test.table",
            checks=[NullCheck(columns=["id"])],
            description="Custom quality check description",
            asset_key="custom.path",
        )
        def custom_key_check():
            """No-op asset function used to register custom asset-key checks."""

            pass

        spec = next(spec for spec in get_quality_checks() if spec.name == "custom_key_check")
        assert spec.asset_key == "custom.path"
        assert "Custom quality check description" in (spec.description or "")

    def test_decorator_registers_all_declared_checks_on_one_spec(self):
        """Every declared check lands on the asset's single registered spec."""
        baseline = len(get_quality_checks())

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

        registered = get_quality_checks()
        assert len(registered) - baseline == 1
        spec = next(spec for spec in registered if spec.name == "multi_check")
        assert "null_check_id" in spec.description
        assert "range_check_value" in spec.description

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

        # An empty frame has no nulls to fail on; the check reports a zero count
        # rather than dividing by zero or treating absence of rows as a failure.
        assert result.passed is True
        assert result.metric_name == "null_check"
        assert result.metric_value["id"] == 0
        assert result.metadata["null_percentages"] == {"id": 0.0}

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


@pytest.mark.parametrize("schema", [False, True])
@pytest.mark.parametrize("outcome", ["pass", "fail", "empty", "query_failed"])
def test_registered_checks_execute_against_duckdb(schema: bool, outcome: str) -> None:
    class PositiveIds(pa.DataFrameModel):
        id: int = pa.Field(gt=0)

    checks = (
        [SchemaCheck(schema=PositiveIds)]
        if schema
        else [RangeCheck(column="id", min_value=1), CountCheck(min_rows=1)]
    )
    # Enabled at INFO like Dagster's context logger, so keyword fields raise.
    logger = logging.getLogger("test.quality")
    logger.setLevel(logging.INFO)
    with duckdb.connect() as connection:
        connection.execute("CREATE TABLE readings (id BIGINT)")
        if outcome != "empty":
            connection.execute(
                "INSERT INTO readings VALUES (1), (?)", [-1 if outcome == "fail" else 2]
            )
        runtime = SimpleNamespace(
            run_id="quality-run",
            partition_key=None,
            resources={"duckdb": connection},
            # Orchestrator loggers such as Dagster's reject structured fields.
            logger=logger,
        )

        def quality():
            return "unchanged"

        decorated = phlo_pandera(
            table="readings",
            checks=checks,
            backend="duckdb",
            partition_aware=False,
            blocking=False,
            warn_threshold=0.5,
            query="SELECT * FROM missing_table" if outcome == "query_failed" else None,
        )(quality)
        assert decorated is quality
        [spec] = get_quality_checks()
        assert spec.asset_key == "readings"
        assert spec.blocking is schema
        result = spec.fn(runtime)
        assert result.passed is (outcome in {"pass", "empty"})
        expected_severity = {
            "pass": None,
            "empty": None,
            "fail": "error" if schema else "warn",
            "query_failed": "error",
        }[outcome]
        assert result.severity == expected_severity
        if outcome == "query_failed":
            assert result.metadata["reason"] == "query_failed"
            assert "missing_table" in result.metadata["error"]
        elif outcome == "empty":
            assert result.metadata["note"] == "No data available for validation"
        elif schema:
            assert result.metadata["schemas"] == ["PositiveIds"]
            assert result.metadata["total_count"] == 2
            assert result.metadata["failed_count"] == (1 if outcome == "fail" else 0)
        else:
            assert result.metadata["summary"] == (
                "1/2 quality checks passed" if outcome == "fail" else "2/2 quality checks passed"
            )


@pytest.mark.parametrize("candidate_passes", [False, True])
def test_schema_and_data_checks_validate_the_same_runtime_ref(candidate_passes):
    class PositiveIds(pa.DataFrameModel):
        id: int = pa.Field(gt=0)

    class RefResource:
        """Use real SQL with different contents for each catalog ref."""

        def __init__(self, connections, ref="main"):
            self.connections = connections
            self.ref = ref

        def for_ref(self, ref):
            return RefResource(self.connections, ref)

        def cursor(self):
            return self.connections[self.ref].cursor()

    with duckdb.connect() as main, duckdb.connect() as candidate:
        main.execute(
            "CREATE TABLE readings AS SELECT ?::BIGINT AS id", [-1 if candidate_passes else 1]
        )
        candidate.execute(
            "CREATE TABLE readings AS SELECT ?::BIGINT AS id", [1 if candidate_passes else -1]
        )
        runtime = SimpleNamespace(
            run_id="candidate-quality",
            partition_key=None,
            routing=RuntimeRouting(ref="candidate"),
            resources={"trino": RefResource({"main": main, "candidate": candidate})},
            logger=get_logger("test.candidate_quality"),
        )

        @phlo_pandera(
            table="readings",
            checks=[SchemaCheck(schema=PositiveIds), RangeCheck(column="id", min_value=1)],
            backend="trino",
            full_table=True,
        )
        def quality():
            pass

        results = [spec.fn(runtime) for spec in get_quality_checks()]
        assert len(results) == 2
        assert [result.passed for result in results] == [candidate_passes, candidate_passes]


@pytest.mark.parametrize("entrypoint", ["sla", "neutral"])
@pytest.mark.parametrize(
    "timestamps, passed, age, reason",
    [
        (["2025-01-14T06:00:00Z"], False, 30.0, None),
        (["2025-01-15T11:00:00Z"], True, 1.0, None),
        (["2025-01-15 10:00:00"], True, 2.0, None),
        (["2025-01-15T10:59:59+01:00"], False, 2 + 1 / 3600, None),
        (["2025-01-15T11:00:00+01:00", "2025-01-15T11:30:00Z"], True, 0.5, None),
        (["invalid", "2025-01-15T11:00:00Z"], True, 1.0, None),
        ([None], False, None, "no_usable_timestamps"),
        (["invalid"], False, None, "no_usable_timestamps"),
        ([], False, None, "no_usable_timestamps"),
        (None, False, None, "missing_timestamp_column"),
    ],
)
def test_explicit_freshness_public_declarations_execute_against_duckdb(
    entrypoint,
    timestamps,
    passed,
    age,
    reason,
) -> None:
    import phlo

    reference = datetime(2025, 1, 15, 17, 30, tzinfo=timezone(timedelta(hours=5, minutes=30)))
    promise = SLA(2, freshness_column="event_time", freshness_reference_time=reference)
    options = {"table": "readings", "backend": "duckdb", "full_table": True}
    if entrypoint == "sla":
        decorator = phlo.quality.pandera(
            checks=[
                FreshnessCheck(timestamp_column="_phlo_ingested_at", max_age_hours=2),
                RangeCheck(column="id", min_value=0),
            ],
            sla=promise,
            **options,
        )
    else:
        decorator = phlo.quality.rules(
            rules=[
                phlo.freshness(
                    "event_time", hours=2, reference_time=reference, require_timestamps=True
                )
            ],
            **options,
        )

    @decorator
    def telemetry_freshness():
        pass

    with duckdb.connect() as connection:
        connection.execute(
            "CREATE TABLE readings (event_time VARCHAR, _phlo_ingested_at TIMESTAMPTZ, id BIGINT)"
        )
        if timestamps is None:
            connection.execute("ALTER TABLE readings DROP event_time")
            connection.execute("INSERT INTO readings VALUES (now(), 1)")
        elif timestamps:
            connection.executemany(
                "INSERT INTO readings VALUES (?, now(), 1)", [(t,) for t in timestamps]
            )
        runtime = SimpleNamespace(
            run_id="historical-freshness",
            partition_key=None,
            resources={"duckdb": connection},
            logger=get_logger("test.freshness"),
        )
        [spec] = get_quality_checks()
        result = spec.fn(runtime)
    assert result.passed is passed
    assert result.metadata["checks_executed"] == (
        3 if entrypoint == "sla" and timestamps != [] else 1
    )
    if timestamps == []:
        assert result.metadata["checks_failed"] == 1
        assert "range_check_value" not in result.metadata
    prefix = "freshness_check_event_time"
    assert result.metadata[f"{prefix}_timestamp_column"] == "event_time"
    assert result.metadata[f"{prefix}_reference_mode"] == "explicit"
    assert datetime.fromisoformat(result.metadata[f"{prefix}_reference_time"]) == reference
    if age is not None:
        assert result.metadata[f"{prefix}_age_hours"] == pytest.approx(age)
    if reason is not None:
        assert result.metadata[f"{prefix}_reason"] == reason


def test_explicit_sla_without_reference_uses_wall_clock() -> None:
    import phlo

    @phlo.quality.pandera(
        table="readings",
        checks=[],
        backend="duckdb",
        full_table=True,
        sla=SLA(2, freshness_column="event_time"),
    )
    def live_freshness():
        pass

    with duckdb.connect() as connection:
        connection.execute(
            "CREATE TABLE readings AS SELECT now() - INTERVAL '1 hour' AS event_time"
        )
        runtime = SimpleNamespace(
            run_id="live-freshness",
            partition_key=None,
            resources={"duckdb": connection},
            logger=get_logger("test.freshness"),
        )
        [spec] = get_quality_checks()
        before = datetime.now(timezone.utc)
        result = spec.fn(runtime)
        after = datetime.now(timezone.utc)
    assert result.passed
    assert result.metadata["freshness_check_event_time_reference_mode"] == "wall_clock"
    evaluated_at = datetime.fromisoformat(
        result.metadata["freshness_check_event_time_reference_time"]
    )
    assert before <= evaluated_at <= after


def test_registered_quality_check_execution_error_is_a_failed_result() -> None:
    from phlo_pandera.checks import QualityCheck

    class BrokenCheck(QualityCheck):
        @property
        def name(self):
            return "broken"

        def execute(self, df, context):
            raise RuntimeError("check unavailable")

    @phlo_pandera(table="readings", checks=[BrokenCheck()], backend="duckdb", full_table=True)
    def quality():
        pass

    with duckdb.connect() as connection:
        connection.execute("CREATE TABLE readings AS SELECT 1 AS id")
        runtime = SimpleNamespace(
            run_id="quality-run",
            partition_key=None,
            resources={"duckdb": connection},
            logger=get_logger("test.quality"),
        )
        [spec] = get_quality_checks()
        result = spec.fn(runtime)
    assert result.passed is False
    assert result.severity == "error"
    assert result.metadata["summary"] == "0/1 quality checks passed"
    assert "check unavailable" in result.metadata["failures"]


def test_decorator_pushes_builtin_checks_into_real_duckdb() -> None:
    checks = [
        CountCheck(min_rows=7),
        NullCheck(columns=["id"], allow_threshold=0.2),
        UniqueCheck(columns=["id", "kind"], allow_threshold=0.5),
        RangeCheck(column="value", min_value=0, max_value=10, allow_threshold=0.25),
    ]
    with duckdb.connect() as connection:
        connection.execute(
            "CREATE TABLE events AS SELECT *, 0 AS __phlo_duplicates, 0 AS __phlo_quality_index FROM (VALUES (2, 'a', -1.0), (2, 'a', 2.0), (5, 'b', 20.0), (7, 'c', 4.0), (NULL, 'z', NULL), (NULL, 'z', NULL)) t(id, kind, value)"
        )

        @phlo_pandera(table="events", checks=checks, backend="duckdb", full_table=True)
        def aggregate_quality():
            pass

        runtime = SimpleNamespace(
            run_id="aggregate-run",
            partition_key=None,
            logger=MagicMock(),
            resources={"duckdb": connection},
        )
        result = next(spec for spec in get_quality_checks() if spec.name == "aggregate_quality").fn(
            runtime
        )
        assert not result.passed
        assert result.metadata["rows_validated"] == 6
        assert result.metadata["count_check_row_count"] == 6
        assert result.metadata["null_check_null_counts"] == {"id": 2}
        assert result.metadata["unique_check_duplicate_count"] == 4
        assert result.metadata["range_check_out_of_range"] == 2
        assert [row["row_index"] for row in result.metadata["unique_check_sample_rows"]] == [
            0,
            1,
            4,
            5,
        ]


@pytest.mark.parametrize(
    "minimum,maximum", [(float("-inf"), float("inf")), (float("nan"), float("nan"))]
)
def test_sql_range_preserves_nonfinite_bounds(minimum, maximum):
    with duckdb.connect() as connection:

        @phlo_pandera(
            table="unused",
            backend="duckdb",
            full_table=True,
            query="SELECT * FROM (VALUES (-2.0), (3.0), (NULL)) t(value)",
            checks=[RangeCheck(column="value", min_value=minimum, max_value=maximum)],
        )
        def nonfinite_bounds():
            pass

        result = get_quality_checks()[0].fn(
            SimpleNamespace(
                run_id="nonfinite",
                partition_key=None,
                logger=MagicMock(),
                resources={"duckdb": connection},
            )
        )
        assert result.passed
        assert result.metadata["range_check_out_of_range"] == 0
        assert result.metadata["range_check_actual_min"] == -2.0
        assert result.metadata["range_check_actual_max"] == 3.0


@pytest.mark.integration
def test_aggregate_results_against_real_trino_nan_and_nulls() -> None:
    import time

    import httpx
    from testcontainers.core.container import DockerContainer
    from trino.dbapi import connect

    with DockerContainer("trinodb/trino:483").with_exposed_ports(8080) as container:
        port = int(container.get_exposed_port(8080))
        host = container.get_container_host_ip()
        for _ in range(90):
            try:
                response = httpx.get(f"http://{host}:{port}/v1/info", timeout=2)
                if response.is_success and not response.json()["starting"]:
                    break
            except httpx.HTTPError:
                pass
            time.sleep(1)
        else:
            pytest.fail("Disposable Trino did not become ready")
        connection = connect(host=host, port=port, user="quality-test")
        try:

            @phlo_pandera(
                table="unused",
                backend="trino",
                full_table=True,
                query="SELECT * FROM (VALUES (2e0, -1e0), (2e0, 2e0), (5e0, 20e0), (7e0, 4e0), (nan(), NULL), (NULL, NULL)) t(id, value)",
                checks=[
                    CountCheck(min_rows=7),
                    NullCheck(columns=["id"]),
                    UniqueCheck(columns=["id"]),
                    RangeCheck(column="value", min_value=0, max_value=10),
                ],
            )
            def trino_aggregate_quality():
                pass

            runtime = SimpleNamespace(
                run_id="aggregate-trino-run",
                partition_key=None,
                logger=MagicMock(),
                resources={"trino": SimpleNamespace(cursor=lambda: closing(connection.cursor()))},
            )
            result = next(
                spec for spec in get_quality_checks() if spec.name == "trino_aggregate_quality"
            ).fn(runtime)
            assert not result.passed
            assert result.metadata["count_check_row_count"] == 6
            assert result.metadata["null_check_null_counts"] == {"id": 2}
            assert result.metadata["unique_check_duplicate_count"] == 4
            assert result.metadata["range_check_out_of_range"] == 2
            for minimum, maximum in [(float("-inf"), float("inf")), (float("nan"), float("nan"))]:
                clear_quality_checks()

                @phlo_pandera(
                    table="unused",
                    backend="trino",
                    full_table=True,
                    query="SELECT * FROM (VALUES (-2e0), (3e0), (NULL)) t(value)",
                    checks=[RangeCheck(column="value", min_value=minimum, max_value=maximum)],
                )
                def nonfinite_trino_bounds():
                    pass

                result = get_quality_checks()[0].fn(runtime)
                assert result.passed
                assert result.metadata["range_check_out_of_range"] == 0
        finally:
            connection.close()
