"""Tests for strict validation mode in validate_with_pandera.

Strict mode raises pandera SchemaErrors on invalid data; non-strict mode
(the backwards-compatible default) returns False instead.
"""

from __future__ import annotations

from types import SimpleNamespace

import pandera.errors
import pytest
from pandera.pandas import DataFrameModel
from pandera.typing import Series  # type: ignore[possibly-missing-import]
from phlo_dlt.dlt_helpers import validate_with_pandera

from phlo.logging import get_logger


class StrictTestSchema(DataFrameModel):
    """Test schema with string and int columns."""

    name: Series[str]
    value: Series[int]


@pytest.fixture
def mock_context():
    """Create a mock Dagster context with a logger."""
    return SimpleNamespace(log=get_logger("phlo.tests.strict_validation"))


class TestValidateWithPanderaStrictMode:
    """Test strict mode behavior in validate_with_pandera."""

    def test_valid_data_strict_false_returns_true(self, mock_context) -> None:
        """Valid data with strict=False should return True."""
        data = [{"name": "test", "value": 1}]
        result = validate_with_pandera(mock_context, data, StrictTestSchema, strict=False)
        assert result is True

    def test_valid_data_strict_true_returns_true(self, mock_context) -> None:
        """Valid data with strict=True should return True."""
        data = [{"name": "test", "value": 1}]
        result = validate_with_pandera(mock_context, data, StrictTestSchema, strict=True)
        assert result is True

    def test_invalid_data_strict_false_returns_false(self, mock_context) -> None:
        """Invalid data with strict=False should return False without raising."""
        data = [{"name": "test", "value": "not_an_int"}]
        result = validate_with_pandera(mock_context, data, StrictTestSchema, strict=False)
        assert result is False

    def test_invalid_data_strict_true_raises_schema_errors(self, mock_context) -> None:
        """Invalid data with strict=True should raise SchemaErrors."""
        data = [{"name": "test", "value": "not_an_int"}]
        with pytest.raises(pandera.errors.SchemaErrors):
            validate_with_pandera(mock_context, data, StrictTestSchema, strict=True)

    def test_strict_default_is_false(self, mock_context) -> None:
        """Default strict value should be False (backwards compatible)."""
        data = [{"name": "test", "value": "not_an_int"}]
        # Should not raise, should return False
        result = validate_with_pandera(mock_context, data, StrictTestSchema)
        assert result is False
