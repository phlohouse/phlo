"""The ``phlo.ingestion`` alias must warn on use.

The compatibility alias has a migration codemod
(``phlo migrate decorators-2026-05``) and will be removed in an upcoming
release. These tests pin the warning without preserving the alias indefinitely.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.core_regression


@pytest.fixture
def _capture_ingest_calls(monkeypatch: pytest.MonkeyPatch) -> dict[str, list[tuple]]:
    """Route the alias through sentinels so no real provider is needed."""
    import phlo.ingest

    calls: dict[str, list[tuple]] = {"dlt": [], "assets": []}

    def _dlt(*args: object, **kwargs: object) -> str:
        calls["dlt"].append((args, kwargs))
        return "dlt-decorator"

    def _assets(provider_name: str | None = None) -> list[str]:
        calls["assets"].append((provider_name,))
        return ["asset"]

    monkeypatch.setattr(phlo.ingest, "dlt", _dlt)
    monkeypatch.setattr(phlo.ingest, "assets", _assets)
    return calls


def test_module_call_form_warns(_capture_ingest_calls: dict[str, list[tuple]]) -> None:
    """``@phlo.ingestion(...)`` must emit the deprecation warning."""
    import phlo.ingestion

    with pytest.warns(DeprecationWarning, match="phlo.ingestion is deprecated"):
        decorator = phlo.ingestion(table_name="events")

    assert decorator == "dlt-decorator"
    assert _capture_ingest_calls["dlt"] == [((), {"table_name": "events"})]


def test_phlo_ingestion_function_warns(_capture_ingest_calls: dict[str, list[tuple]]) -> None:
    """``phlo.ingestion.phlo_ingestion(...)`` must emit the deprecation warning."""
    from phlo.ingestion import phlo_ingestion

    with pytest.warns(DeprecationWarning, match="phlo.ingestion is deprecated"):
        decorator = phlo_ingestion(table_name="events")

    assert decorator == "dlt-decorator"


def test_top_level_phlo_ingestion_warns(_capture_ingest_calls: dict[str, list[tuple]]) -> None:
    """The top-level ``phlo.phlo_ingestion`` export routes through the alias and warns."""
    import phlo

    with pytest.warns(DeprecationWarning, match="phlo.ingestion is deprecated"):
        decorator = phlo.phlo_ingestion(table_name="events")

    assert decorator == "dlt-decorator"


def test_get_ingestion_assets_warns(_capture_ingest_calls: dict[str, list[tuple]]) -> None:
    """``phlo.ingestion.get_ingestion_assets()`` must emit the deprecation warning."""
    from phlo.ingestion import get_ingestion_assets

    with pytest.warns(DeprecationWarning, match="phlo.ingestion is deprecated"):
        assets = get_ingestion_assets()

    assert assets == ["asset"]
    assert _capture_ingest_calls["assets"] == [("dlt",)]


def test_warning_names_the_migration_codemod(
    _capture_ingest_calls: dict[str, list[tuple]],
) -> None:
    """The warning must point users at the shipped migration path."""
    import phlo.ingestion

    with pytest.warns(DeprecationWarning, match="phlo migrate decorators-2026-05"):
        phlo.ingestion.phlo_ingestion()
