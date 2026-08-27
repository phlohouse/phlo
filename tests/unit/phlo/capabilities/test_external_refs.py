"""Tests cross-provider external-reference validation of merged asset specs.

Providers record deps they cannot resolve against their own specs in the
``phlo/external_deps`` metadata entry (see the dbt provider). Once every
provider has registered its specs, validate_external_asset_references warns
for recorded keys that no registered spec provides and stays silent when the
referenced key exists anywhere in the graph.
"""

from __future__ import annotations

import logging as _logging

import pytest

from phlo.capabilities.external_refs import validate_external_asset_references
from phlo.capabilities.specs import AssetSpec


def _spec(key: str, *, deps: list[str] | None = None, metadata: dict | None = None) -> AssetSpec:
    return AssetSpec(
        key=key,
        group="transform",
        description=None,
        deps=deps or [],
        metadata=metadata or {},
    )


@pytest.fixture(name="ref_warnings")
def _ref_warnings():
    """Capture structured warnings from the external-refs logger.

    caplog's root-level handler is unreliable for phlo's structlog bridge,
    so a handler is attached to the emitting logger directly.
    """
    import logging as _logging

    records: list[_logging.LogRecord] = []

    class _Capture(_logging.Handler):
        def emit(self, record: _logging.LogRecord) -> None:
            records.append(record)

    logger = _logging.getLogger("phlo.capabilities.external_refs")
    handler = _Capture(level=_logging.WARNING)
    logger.addHandler(handler)
    try:
        yield records
    finally:
        logger.removeHandler(handler)


def _warning_records(records) -> list[_logging.LogRecord]:
    return [
        record
        for record in records
        if record.getMessage().find("asset_external_reference_unresolved") != -1
    ]


def test_silent_when_external_reference_resolves(ref_warnings) -> None:
    dbt = _spec(
        "finance_domain.invoice_aging",
        deps=["dlt_finance_invoices"],
        metadata={"phlo/external_deps": ["dlt_finance_invoices"]},
    )
    dlt = _spec("dlt_finance_invoices")

    validate_external_asset_references([dbt, dlt])

    assert _warning_records(ref_warnings) == []


def test_warns_when_external_reference_missing(ref_warnings) -> None:
    dbt = _spec(
        "finance_domain.invoice_aging",
        deps=["dlt_finance_invoices"],
        metadata={"phlo/external_deps": ["dlt_finance_invoices"], "dbt_project": "finance_domain"},
    )

    validate_external_asset_references([dbt])

    records = _warning_records(ref_warnings)
    assert len(records) == 1
    assert "finance_domain.invoice_aging" in records[0].getMessage()
    assert "dlt_finance_invoices" in records[0].getMessage()


def test_warns_once_per_spec_and_key_pair(ref_warnings) -> None:
    one = _spec(
        "a.model",
        metadata={"phlo/external_deps": ["dlt_missing", "dlt_missing"]},
    )
    two = _spec("b.model", deps=[])

    validate_external_asset_references([one, two])

    assert len(_warning_records(ref_warnings)) == 1


def test_specs_without_external_metadata_are_skipped(ref_warnings) -> None:
    validate_external_asset_references([_spec("solo.asset", deps=["nowhere"])])

    assert _warning_records(ref_warnings) == []


def test_missing_key_referenced_by_two_specs_warns_twice(ref_warnings) -> None:
    one = _spec("a.model", metadata={"phlo/external_deps": ["dlt_missing"]})
    two = _spec("b.model", metadata={"phlo/external_deps": ["dlt_missing"]})

    validate_external_asset_references([one, two])

    assert len(_warning_records(ref_warnings)) == 2


def test_resolvable_key_from_any_provider_silences_warning(ref_warnings) -> None:
    """Any registered provider's key satisfies the reference, not just dlt keys."""
    dbt = _spec("dbt.model", metadata={"phlo/external_deps": ["sling.uploads"]})
    sling = _spec("sling.uploads")

    validate_external_asset_references([dbt, sling])

    assert _warning_records(ref_warnings) == []
