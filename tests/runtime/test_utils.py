"""Tests for dict/list utilities: None-stripping compaction and
order-preserving dedupe."""

from __future__ import annotations

import pytest

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


@pytest.mark.parametrize(
    ("values", "expected"),
    [
        ([1, 2, 1, 3, 2, 4], [1, 2, 3, 4]),
        (["a", "b", "a", "c", "b"], ["a", "b", "c"]),
        ([], []),
        ([1, 2, 3], [1, 2, 3]),
        ([1, "1", 1, True, "a", "a"], [1, "1", "a"]),
    ],
    ids=["duplicates", "first-occurrence", "empty", "already-unique", "mixed-types"],
)
def test_dedupe_preserve_order(values: list, expected: list) -> None:
    assert dedupe_preserve_order(values) == expected


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("phlo fixture with spaces", "phlo-fixture-with-spaces"),
        ("My_Lakehouse", "my_lakehouse"),
        ("lake-1", "lake-1"),
        ("  ", "phlo"),
    ],
)
def test_compose_project_name_is_valid_for_compose(name: str, expected: str) -> None:
    """A phlo.yaml name with spaces or capitals still gives Compose a valid project name."""
    from phlo.infrastructure.utils import compose_project_name

    assert compose_project_name(name) == expected
