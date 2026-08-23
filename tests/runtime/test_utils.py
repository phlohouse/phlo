"""Tests for dict/list utilities: None-stripping compaction and
order-preserving dedupe."""

from __future__ import annotations

from phlo.utils import compact_dict, dedupe_preserve_order


def test_compact_dict_removes_none_values() -> None:
    result = compact_dict({"a": 1, "b": None, "c": 0, "d": ""})
    assert result == {"a": 1, "c": 0, "d": ""}


def test_compact_dict_preserves_non_none() -> None:
    result = compact_dict({"x": "hello", "y": 42, "z": False})
    assert result == {"x": "hello", "y": 42, "z": False}


def test_compact_dict_empty_input() -> None:
    result = compact_dict({})
    assert result == {}


def test_dedupe_preserve_order_removes_duplicates() -> None:
    result = dedupe_preserve_order([1, 2, 1, 3, 2, 4])
    assert result == [1, 2, 3, 4]


def test_dedupe_preserve_order_keeps_first_occurrence() -> None:
    result = dedupe_preserve_order(["a", "b", "a", "c", "b"])
    assert result == ["a", "b", "c"]


def test_dedupe_preserve_order_empty_input() -> None:
    result = dedupe_preserve_order([])
    assert result == []


def test_dedupe_preserve_order_no_duplicates() -> None:
    result = dedupe_preserve_order([1, 2, 3])
    assert result == [1, 2, 3]


def test_dedupe_preserve_order_with_mixed_types() -> None:
    result = dedupe_preserve_order([1, "1", 1, True, "a", "a"])
    assert result == [1, "1", "a"]
