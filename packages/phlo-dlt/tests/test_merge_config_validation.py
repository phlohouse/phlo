"""Tests for merge_config validation in the phlo.ingest.dlt decorator."""

import pytest

from phlo.exceptions import PhloConfigError
from phlo_dlt.decorator import _default_merge_config, _validate_merge_config


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
    _validate_merge_config("merge", "event_id", merge_config)


def test_unknown_merge_config_key_is_rejected() -> None:
    with pytest.raises(PhloConfigError, match="Unsupported merge_config option"):
        _validate_merge_config("merge", "event_id", {"deduplication": True, "strategy_hint": "foo"})


@pytest.mark.parametrize("method", ["earliest", "newest", 1, "", "LAST"])
def test_unsupported_deduplication_method_is_rejected(method) -> None:
    with pytest.raises(PhloConfigError, match="Unsupported merge_config deduplication_method"):
        _validate_merge_config("merge", "event_id", {"deduplication_method": method})


def test_non_string_order_column_is_rejected() -> None:
    with pytest.raises(PhloConfigError, match="deduplication_order_by"):
        _validate_merge_config("merge", "event_id", {"deduplication_order_by": ["updated_at"]})


def test_non_boolean_deduplication_is_rejected() -> None:
    with pytest.raises(PhloConfigError, match="must be a boolean"):
        _validate_merge_config("merge", "event_id", {"deduplication": "yes"})


def test_dedup_without_unique_key_is_rejected() -> None:
    with pytest.raises(PhloConfigError, match="deduplication requires a unique_key"):
        _validate_merge_config("merge", "", {"deduplication": True})


def test_merge_strategy_defaults_dedup_to_last() -> None:
    config = _default_merge_config("merge", {})
    assert config == {"deduplication": True, "deduplication_method": "last"}


def test_append_strategy_defaults_dedup_off() -> None:
    config = _default_merge_config("append", {})
    assert config == {"deduplication": False}
