"""Tests for phlo-observe instrumentation of the WAP lifecycle.

``_emit_wap_observation`` must carry the physical Dagster run id, the run's
audit branch, and the owning catalog system in ``catalog_change`` so the
observe hook plugin can bind translated events to the correct run row and
``branch://<system>/`` entity. ``prepare_wap_launch`` emits
``wap.branch.create`` around catalog ref creation.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest

import phlo.telemetry as phlo_observe
from phlo._correlation import ProjectIdentity
from phlo.capabilities.interfaces import SnapshotPromotionCatalog, VersionedCatalog


def _run(
    *,
    run_id: str = "phys-1",
    logical: str = "logical-1",
    branch: str = "pipeline-run-logical-1",
    project_id: str = "proj",
    attempt: int = 1,
    catalog_system: str | None = None,
) -> Any:
    tags = {
        "phlo/run_id": logical,
        "phlo/wap_branch": branch,
        "phlo/ref": branch,
        "phlo/project_id": project_id,
        "phlo/attempt": str(attempt),
    }
    if catalog_system:
        tags["phlo/catalog_system"] = catalog_system
    return SimpleNamespace(run_id=run_id, tags=tags)


def test_observation_carries_physical_run_and_wap_branch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[dict[str, Any]] = []
    monkeypatch.setattr(
        "phlo_dagster.wap_sensors.emit_observation",
        lambda **kw: captured.append(kw),
    )
    from phlo_dagster.wap_sensors import _emit_wap_observation

    _emit_wap_observation(
        run=_run(),
        status="success",
        run_status="success",
        operation="promotion",
        catalog_ref="main",
        merge_outcome="promoted",
    )
    assert len(captured) == 1
    change = captured[0]["catalog_change"]
    assert change["dagster_run_id"] == "phys-1"
    assert change["wap_branch"] == "pipeline-run-logical-1"
    assert change["operation"] == "promotion"
    assert change["catalog_ref"] == "main"
    assert change["catalog_system"] is None


def test_observation_carries_catalog_system(monkeypatch: pytest.MonkeyPatch) -> None:
    """Snapshot-strategy runs carry phlo/catalog_system so hook translation
    binds the staging ref to its real owner instead of assuming Nessie."""
    captured: list[dict[str, Any]] = []
    monkeypatch.setattr(
        "phlo_dagster.wap_sensors.emit_observation",
        lambda **kw: captured.append(kw),
    )
    from phlo_dagster.wap_sensors import _emit_wap_observation

    _emit_wap_observation(
        run=_run(catalog_system="polaris"),
        status="success",
        run_status="success",
        operation="promotion",
        catalog_ref="main",
        merge_outcome="promoted",
    )
    assert len(captured) == 1
    change = captured[0]["catalog_change"]
    assert change["wap_branch"] == "pipeline-run-logical-1"
    assert change["catalog_system"] == "polaris"


def test_observation_survives_missing_tags(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: list[dict[str, Any]] = []
    monkeypatch.setattr(
        "phlo_dagster.wap_sensors.emit_observation",
        lambda **kw: captured.append(kw),
    )
    from phlo_dagster.wap_sensors import _emit_wap_observation

    # No wap branch tag: downstream translation falls back to catalog_ref.
    run = _run()
    run.tags.pop("phlo/wap_branch")
    _emit_wap_observation(
        run=run,
        status="success",
        operation="cleanup",
        catalog_ref="pipeline-run-logical-1",
    )
    change = captured[0]["catalog_change"]
    assert change["dagster_run_id"] == "phys-1"
    assert change["wap_branch"] is None


def _patch_launch_deps(
    monkeypatch: pytest.MonkeyPatch, catalog: Any, *, system: str = "nessie"
) -> None:
    monkeypatch.setattr(
        "phlo_dagster.wap_launch.resolve_project_identity",
        lambda **kw: ProjectIdentity(project_id="proj"),
    )
    resolution = MagicMock()
    resolution.name = system
    resolution.provider = catalog
    resolution.support.supports_refs = True
    resolution.support.supports_promote = True
    monkeypatch.setattr("phlo_dagster.wap_launch.resolve_capability", lambda _name: resolution)
    # ``load_wap_config`` is imported lazily inside prepare_wap_launch from
    # phlo.infrastructure; patch it there.
    monkeypatch.setattr(
        "phlo.infrastructure.load_wap_config",
        lambda: MagicMock(strategy="branch"),
    )


def test_wap_launch_emits_branch_create(monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> None:
    monkeypatch.setenv("PHLO_PROJECT_PATH", str(tmp_path))
    emitted: dict[str, Any] = {}
    emitted_entities: dict[str, str] = {}

    class _FakeScope:
        def __enter__(self) -> Any:
            return self

        def __exit__(self, *exc: Any) -> None:
            return None

        def set(self, **attrs: Any) -> None:
            emitted.update(attrs)

        def set_entity(self, role: str, identifier: Any) -> None:
            emitted_entities[role] = str(identifier)

    scopes: list[dict[str, Any]] = []
    monkeypatch.setattr(
        phlo_observe,
        "wap_branch_create",
        lambda **kw: (scopes.append(kw), _FakeScope())[1],
    )
    monkeypatch.setattr(
        phlo_observe,
        "branch_entity_id",
        lambda branch, *, system="nessie": f"branch://{system}/{branch}",
    )

    catalog = MagicMock(spec=VersionedCatalog)
    catalog.get_branch_hash.side_effect = lambda ref: "h0" if ref == "main" else None
    catalog.create_branch.return_value = "h1"
    _patch_launch_deps(monkeypatch, catalog)

    from phlo_dagster.wap_launch import prepare_wap_launch

    launch = prepare_wap_launch(logical_run_id="logical-9")
    assert launch.branch == "pipeline-run-logical-9"
    assert scopes == [{"branch": "pipeline-run-logical-9", "base_branch": "main"}]
    assert emitted["phlo_run_id"] == "logical-9"
    assert emitted["strategy"] == "branch"
    assert emitted_entities["branch"] == "branch://nessie/pipeline-run-logical-9"
    assert launch.tags["phlo/catalog_system"] == "nessie"


def test_branch_launch_restamps_entity_with_catalog_system(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    """wap_branch_create pins the branch entity to nessie; the launch path
    must restamp it with the resolved catalog system so a non-Nessie
    VersionedCatalog owns its branch entities (snapshot-path convention)."""
    monkeypatch.setenv("PHLO_PROJECT_PATH", str(tmp_path))
    entities: dict[str, str] = {}

    class _FakeScope:
        def __enter__(self) -> Any:
            return self

        def __exit__(self, *exc: Any) -> None:
            return None

        def set(self, **attrs: Any) -> None:
            pass

        def set_entity(self, role: str, identifier: Any) -> None:
            entities[role] = str(identifier)

    monkeypatch.setattr(phlo_observe, "wap_branch_create", lambda **kw: _FakeScope())
    monkeypatch.setattr(
        phlo_observe,
        "branch_entity_id",
        lambda branch, *, system="nessie": f"branch://{system}/{branch}",
    )

    catalog = MagicMock(spec=VersionedCatalog)
    catalog.get_branch_hash.side_effect = lambda ref: "h0" if ref == "main" else None
    catalog.create_branch.return_value = "h1"
    _patch_launch_deps(monkeypatch, catalog, system="polaris")

    from phlo_dagster.wap_launch import prepare_wap_launch

    launch = prepare_wap_launch(logical_run_id="logical-9")
    assert entities["branch"] == "branch://polaris/pipeline-run-logical-9"
    assert launch.tags["phlo/catalog_system"] == "polaris"


def test_snapshot_launch_names_catalog_system(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    """The snapshot strategy must tag the run with the resolved catalog's
    name so downstream entities read branch://polaris/... — not Nessie."""
    monkeypatch.setenv("PHLO_PROJECT_PATH", str(tmp_path))
    emitted_events: list[dict[str, Any]] = []
    monkeypatch.setattr(
        phlo_observe,
        "emit",
        lambda _name, **kw: emitted_events.append({"name": _name, **kw}),
    )
    # The SDK is optional in tests; stand in for the canonical identifier.
    monkeypatch.setattr(
        phlo_observe,
        "branch_entity_id",
        lambda branch, *, system="nessie": f"branch://{system}/{branch}",
    )

    catalog = MagicMock(spec=SnapshotPromotionCatalog)
    catalog.release_revision.return_value = 7
    _patch_launch_deps(monkeypatch, catalog, system="polaris")
    resolution = MagicMock()
    resolution.name = "polaris"
    resolution.provider = catalog
    resolution.support.supports_promote = True
    resolution.support.supports_snapshots = True
    monkeypatch.setattr("phlo_dagster.wap_launch.resolve_capability", lambda _name: resolution)
    monkeypatch.setattr(
        "phlo.infrastructure.load_wap_config",
        lambda: MagicMock(strategy="snapshot"),
    )

    from phlo_dagster.wap_launch import prepare_wap_launch

    launch = prepare_wap_launch(logical_run_id="logical-9")
    assert launch.strategy == "snapshot"
    assert launch.tags["phlo/catalog_system"] == "polaris"
    create = next(e for e in emitted_events if e["name"] == "wap.branch.create")
    assert create["entities"]["branch"] == "branch://polaris/pipeline-run-logical-9"


def test_wap_launch_branch_create_failure_records_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    """A failed create_branch propagates through the observe scope so the
    event records failure rather than a silent success."""
    monkeypatch.setenv("PHLO_PROJECT_PATH", str(tmp_path))
    outcomes: list[Any] = []

    class _RecordingScope:
        def __enter__(self) -> Any:
            return self

        def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
            outcomes.append(exc_type)
            return None

        def set(self, **attrs: Any) -> None:
            pass

    monkeypatch.setattr(phlo_observe, "wap_branch_create", lambda **kw: _RecordingScope())

    catalog = MagicMock(spec=VersionedCatalog)
    catalog.get_branch_hash.side_effect = lambda ref: "h0" if ref == "main" else None
    catalog.create_branch.return_value = None
    _patch_launch_deps(monkeypatch, catalog)

    from phlo.exceptions import PhloConfigError
    from phlo_dagster.wap_launch import prepare_wap_launch

    with pytest.raises(PhloConfigError):
        prepare_wap_launch(logical_run_id="logical-x")
    assert outcomes == [PhloConfigError]
