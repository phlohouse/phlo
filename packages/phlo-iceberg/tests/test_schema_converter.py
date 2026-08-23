"""Tests for Iceberg schema conversion utilities.

Pins the Pandera-to-PyIceberg type mapping (including the Decimal
fallback), nullable-to-required inversion, description-to-doc transfer,
sequential field IDs, and the reserved DLT/Phlo metadata fields injected
at fixed IDs unless disabled.
"""

from datetime import date, datetime
from decimal import Decimal

import pytest
from pandera.pandas import DataFrameModel, Field

pytest.importorskip("pyiceberg")

from phlo_iceberg.schema_conversion import SchemaConversionError, pandera_to_iceberg
from pyiceberg.types import (
    BinaryType,
    BooleanType,
    DateType,
    DoubleType,
    LongType,
    StringType,
    TimestamptzType,
)

pytestmark = pytest.mark.integration


class TestBasicTypeMapping:
    """Test basic Pandera to PyIceberg type conversions."""

    def test_string_type_conversion(self):
        """Test str -> StringType conversion."""

        class SimpleSchema(DataFrameModel):
            """Pandera schema used for this test case."""

            name: str

        schema = pandera_to_iceberg(SimpleSchema, add_dlt_metadata=False, add_phlo_metadata=False)

        assert len(schema.fields) == 1
        assert schema.fields[0].name == "name"
        assert isinstance(schema.fields[0].field_type, StringType)

    def test_int_type_conversion(self):
        """Test int -> LongType conversion."""

        class SimpleSchema(DataFrameModel):
            """Pandera schema used for this test case."""

            count: int

        schema = pandera_to_iceberg(SimpleSchema, add_dlt_metadata=False, add_phlo_metadata=False)

        assert len(schema.fields) == 1
        assert schema.fields[0].name == "count"
        assert isinstance(schema.fields[0].field_type, LongType)

    def test_float_type_conversion(self):
        """Test float -> DoubleType conversion."""

        class SimpleSchema(DataFrameModel):
            """Pandera schema used for this test case."""

            value: float

        schema = pandera_to_iceberg(SimpleSchema, add_dlt_metadata=False, add_phlo_metadata=False)

        assert len(schema.fields) == 1
        assert schema.fields[0].name == "value"
        assert isinstance(schema.fields[0].field_type, DoubleType)

    def test_bool_type_conversion(self):
        """Test bool -> BooleanType conversion."""

        class SimpleSchema(DataFrameModel):
            """Pandera schema used for this test case."""

            active: bool

        schema = pandera_to_iceberg(SimpleSchema, add_dlt_metadata=False, add_phlo_metadata=False)

        assert len(schema.fields) == 1
        assert schema.fields[0].name == "active"
        assert isinstance(schema.fields[0].field_type, BooleanType)

    def test_datetime_type_conversion(self):
        """Test datetime -> TimestamptzType conversion."""

        class SimpleSchema(DataFrameModel):
            """Pandera schema used for this test case."""

            created_at: datetime

        schema = pandera_to_iceberg(SimpleSchema, add_dlt_metadata=False, add_phlo_metadata=False)

        assert len(schema.fields) == 1
        assert schema.fields[0].name == "created_at"
        assert isinstance(schema.fields[0].field_type, TimestamptzType)

    def test_date_type_conversion(self):
        """Test date -> DateType conversion."""

        class SimpleSchema(DataFrameModel):
            """Pandera schema used for this test case."""

            birth_date: date

        schema = pandera_to_iceberg(SimpleSchema, add_dlt_metadata=False, add_phlo_metadata=False)

        assert len(schema.fields) == 1
        assert schema.fields[0].name == "birth_date"
        assert isinstance(schema.fields[0].field_type, DateType)

    def test_bytes_type_conversion(self):
        """Test bytes -> BinaryType conversion."""

        class SimpleSchema(DataFrameModel):
            """Pandera schema used for this test case."""

            data: bytes

        schema = pandera_to_iceberg(SimpleSchema, add_dlt_metadata=False, add_phlo_metadata=False)

        assert len(schema.fields) == 1
        assert schema.fields[0].name == "data"
        assert isinstance(schema.fields[0].field_type, BinaryType)

    def test_decimal_type_conversion(self):
        """Test Decimal -> DoubleType conversion (fallback)."""

        class SimpleSchema(DataFrameModel):
            """Pandera schema used for this test case."""

            price: Decimal

        schema = pandera_to_iceberg(SimpleSchema, add_dlt_metadata=False, add_phlo_metadata=False)

        assert len(schema.fields) == 1
        assert schema.fields[0].name == "price"
        assert isinstance(schema.fields[0].field_type, DoubleType)


