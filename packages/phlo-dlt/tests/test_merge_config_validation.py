"""Tests for shared write acceptance and decorator-only merge defaults."""

from types import SimpleNamespace

import pytest

from phlo.exceptions import PhloConfigError
from phlo.logging import get_logger
from phlo_dlt.decorator import _default_merge_config
from phlo_dlt.dlt_helpers import merge_to_table_store, validate_merge_config
from phlo_dlt.registry import TableConfig


@pytest.mark.parametrize(
    "options, message",
    [
        ({"merge_strategy": "typo"}, "Invalid merge_strategy"),
        ({"merge_config": []}, "merge_config must be a dict"),
        ({"merge_config": {"unknown": True}}, "Unsupported merge_config option"),
        ({"merge_strategy": "history"}, "entity_key"),
    ],
)
def test_standalone_write_rejects_options_before_table_creation(options, message) -> None:
    def reject_mutation(**kwargs):
        pytest.fail("invalid options must not create a table")

    with pytest.raises(PhloConfigError, match=message):
        merge_to_table_store(
            log=get_logger("test_dlt_write_preflight"),
            table_store=SimpleNamespace(ensure_table=reject_mutation),
            table_config=TableConfig("entries", object(), None, "name", "raw"),
            parquet_paths=[],
            branch_name="dev",
            **options,
        )


@pytest.mark.parametrize(
    "merge_config",
    [
        {"deduplication": True, "deduplication_method": "last"},
        {"deduplication": True, "deduplication_method": "first"},
        {
            "deduplication": True,
            "deduplication_method": "last",
            "deduplication_order_by": "updated_at",
        },
        {},
        None,
    ],
)
def test_valid_merge_configs_pass(merge_config) -> None:
    validate_merge_config("merge", "event_id", merge_config)


def test_unknown_merge_config_key_is_rejected() -> None:
    with pytest.raises(PhloConfigError, match="Unsupported merge_config option"):
        validate_merge_config("merge", "event_id", {"deduplication": True, "strategy_hint": "foo"})


@pytest.mark.parametrize("method", ["earliest", "newest", 1, "", "LAST"])
def test_unsupported_deduplication_method_is_rejected(method) -> None:
    with pytest.raises(PhloConfigError, match="Unsupported merge_config deduplication_method"):
        validate_merge_config("merge", "event_id", {"deduplication_method": method})


def test_non_string_order_column_is_rejected() -> None:
    with pytest.raises(PhloConfigError, match="deduplication_order_by"):
        validate_merge_config("merge", "event_id", {"deduplication_order_by": ["updated_at"]})


def test_non_boolean_deduplication_is_rejected() -> None:
    with pytest.raises(PhloConfigError, match="must be a boolean"):
        validate_merge_config("merge", "event_id", {"deduplication": "yes"})


def test_dedup_without_unique_key_is_rejected() -> None:
    with pytest.raises(PhloConfigError, match="deduplication requires a unique_key"):
        validate_merge_config("merge", "", {"deduplication": True})


def test_merge_strategy_defaults_dedup_to_last() -> None:
    config = _default_merge_config("merge", {})
    assert config == {"deduplication": True, "deduplication_method": "last"}


def test_append_strategy_defaults_dedup_off() -> None:
    config = _default_merge_config("append", {})
    assert config == {"deduplication": False}
