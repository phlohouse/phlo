"""Active transformation provider resolution."""

from __future__ import annotations

from pathlib import Path

import pytest

from phlo.config.transformation import (
    active_transformation_provider,
    normalise_provider,
    set_active_transformation_provider,
)


@pytest.mark.parametrize(
    ("alias", "expected"),
    [
        ("dbt", "dbt"),
        ("DBT", "dbt"),
        ("transform", "transform"),
        ("phlo-transform", "transform"),
        ("  phlo-transform ", "transform"),
    ],
)
def test_aliases_normalise_to_provider_names(alias: str, expected: str) -> None:
    assert normalise_provider(alias) == expected


def test_unset_project_have_no_active_provider(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("PHLO_TRANSFORMATION_PROVIDER", raising=False)

    assert active_transformation_provider(tmp_path) is None


def test_project_marker_selects_provider(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PHLO_TRANSFORMATION_PROVIDER", raising=False)
    marker = tmp_path / ".phlo" / "transformation-provider"
    marker.parent.mkdir()
    marker.write_text("phlo-transform\n")

    assert active_transformation_provider(tmp_path) == "transform"


def test_environment_overrides_project_marker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    marker = tmp_path / ".phlo" / "transformation-provider"
    marker.parent.mkdir()
    marker.write_text("transform\n")
    monkeypatch.setenv("PHLO_TRANSFORMATION_PROVIDER", "dbt")

    assert active_transformation_provider(tmp_path) == "dbt"


def test_set_and_clear_project_marker(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PHLO_TRANSFORMATION_PROVIDER", raising=False)

    path = set_active_transformation_provider("phlo-transform", tmp_path)
    assert path is not None
    assert path.read_text() == "transform\n"
    assert active_transformation_provider(tmp_path) == "transform"

    assert set_active_transformation_provider(None, tmp_path) is None
    assert not path.exists()
    assert active_transformation_provider(tmp_path) is None
