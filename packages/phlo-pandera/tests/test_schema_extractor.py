"""Tests for PanderaSchemaExtractor.

Extraction maps dtype annotations to canonical dtype strings, honours
Field(nullable=...) nullability, unwraps pandera Series[T] annotations,
and skips dunder/class Config attributes. Unsupported dtypes raise
ValueError; the extractor satisfies the SchemaExtractor protocol.
"""

from __future__ import annotations

import pytest
from pandera.pandas import Field
from pandera.typing import Series

from phlo.capabilities.interfaces import SchemaExtractor
from phlo_pandera.schema_extractor import PanderaSchemaExtractor, _map_dtype
from phlo_pandera.schemas.base import PhloSchema


class SimpleSchema(PhloSchema):
    name: str
    age: int
    score: float
    active: bool


class NullableSchema(PhloSchema):
    required_id: str = Field(nullable=False)
    optional_name: str | None = Field(nullable=True)
    optional_score: float | None = Field(nullable=True)


class SchemaWithConfig(PhloSchema):
    id: str

    class Config:
        strict = True


class PanderaSeriesSchema(PhloSchema):
    id: Series[int] = Field(nullable=False)
    title: Series[str]
    price: Series[float] = Field(nullable=True)


pytestmark = pytest.mark.core_regression


class TestPanderaSchemaExtractor:
    """Tests for PanderaSchemaExtractor."""

    def test_simple_extraction(self):
        extractor = PanderaSchemaExtractor()
        result = extractor.extract(SimpleSchema)

        assert len(result.fields) == 4
        by_name = {f.name: f for f in result.fields}

        assert by_name["name"].dtype == "string"
        assert by_name["age"].dtype == "int64"
        assert by_name["score"].dtype == "float64"
        assert by_name["active"].dtype == "bool"

    def test_nullable_fields(self):
        extractor = PanderaSchemaExtractor()
        result = extractor.extract(NullableSchema)

        by_name = {f.name: f for f in result.fields}

        assert by_name["optional_name"].nullable is True
        assert by_name["optional_score"].nullable is True

    def test_non_nullable_fields(self):
        extractor = PanderaSchemaExtractor()
        result = extractor.extract(NullableSchema)

        by_name = {f.name: f for f in result.fields}
        assert by_name["required_id"].nullable is False

    def test_skips_dunder_and_config(self):
        extractor = PanderaSchemaExtractor()
        result = extractor.extract(SchemaWithConfig)

        names = {f.name for f in result.fields}
        assert "Config" not in names
        assert not any(n.startswith("__") for n in names)
        assert "id" in names

    def test_satisfies_schema_extractor_protocol(self):
        extractor = PanderaSchemaExtractor()
        assert isinstance(extractor, SchemaExtractor)

    def test_map_dtype_unsupported(self):
        with pytest.raises(ValueError, match="Unsupported type"):
            _map_dtype(list)

    def test_unwraps_pandera_series_annotations(self):
        extractor = PanderaSchemaExtractor()
        result = extractor.extract(PanderaSeriesSchema)

        by_name = {f.name: f for f in result.fields}
        assert by_name["id"].dtype == "int64"
        assert by_name["title"].dtype == "string"
        assert by_name["price"].dtype == "float64"
        assert by_name["price"].nullable is True
        assert by_name["id"].nullable is False
