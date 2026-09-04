"""Cross-surface Dataset parity tests.

CLI, Governance, and phlo-api must agree on one canonical Dataset
projection: identical identity, owner, classifications, controls and
evidence, readiness reasons in the evaluator's order, and publication
state. Each test drives the CLI (``phlo dataset show --json``, ``phlo
governance check --json``) and the Observatory API (profile ``canonical``,
``/actions`` dispatch) against the same explicit memory-mode store in one
process and asserts equality of the projections.
"""

from __future__ import annotations

import json

import pytest
from click.testing import CliRunner

import phlo
from phlo.cli.commands.dataset import dataset_group
from phlo.cli.commands.governance import governance_group
from phlo.dataset.models import DatasetRecord
from phlo.dataset.store import StoreWrite
from security_test_support import authenticated_client  # noqa: F401


@pytest.fixture()
def _declared_orders():
    """Register one fully declared gold.orders contract, cleaned up after."""

    @phlo.contract(
        table="gold.orders",
        owner="analytics",
        metadata={"classification": "internal"},
    )
    def _orders_contract() -> None:
        return None

    yield
    phlo.clear_flow_declarations()


def _cli_projection(tmp_path, dataset_id: str = "gold.orders") -> dict:
    from phlo_api.observatory_api import observatory

    observatory._clear_read_model_cache()
    result = CliRunner().invoke(
        dataset_group,
        ["show", dataset_id, "--json", "--store-mode", "memory"],
        env={"PHLO_PROJECT_PATH": str(tmp_path)},
    )
    assert result.exit_code == 0, result.output
    return json.loads(result.output)


def test_cli_show_json_matches_api_profile_canonical(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
    observatory_loaders,
    _declared_orders,
) -> None:
    monkeypatch.setenv("PHLO_PROJECT_PATH", str(tmp_path))
    observatory_loaders(
        assets=[_asset("gold.orders")],
        tables_without_catalog=[],
        quality=[],
    )

    response = authenticated_client("admin").get("/api/observatory/datasets/gold.orders")
    assert response.status_code == 200
    profile = response.json()
    canonical = profile["canonical"]

    cli_projection = _cli_projection(tmp_path)

    # Byte-for-byte projection parity: the CLI emits the canonical projection
    # verbatim and the API profile embeds the same dict.
    assert cli_projection == canonical
    assert canonical["dataset_id"] == "gold.orders"
    assert canonical["owner"] == "analytics"
    assert canonical["classifications"] == ["internal"]
    assert profile["dataset"]["id"] == canonical["dataset_id"]
    assert profile["dataset"]["owner"] == canonical["owner"]
    assert profile["dataset"]["classifications"] == canonical["classifications"]


