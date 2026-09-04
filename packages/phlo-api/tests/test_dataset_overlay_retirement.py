"""Overlay-authority retirement tests.

After an explicit overlay migration (``phlo dataset migrate-overlay apply``
or ``discard``), no request path may read or write the legacy
``.phlo/observatory/dataset_workflow.json`` overlay: the retired Observatory
helpers are gone from the module, the legacy file is retained byte-for-byte
untouched, and every surface resolves Dataset state from the durable store.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

import phlo
from phlo.cli.commands.dataset import dataset_group
from security_test_support import authenticated_client  # noqa: F401

RETIRED_OVERLAY_SYMBOLS = (
    "_load_dataset_workflow_state",
    "_write_dataset_workflow_state",
    "_dataset_workflow_write_lock",
    "_workflow_dataset_overlay",
    "_workflow_candidate_overlay",
    "_dataset_workflow_path",
    "_publication_state",
)

LEGACY_OVERLAY: dict = {
    "datasets": {
        "gold.customer_health": {
            "publication_state": "published",
            "approval_state": "approved",
            "owner": "alice",
        }
    },
    "candidates": {},
    "config": {"default_owner": "ops"},
}


@pytest.fixture()
def _declared_customer_health():
    @phlo.contract(
        table="gold.customer_health",
        owner="data-platform",
        metadata={"classification": "internal"},
    )
    def _customer_health_contract() -> None:
        return None

    yield
    phlo.clear_flow_declarations()


@pytest.fixture()
def legacy_overlay(tmp_path: Path) -> Path:
    observatory_dir = tmp_path / ".phlo" / "observatory"
    observatory_dir.mkdir(parents=True)
    overlay = observatory_dir / "dataset_workflow.json"
    overlay.write_text(json.dumps(LEGACY_OVERLAY, indent=2, sort_keys=True), encoding="utf-8")
    return overlay


def _migrate(
    tmp_path: Path,
    overlay: Path,
    *args: str,
) -> dict:
    from phlo_api.observatory_api import observatory

    observatory._clear_read_model_cache()
    env = {"PHLO_PROJECT_PATH": str(tmp_path)}
    plan_path = tmp_path / "PLAN.json"
    planned = CliRunner().invoke(
        dataset_group,
        [
            "migrate-overlay",
            "plan",
            "--source",
            str(overlay),
            "--output",
            str(plan_path),
        ],
        env=env,
    )
    assert planned.exit_code == 0, planned.output
    document = json.loads(plan_path.read_text(encoding="utf-8"))

    applied = CliRunner().invoke(
        dataset_group,
        [
            "migrate-overlay",
            *args,
            "--json",
            "--source",
            str(overlay),
            "--plan",
            str(plan_path),
            "--digest",
            document["plan_digest"],
        ],
        env=env,
    )
    assert applied.exit_code == 0, applied.output
    return json.loads(applied.output)


def test_retired_overlay_symbols_are_absent() -> None:
    from phlo_api.observatory_api import observatory

    present = [name for name in RETIRED_OVERLAY_SYMBOLS if hasattr(observatory, name)]
    assert present == [], f"retired overlay helpers still present: {present}"


def test_request_paths_do_not_touch_overlay_after_apply(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
    observatory_loaders,
    legacy_overlay: Path,
    _declared_customer_health,
) -> None:
    monkeypatch.setenv("PHLO_PROJECT_PATH", str(tmp_path))
    observatory_loaders(
        assets=[_asset("gold.customer_health")],
        tables_without_catalog=[],
        quality=[],
    )
    result = _migrate(tmp_path, legacy_overlay, "apply", "--store-mode", "memory")
    assert result["status"] == "committed"

    overlay_bytes = legacy_overlay.read_bytes()

    client = authenticated_client("admin")
    profile = client.get("/api/observatory/datasets/gold.customer_health").json()
    datasets = client.get("/api/observatory/datasets").json()
    governance = client.get("/api/observatory/governance").json()

    cli_show = CliRunner().invoke(
        dataset_group,
        ["show", "gold.customer_health", "--json", "--store-mode", "memory"],
        env={"PHLO_PROJECT_PATH": str(tmp_path)},
    )
    assert cli_show.exit_code == 0, cli_show.output

    # Every surface resolves state from the durable store, not the overlay.
    assert profile["canonical"]["publication_state"] == "published"
    assert profile["canonical"]["owner"] == "alice"
    assert json.loads(cli_show.output)["publication_state"] == "published"
    assert any(
        item["id"] == "gold.customer_health" and item["publication_state"] == "published"
        for item in datasets["items"]
    )
    assert governance["rows"]

    # The legacy overlay is retained but never read or written on a request
    # path after the explicit migration.
    assert legacy_overlay.read_bytes() == overlay_bytes


def test_discard_retains_legacy_file_and_reads_no_overlay(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
    observatory_loaders,
    legacy_overlay: Path,
    _declared_customer_health,
) -> None:
    from phlo.dataset_state import memory_store

    monkeypatch.setenv("PHLO_PROJECT_PATH", str(tmp_path))
    observatory_loaders(
        assets=[_asset("gold.customer_health")],
        tables_without_catalog=[],
        quality=[],
    )
    result = _migrate(tmp_path, legacy_overlay, "discard", "--store-mode", "memory")
    assert result["status"] == "discarded"
    assert result["legacy_source_retained"] is True

    # The discard is journaled in the durable store's audit stream.
    audits = [event.action for event in memory_store().audit_events()]
    assert "discard-overlay" in audits

    overlay_bytes = legacy_overlay.read_bytes()
    client = authenticated_client("admin")
    profile = client.get("/api/observatory/datasets/gold.customer_health").json()

    # No overlay state leaked into the durable store: the projection shows
    # the governed-surface/draft view, not the discarded legacy record.
    assert profile["canonical"]["publication_state"] is None
    assert profile["canonical"]["owner"] == "data-platform"
    assert legacy_overlay.read_bytes() == overlay_bytes


def _asset(asset_id: str):
    from phlo_api.observatory_api.observatory_models import ObservatoryAsset

    return ObservatoryAsset(id=asset_id, name=asset_id, group=asset_id.split(".")[0], metadata={})
