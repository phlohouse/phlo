"""Functional tests for phlo-pandera quality evaluation.

- Check generation logic: quality check contract creation and metadata.
- Check execution: run checks against real Pandas datasets and Parquet files.
"""

import pandas as pd


class TestPanderaContractEvaluation:
    """Test Pandera contract evaluation functionality."""

    def test_evaluate_valid_data(self):
        """Test evaluation passes for valid data."""
        from pandera.pandas import DataFrameModel
        from phlo_pandera.pandera_asset_checks import evaluate_pandera_contract

        class TestSchema(DataFrameModel):
            """Schema for valid integer and string columns."""

            id: int
            name: str

        df = pd.DataFrame({"id": [1, 2, 3], "name": ["Alice", "Bob", "Charlie"]})

        result = evaluate_pandera_contract(df, schema_class=TestSchema)

        assert result.passed is True
        assert result.failed_count == 0
        assert result.total_count == 3
        assert result.error is None

    def test_evaluate_invalid_data_wrong_type(self):
        """Test evaluation fails for wrong column types."""
        from pandera.pandas import DataFrameModel
        from phlo_pandera.pandera_asset_checks import evaluate_pandera_contract

        class TestSchema(DataFrameModel):
            """Schema expecting integer IDs and string names."""

            id: int
            name: str

        # id column has strings instead of ints
        df = pd.DataFrame({"id": ["not", "an", "int"], "name": ["Alice", "Bob", "Charlie"]})

        result = evaluate_pandera_contract(df, schema_class=TestSchema)

        assert result.passed is False
        assert result.error is not None

    def test_evaluate_missing_column(self):
        """Test evaluation fails for missing required column."""
        from pandera.pandas import DataFrameModel
        from phlo_pandera.pandera_asset_checks import evaluate_pandera_contract

        class TestSchema(DataFrameModel):
            """Schema requiring an additional float column."""

            id: int
            name: str
            required_col: float

        # Missing required_col
        df = pd.DataFrame({"id": [1, 2], "name": ["a", "b"]})

        result = evaluate_pandera_contract(df, schema_class=TestSchema)

        assert result.passed is False

    def test_evaluation_result_structure(self):
        """Test PanderaContractEvaluation dataclass structure."""
        from phlo_pandera.pandera_asset_checks import PanderaContractEvaluation

        evaluation = PanderaContractEvaluation(
            passed=True, failed_count=0, total_count=100, sample=[], error=None
        )

        assert evaluation.passed is True
        assert evaluation.failed_count == 0
        assert evaluation.total_count == 100


# =============================================================================
# Quality Check Contract Tests
# =============================================================================


class TestQualityCheckContract:
    """Test QualityCheckContract functionality."""

    def test_contract_creation(self):
        """Test creating a QualityCheckContract."""
        from phlo_pandera.contract import QualityCheckContract

        contract = QualityCheckContract(
            source="pandera",
            partition_key="2024-01-01",
            failed_count=5,
            total_count=100,
            query_or_sql="SELECT * FROM table",
            repro_sql=None,
            sample=[],
        )

        assert contract.source == "pandera"
        assert contract.failed_count == 5
        assert contract.total_count == 100

    def test_contract_to_dagster_metadata(self):
        """Test converting contract to Dagster metadata."""
        from phlo_pandera.contract import QualityCheckContract

        contract = QualityCheckContract(
            source="pandera",
            partition_key=None,
            failed_count=2,
            total_count=50,
            query_or_sql="SELECT 1",
            repro_sql=None,
            sample=[{"error": "test"}],
        )

        metadata = contract.to_dagster_metadata()

        assert isinstance(metadata, dict)

    def test_pandera_contract_check_name(self):
        """The contract check name is a stable orchestration identifier."""
        from phlo_pandera.contract import PANDERA_CONTRACT_CHECK_NAME

        assert PANDERA_CONTRACT_CHECK_NAME == "pandera_contract"


# =============================================================================
# Quality Decorator Tests
# =============================================================================


class TestQualityDecorator:
    """Test quality decorator functionality."""

    def test_provider_exposes_quality_check_base_class(self):
        """Provider check class map includes the base QualityCheck type."""
        from phlo_pandera import QualityCheck
        from phlo_pandera.plugin import PanderaQualityProvider

        check_classes = PanderaQualityProvider().get_check_classes()

        assert "quality_check" in check_classes
        assert check_classes["quality_check"] is QualityCheck


# =============================================================================
# Severity Tests
# =============================================================================


class TestQualitySeverity:
    """Test quality severity functionality."""

    def test_severity_for_pandera_contract(self):
        """Contract severity is None on pass and error on failure."""
        from phlo_pandera.severity import severity_for_pandera_contract

        assert severity_for_pandera_contract(passed=True) is None
        assert severity_for_pandera_contract(passed=False) == "error"


# =============================================================================
# Parquet File Validation Tests
# =============================================================================


class TestParquetValidation:
    """Test Parquet file validation."""

    def test_evaluate_parquet_file(self, tmp_path):
        """Test evaluating a Pandera contract against a Parquet file."""
        from pathlib import Path
        from pandera.pandas import DataFrameModel
        from phlo_pandera.pandera_asset_checks import evaluate_pandera_contract_parquet

        class TestSchema(DataFrameModel):
            """Schema used for parquet contract validation."""

            id: int
            name: str

        # Create test parquet file
        df = pd.DataFrame({"id": [1, 2, 3], "name": ["a", "b", "c"]})

        parquet_path = Path(tmp_path) / "test.parquet"
        df.to_parquet(parquet_path)

        result = evaluate_pandera_contract_parquet(parquet_path, schema_class=TestSchema)

        assert result.passed is True


# =============================================================================
# Complex Schema Tests
# =============================================================================


class TestComplexSchemas:
    """Test quality checks with complex schemas."""

    def test_schema_with_datetime(self):
        """Test schema with datetime columns."""
        from datetime import datetime
        from pandera.pandas import DataFrameModel
        from phlo_pandera.pandera_asset_checks import evaluate_pandera_contract

        class DateTimeSchema(DataFrameModel):
            """Schema with datetime field for timestamp validation."""

            id: int
            created_at: datetime

        df = pd.DataFrame(
            {"id": [1, 2], "created_at": pd.to_datetime(["2024-01-01", "2024-01-02"])}
        )

        result = evaluate_pandera_contract(df, schema_class=DateTimeSchema)

        # Should handle datetime conversion
        assert result.total_count == 2

    def test_schema_with_nullable_fields(self):
        """Test schema with nullable/optional fields."""
        from typing import Optional
        from pandera.pandas import DataFrameModel, Field
        from phlo_pandera.pandera_asset_checks import evaluate_pandera_contract

        class NullableSchema(DataFrameModel):
            """Schema with nullable optional string values."""

            id: int
            optional_value: Optional[str] = Field(nullable=True)

        df = pd.DataFrame({"id": [1, 2, 3], "optional_value": ["a", None, "c"]})

        result = evaluate_pandera_contract(df, schema_class=NullableSchema)

        # Should handle nulls
        assert result.total_count == 3