def test_governance_check_datasets_section_matches_cli_projection(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
    _declared_orders,
) -> None:
    monkeypatch.setenv("PHLO_PROJECT_PATH", str(tmp_path))
    cli_projection = _cli_projection(tmp_path)

    result = CliRunner().invoke(
        governance_group,
        ["check", "--json"],
        env={"PHLO_PROJECT_PATH": str(tmp_path)},
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["datasets"]["available"] is True
    section = next(
        item for item in payload["datasets"]["datasets"] if item["dataset_id"] == "gold.orders"
    )
    assert section == cli_projection


def test_blocked_publish_returns_identical_ordered_reasons_on_every_surface(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
    observatory_loaders,
    _declared_orders,
) -> None:
    """A blocked publish shows the same ordered reasons on CLI and API."""
    monkeypatch.setenv("PHLO_PROJECT_PATH", str(tmp_path))
    observatory_loaders(
        assets=[_asset("gold.orders")],
        tables_without_catalog=[],
        quality=[],
    )

    # Seed the promoted draft record a real promotion would have committed;
    # publish is then blocked by policy, not by a missing record.
    from phlo_api.observatory_api import observatory as observatory_module

    authority = observatory_module._dataset_authority()
    seed = authority.service.store.compare_and_set(
        writes=(
            StoreWrite(
                record_id="gold.orders",
                expected_state="open",
                next_record=DatasetRecord(
                    dataset_id="gold.orders",
                    table_id="gold.orders",
                    publication_state="draft",
                    owner="analytics",
                ),
            ),
        ),
        action_id="parity-seed-promotion",
        action="promote",
        fingerprint="parity-seed-promotion",
    )
    assert seed.status.value == "committed"

    cli_projection = _cli_projection(tmp_path)
    reasons = cli_projection["readiness"]["reasons"]
    assert reasons, "expected the publish readiness to be blocked"

    api_profile = authenticated_client("admin").get("/api/observatory/datasets/gold.orders").json()
    assert api_profile["canonical"]["readiness"]["reasons"] == reasons

    # The /actions dispatch surfaces the same ordered reasons verbatim.
    action = authenticated_client("admin").post(
        "/api/observatory/actions",
        json={"action_id": "dataset:gold.orders:publish"},
    )
    assert action.status_code == 200
    assert action.json()["status"] == "skipped"
    assert "; ".join(reasons) in action.json()["message"]

    # The CLI transition prints the same ordered reasons and exits non-zero.
    cli_transition = CliRunner().invoke(
        dataset_group,
        [
            "transition",
            "gold.orders",
            "publish",
            "--action-id",
            "parity-blocked-probe",
            "--store-mode",
            "memory",
        ],
        env={"PHLO_PROJECT_PATH": str(tmp_path)},
    )
    assert cli_transition.exit_code == 1
    for reason in reasons:
        assert reason in cli_transition.output


def test_authorized_publish_is_idempotent_audited_and_durable(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
    observatory_loaders,
    _declared_orders,
) -> None:
    from phlo.dataset_state import memory_store
    from phlo_api.observatory_api import observatory as observatory_module

    monkeypatch.setenv("PHLO_PROJECT_PATH", str(tmp_path))
    observatory_loaders(
        assets=[_asset("gold.orders")],
        tables_without_catalog=[],
        quality=[_passing_quality_check()],
    )
    authority = observatory_module._dataset_authority()
    seed = authority.service.store.compare_and_set(
        writes=(
            StoreWrite(
                record_id="gold.orders",
                expected_state="open",
                next_record=DatasetRecord(
                    dataset_id="gold.orders",
                    table_id="gold.orders",
                    publication_state="draft",
                    owner="analytics",
                ),
            ),
        ),
        action_id="durable-seed-promotion",
        action="promote",
        fingerprint="durable-seed-promotion",
    )
    assert seed.status.value == "committed"
    client = authenticated_client("admin")

    first = client.post(
        "/api/observatory/actions",
        json={"action_id": "dataset:gold.orders:publish"},
    )
    replay = client.post(
        "/api/observatory/actions",
        json={"action_id": "dataset:gold.orders:publish"},
    )

    assert first.status_code == 200
    assert first.json()["status"] == "succeeded"
    assert replay.status_code == 200
    assert replay.json()["status"] == "succeeded"
    assert replay.json()["message"].endswith("(replayed)")

    # Audited: the core durable store holds one audit event per attempt for
    # the idempotency key -- the original commit and the replay.
    store = memory_store()
    audits = [
        event for event in store.audit_events() if event.action_id == "dataset:gold.orders:publish"
    ]
    assert [event.outcome for event in audits] == ["committed", "replayed"]
    assert audits[0].action == "publish"

    # Durable: a freshly built authority (what a restarted process resolves)
    # sees the published state through the same store.
    from phlo.dataset_projection import build_dataset_authority

    restarted = build_dataset_authority(str(tmp_path), store_mode="memory")
    projection = restarted.projection("gold.orders")
    assert projection["publication_state"] == "published"
    assert projection["last_action_id"] == "dataset:gold.orders:publish"
    assert projection["record"]["last_action_id"] == "dataset:gold.orders:publish"


def _asset(asset_id: str):
    from phlo_api.observatory_api.observatory_models import ObservatoryAsset

    return ObservatoryAsset(id=asset_id, name=asset_id, group=asset_id.split(".")[0], metadata={})


def _passing_quality_check():
    from phlo_api.observatory_api.observatory_models import ObservatoryQualityCheck

    return ObservatoryQualityCheck(
        id="gold.orders:not_null_order_id",
        name="not_null_order_id",
        asset_id="gold.orders",
        status="passing",
        blocking=True,
    )