class TestOptionalTypes:
    """Test Optional/Union type handling."""

    def test_optional_string_type(self):
        """Test str | None -> StringType conversion."""

        class SimpleSchema(DataFrameModel):
            """Pandera schema used for this test case."""

            nickname: str | None

        schema = pandera_to_iceberg(SimpleSchema, add_dlt_metadata=False, add_phlo_metadata=False)

        assert len(schema.fields) == 1
        assert schema.fields[0].name == "nickname"
        assert isinstance(schema.fields[0].field_type, StringType)


class TestNullableMapping:
    """Test nullable field mapping to required parameter."""

    def test_nullable_false_becomes_required_true(self):
        """Test Field(nullable=False) -> required=True."""

        class SimpleSchema(DataFrameModel):
            """Pandera schema used for this test case."""

            id: str = Field(nullable=False)

        schema = pandera_to_iceberg(SimpleSchema, add_dlt_metadata=False, add_phlo_metadata=False)

        assert schema.fields[0].required is True

    def test_nullable_true_becomes_required_false(self):
        """Test Field(nullable=True) -> required=False."""

        class SimpleSchema(DataFrameModel):
            """Pandera schema used for this test case."""

            nickname: str = Field(nullable=True)

        schema = pandera_to_iceberg(SimpleSchema, add_dlt_metadata=False, add_phlo_metadata=False)

        assert schema.fields[0].required is False

    def test_default_nullable_is_false_required(self):
        """Test default behavior when nullable not specified."""

        class SimpleSchema(DataFrameModel):
            """Pandera schema used for this test case."""

            value: int

        schema = pandera_to_iceberg(SimpleSchema, add_dlt_metadata=False, add_phlo_metadata=False)

        # Pandera's default is nullable=False -> required=True
        assert schema.fields[0].required is True


class TestFieldDescriptions:
    """Test description extraction from Pandera Field to PyIceberg doc."""

    def test_description_extracted_to_doc(self):
        """Test Field(description="...") -> doc parameter."""

        class SimpleSchema(DataFrameModel):
            """Pandera schema used for this test case."""

            user_id: str = Field(description="Unique user identifier")

        schema = pandera_to_iceberg(SimpleSchema, add_dlt_metadata=False, add_phlo_metadata=False)

        assert schema.fields[0].doc == "Unique user identifier"

    def test_empty_doc_when_no_description(self):
        """Test doc is empty string when no description provided."""

        class SimpleSchema(DataFrameModel):
            """Pandera schema used for this test case."""

            value: int

        schema = pandera_to_iceberg(SimpleSchema, add_dlt_metadata=False)

        assert schema.fields[0].doc == ""


class TestFieldOrdering:
    """Test sequential field ID assignment."""

    def test_sequential_field_ids(self):
        """Test fields get sequential IDs starting from start_field_id."""

        class SimpleSchema(DataFrameModel):
            """Pandera schema used for this test case."""

            first: str
            second: int
            third: bool

        schema = pandera_to_iceberg(SimpleSchema, add_dlt_metadata=False)

        assert schema.fields[0].field_id == 1
        assert schema.fields[1].field_id == 2
        assert schema.fields[2].field_id == 3

    def test_custom_start_field_id(self):
        """Test custom start_field_id parameter."""

        class SimpleSchema(DataFrameModel):
            """Pandera schema used for this test case."""

            name: str

        schema = pandera_to_iceberg(
            SimpleSchema, start_field_id=10, add_dlt_metadata=False, add_phlo_metadata=False
        )

        assert schema.fields[0].field_id == 10


class TestDLTMetadataFields:
    """Test automatic DLT metadata field injection."""

    def test_dlt_fields_added_by_default(self):
        """Test _dlt_load_id and _dlt_id automatically added."""

        class SimpleSchema(DataFrameModel):
            """Pandera schema used for this test case."""

            id: str

        schema = pandera_to_iceberg(SimpleSchema, add_phlo_metadata=False)

        field_names = {f.name for f in schema.fields}
        assert "_dlt_load_id" in field_names
        assert "_dlt_id" in field_names

    def test_dlt_fields_have_correct_ids(self):
        """Test DLT fields get IDs 100+."""

        class SimpleSchema(DataFrameModel):
            """Pandera schema used for this test case."""

            id: str

        schema = pandera_to_iceberg(SimpleSchema, add_phlo_metadata=False)

        dlt_load_id = next(f for f in schema.fields if f.name == "_dlt_load_id")
        dlt_id = next(f for f in schema.fields if f.name == "_dlt_id")

        assert dlt_load_id.field_id == 100
        assert dlt_id.field_id == 101

    def test_dlt_fields_are_required(self):
        """Test DLT fields are marked as required."""

        class SimpleSchema(DataFrameModel):
            """Pandera schema used for this test case."""

            id: str

        schema = pandera_to_iceberg(SimpleSchema, add_phlo_metadata=False)

        dlt_load_id = next(f for f in schema.fields if f.name == "_dlt_load_id")
        dlt_id = next(f for f in schema.fields if f.name == "_dlt_id")

        assert dlt_load_id.required is True
        assert dlt_id.required is True

    def test_dlt_fields_not_added_when_disabled(self):
        """Test add_dlt_metadata=False skips DLT fields."""

        class SimpleSchema(DataFrameModel):
            """Pandera schema used for this test case."""

            id: str

        schema = pandera_to_iceberg(SimpleSchema, add_dlt_metadata=False, add_phlo_metadata=False)

        field_names = {f.name for f in schema.fields}
        assert "_dlt_load_id" not in field_names
        assert "_dlt_id" not in field_names

    def test_phlo_ingested_at_gets_special_id(self):
        """Test _phlo_ingested_at gets ID 102."""

        class SimpleSchema(DataFrameModel):
            """Pandera schema used for this test case."""

            id: str
            _phlo_ingested_at: datetime

        schema = pandera_to_iceberg(SimpleSchema, add_dlt_metadata=False, add_phlo_metadata=False)

        phlo_field = next(f for f in schema.fields if f.name == "_phlo_ingested_at")
        assert phlo_field.field_id == 102


