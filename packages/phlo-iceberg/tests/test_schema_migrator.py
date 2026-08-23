"""Tests for IcebergSchemaMigrator.

Settings are pinned to local Nessie/MinIO endpoints, and the cached settings
singleton is cleared around every test so env overrides apply.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from pyiceberg.types import (
    BinaryType,
    BooleanType,
    DateType,
    DecimalType,
    DoubleType,
    FloatType,
    IntegerType,
    ListType,
    LongType,
    StringType,
    TimestampType,
    TimestamptzType,
)

from phlo.capabilities.specs import FieldSpec, NormalizedSchema
from phlo.capabilities.interfaces import SchemaMigrator
from phlo_iceberg.schema_migrator import IcebergSchemaMigrator, _iceberg_type_to_dtype

pytestmark = pytest.mark.core_regression


@pytest.fixture(autouse=True)
def _mock_settings(monkeypatch):
    monkeypatch.setenv("ICEBERG_NESSIE_URI", "http://localhost:19120/api/v1")
    monkeypatch.setenv("ICEBERG_S3_ENDPOINT", "http://localhost:9000")
    monkeypatch.setenv("ICEBERG_S3_ACCESS_KEY", "test")
    monkeypatch.setenv("ICEBERG_S3_SECRET_KEY", "test")
    monkeypatch.setenv("ICEBERG_WAREHOUSE", "s3://test/warehouse")
    # Clear cached settings so env vars take effect
    from phlo_iceberg.settings import get_settings

    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


# -- protocol conformance ---------------------------------------------------


class TestProtocolConformance:
    def test_implements_schema_migrator_protocol(self):
        migrator = IcebergSchemaMigrator(ref="main")
        assert isinstance(migrator, SchemaMigrator)


# -- supported_changes -------------------------------------------------------


class TestSupportedChanges:
    def test_returns_expected_set(self):
        migrator = IcebergSchemaMigrator(ref="main")
        expected = {
            "add",
            "drop",
            "rename",
            "widen_type",
            "narrow_type",
            "reorder",
            "nullability_relaxed",
            "nullability_tightened",
        }
        assert migrator.supported_changes() == expected


# -- classify_change ---------------------------------------------------------


class TestClassifyChange:
    def setup_method(self):
        self.migrator = IcebergSchemaMigrator(ref="main")

    def test_rename_is_safe(self):
        assert self.migrator.classify_change("rename") == "safe"

    def test_drop_is_warning(self):
        assert self.migrator.classify_change("drop") == "warning"

    def test_add_nullable_is_safe(self):
        assert self.migrator.classify_change("add", nullable=True) == "safe"

    def test_add_non_nullable_with_default_is_warning(self):
        assert self.migrator.classify_change("add", nullable=False, has_default=True) == "warning"

    def test_add_non_nullable_no_default_is_breaking(self):
        assert self.migrator.classify_change("add", nullable=False, has_default=False) == "breaking"

    def test_widen_type_is_safe(self):
        assert self.migrator.classify_change("widen_type") == "safe"

    def test_narrow_type_is_breaking(self):
        assert self.migrator.classify_change("narrow_type") == "breaking"

    def test_nullability_relaxed_is_safe(self):
        assert self.migrator.classify_change("nullability_relaxed") == "safe"

    def test_nullability_tightened_is_breaking(self):
        assert self.migrator.classify_change("nullability_tightened") == "breaking"

    def test_unknown_change_is_breaking(self):
        assert self.migrator.classify_change("unknown_operation") == "breaking"


# -- _iceberg_type_to_dtype --------------------------------------------------


class TestIcebergTypeToDtype:
    @pytest.mark.parametrize(
        ("iceberg_type", "expected"),
        [
            (StringType(), "string"),
            (LongType(), "int64"),
            (IntegerType(), "int32"),
            (DoubleType(), "float64"),
            (FloatType(), "float32"),
            (BooleanType(), "bool"),
            (TimestamptzType(), "timestamptz"),
            (TimestampType(), "timestamp"),
            (DateType(), "date"),
            (BinaryType(), "binary"),
            (DecimalType(precision=10, scale=2), "decimal"),
        ],
    )
    def test_known_types(self, iceberg_type, expected):
        assert _iceberg_type_to_dtype(iceberg_type) == expected

    def test_unknown_type_falls_back_to_str(self):
        list_type = ListType(element_id=1, element=StringType(), element_required=False)
        result = _iceberg_type_to_dtype(list_type)
        assert isinstance(result, str)
        assert result == str(list_type)


class TestDiffSchema:
    def test_ignores_system_metadata_columns(self, monkeypatch):
        migrator = IcebergSchemaMigrator(ref="main")

        current_schema = SimpleNamespace(
            fields=[
                SimpleNamespace(name="name", field_type=StringType(), required=False),
                SimpleNamespace(name="_dlt_load_id", field_type=StringType(), required=False),
                SimpleNamespace(
                    name="_phlo_ingested_at", field_type=TimestamptzType(), required=False
                ),
            ]
        )
        table = SimpleNamespace(schema=lambda: current_schema)
        catalog = SimpleNamespace(load_table=lambda table_name: table)
        monkeypatch.setattr(
            "phlo_iceberg.schema_migrator.get_catalog",
            lambda ref: catalog,
        )

        desired = NormalizedSchema(
            fields=[
                FieldSpec(name="name", dtype="string", nullable=True),
                FieldSpec(name="habitat", dtype="string", nullable=True),
            ]
        )

        plan = migrator.diff_schema(table_name="raw.pokemon", desired=desired)

        assert [(change.field_name, change.change_type) for change in plan.changes] == [
            ("habitat", "add")
        ]
        assert plan.classification == "safe"
