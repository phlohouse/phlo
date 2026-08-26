"""Regression tests for plugin entry-point compatibility helpers.

entry_points_for_group must work across importlib.metadata shapes:
modern select()-only collections, mapping-like legacy collections, and
the group= keyword form. Workspace-plugin discoverability is asserted by
test_plugin_system.py::test_workspace_entry_point_plugins_are_discoverable.
"""

from __future__ import annotations

import pytest

from phlo.plugins.discovery._entry_points import entry_points_for_group

pytestmark = pytest.mark.core_regression


class _SelectableEntryPoints:
    """Entry-point collection that supports select() but not get()."""

    def __init__(self, groups: dict[str, list[object]]) -> None:
        self._groups = groups

    def select(self, *, group: str) -> list[object]:
        """Return entry points for a group."""
        return self._groups.get(group, [])


def test_entry_points_for_group_supports_selectable_groups_without_get(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Python 3.12+ fallback entry-point collections may not expose get()."""
    expected = [object()]

    def _entry_points(*args: object, **kwargs: object) -> _SelectableEntryPoints:
        if "group" in kwargs:
            raise TypeError("group parameter unsupported")
        return _SelectableEntryPoints({"phlo.plugins.sources": expected})

    monkeypatch.setattr(
        "phlo.plugins.discovery._entry_points.importlib.metadata.entry_points",
        _entry_points,
    )

    assert list(entry_points_for_group("phlo.plugins.sources")) == expected


def test_entry_points_for_group_supports_legacy_mapping(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Older importlib.metadata fallback collections are mapping-like."""
    expected = [object()]

    def _entry_points(*args: object, **kwargs: object) -> dict[str, list[object]]:
        if "group" in kwargs:
            raise TypeError("group parameter unsupported")
        return {"phlo.plugins.services": expected}

    monkeypatch.setattr(
        "phlo.plugins.discovery._entry_points.importlib.metadata.entry_points",
        _entry_points,
    )

    assert list(entry_points_for_group("phlo.plugins.services")) == expected
