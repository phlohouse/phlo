"""Tests for core quality check plugins.

Each plugin is exercised through create_check() to confirm the generated check
name matches the expected identifier for its check kind.
"""

from phlo_core.quality.freshness_check import FreshnessCheckPlugin
from phlo_core.quality.null_check import NullCheckPlugin
from phlo_core.quality.schema_check import SchemaCheckPlugin
from phlo_core.quality.uniqueness_check import UniquenessCheckPlugin


def test_null_check_plugin():
    """Verify null check plugin creates an expected check name."""
    plugin = NullCheckPlugin()
    check = plugin.create_check(columns=["id"])
    assert check.name == "null_check_id"


def test_uniqueness_check_plugin():
    """Verify uniqueness check plugin creates an expected check name."""
    plugin = UniquenessCheckPlugin()
    check = plugin.create_check(columns=["id"])
    assert check.name == "unique_check_id"


def test_freshness_check_plugin():
    """Verify freshness check plugin creates an expected check name."""
    plugin = FreshnessCheckPlugin()
    check = plugin.create_check(timestamp_column="ts", max_age_hours=2)
    assert check.name == "freshness_check_ts"


def test_schema_check_plugin():
    """Verify schema check plugin creates an expected check name."""
    plugin = SchemaCheckPlugin()

    class DummySchema:
        """Minimal schema stub used by the schema check plugin test."""

        __name__ = "DummySchema"

        def validate(self, df, lazy=True):
            """Return input dataframe unchanged."""
            return df

    check = plugin.create_check(schema=DummySchema)
    assert check.name == "schema_check_DummySchema"
