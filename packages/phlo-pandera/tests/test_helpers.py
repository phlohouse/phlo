"""Verify conversion of data contracts into Pandera checks: null checks from
non-nullable fields, uniqueness, freshness, accepted values, and custom SQL."""

from datetime import datetime

import pytest
from pandera.pandas import Field

from phlo.contracts import SLA
from phlo_pandera.checks import FreshnessCheck, NullCheck, UniqueCheck
from phlo_pandera.checks_extra import CustomSQLCheck
from phlo_pandera.helpers import (
    accepted_values_check,
    checks_from_contract,
    freshness_check_from_sla,
    required_field_null_checks,
    unique_key_check,
)
from phlo_pandera.schemas import PhloSchema


class ExampleSchema(PhloSchema):
    id: int = Field(nullable=False)
    status: str = Field(nullable=False)
    notes: str | None = Field(nullable=True)


def test_required_field_null_checks_uses_non_nullable_schema_fields() -> None:
    checks = required_field_null_checks(ExampleSchema)

    assert len(checks) == 1
    assert isinstance(checks[0], NullCheck)
    assert checks[0].columns == ["id", "status"]


def test_unique_key_check_supports_single_and_composite_keys() -> None:
    single = unique_key_check("id")
    composite = unique_key_check(["tenant_id", "id"])

    assert isinstance(single, UniqueCheck)
    assert single.columns == ["id"]
    assert isinstance(composite, UniqueCheck)
    assert composite.columns == ["tenant_id", "id"]
    assert unique_key_check(None) is None


def test_freshness_check_from_sla_uses_freshness_hours() -> None:
    reference_time = datetime(2026, 5, 16, 12, 0, 0)
    check = freshness_check_from_sla(
        SLA(freshness_hours=6),
        "updated_at",
        reference_time=reference_time,
    )

    assert isinstance(check, FreshnessCheck)
    assert check.timestamp_column == "updated_at"
    assert check.max_age_hours == 6.0
    assert check.reference_time == reference_time
    assert freshness_check_from_sla(SLA(), "updated_at") is None


def test_accepted_values_check_builds_custom_sql_check() -> None:
    check = accepted_values_check("status", ["new", "can't", "done"])

    assert isinstance(check, CustomSQLCheck)
    assert check.name_ == "accepted_values_status"
    assert "\"status\" IN ('new', 'can''t', 'done')" in check.sql


def test_accepted_values_check_rejects_empty_values() -> None:
    with pytest.raises(ValueError, match="requires at least one"):
        accepted_values_check("status", [])


def test_checks_from_contract_combines_available_helpers() -> None:
    checks = checks_from_contract(
        schema=ExampleSchema,
        unique_key="id",
        sla=SLA(freshness_hours=24),
        freshness_column="updated_at",
    )

    assert [type(check) for check in checks] == [NullCheck, UniqueCheck, FreshnessCheck]