class TestErrorHandling:
    """Test error handling and validation."""

    def test_empty_schema_raises_error(self):
        """Test schema with no fields raises SchemaConversionError."""

        class EmptySchema(DataFrameModel):
            """Pandera schema intentionally defined without data fields."""

            class Config:
                """Pandera configuration for this test schema."""

                strict = True

        with pytest.raises(SchemaConversionError, match="No fields found"):
            pandera_to_iceberg(EmptySchema, add_dlt_metadata=False)

    def test_unsupported_type_raises_error(self):
        """Test unsupported type raises SchemaConversionError."""

        class BadSchema(DataFrameModel):
            """Pandera schema with an unsupported field type for error testing."""

            data: list  # type: ignore

        with pytest.raises(SchemaConversionError, match="Cannot map Pandera type"):
            pandera_to_iceberg(BadSchema, add_dlt_metadata=False)


class TestComplexSchemas:
    """Test conversion of complex real-world schemas."""

    def test_github_user_events_schema(self):
        """Test conversion of realistic GitHub events schema."""

        class GitHubEvents(DataFrameModel):
            """Pandera schema representing GitHub events test data."""

            id: str = Field(nullable=False, unique=True, description="Event ID")
            type: str = Field(nullable=False)
            actor: str = Field(nullable=False)
            repo: str = Field(nullable=False)
            created_at: datetime = Field(nullable=False)
            public: bool = Field(nullable=False)

        schema = pandera_to_iceberg(GitHubEvents, add_phlo_metadata=False)

        # Should have 6 data fields + 2 DLT fields
        assert len(schema.fields) == 8

        # Check ID field
        id_field = schema.fields[0]
        assert id_field.name == "id"
        assert id_field.required is True
        assert id_field.doc == "Event ID"

        # Check datetime field
        created_field = next(f for f in schema.fields if f.name == "created_at")
        assert isinstance(created_field.field_type, TimestamptzType)
        assert created_field.required is True

    def test_glucose_entries_schema(self):
        """Test conversion of Nightscout glucose schema."""

        class GlucoseEntries(DataFrameModel):
            """Pandera schema representing glucose entries test data."""

            _id: str = Field(nullable=False, unique=True)
            sgv: int = Field(ge=1, le=1000, nullable=False)
            date: int = Field(nullable=False)
            date_string: datetime = Field(nullable=False)
            direction: str | None = Field(nullable=True)

        schema = pandera_to_iceberg(GlucoseEntries, add_phlo_metadata=False)

        # 5 data fields + 2 DLT fields
        assert len(schema.fields) == 7

        # Check integer field
        sgv_field = next(f for f in schema.fields if f.name == "sgv")
        assert isinstance(sgv_field.field_type, LongType)
        assert sgv_field.required is True

        # Check optional string
        direction_field = next(f for f in schema.fields if f.name == "direction")
        assert isinstance(direction_field.field_type, StringType)
        assert direction_field.required is False


class TestConfigClassHandling:
    """Test that Pandera Config class is properly skipped."""

    def test_config_class_skipped(self):
        """Test Pandera Config class doesn't become a field."""

        class SchemaWithConfig(DataFrameModel):
            """Pandera schema used to validate nested Config handling."""

            id: str

            class Config:
                """Pandera configuration for this test schema."""

                strict = True
                coerce = True

        schema = pandera_to_iceberg(
            SchemaWithConfig, add_dlt_metadata=False, add_phlo_metadata=False
        )

        # Should only have 'id' field, not 'Config'
        field_names = [f.name for f in schema.fields]
        assert field_names == ["id"]
        assert "Config" not in field_names
