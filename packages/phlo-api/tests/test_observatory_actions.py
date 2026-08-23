"""Tests the Observatory action dispatcher.

Covers guard metadata on ObservatoryAction, rejection of unknown action ids,
skipped results for known-but-unimplemented families, and execution of a
quality check registered in a CapabilityRegistry.
"""

from phlo.capabilities.registry import CapabilityRegistry
from phlo.capabilities.specs import AlertSinkSpec, AssetCheckSpec, CheckResult

from phlo_api.observatory_api.observatory_actions import execute_observatory_action
from phlo_api.observatory_api.observatory_models import ObservatoryAction, ObservatoryActionRequest


def test_observatory_action_exposes_guard_metadata() -> None:
    action = ObservatoryAction(
        id="service:trino:restart",
        label="Restart",
        kind="service.restart",
        enabled=True,
        risk_level="medium",
        required_capability="service_control",
        required_service="trino",
        required_permission="service.manage",
        equivalent_cli_command="phlo services restart --service trino",
        expected_evidence=["service.status.running"],
    )
    payload = action.model_dump()
    assert payload["risk_level"] == "medium"
    assert payload["required_permission"] == "service.manage"
    assert payload["expected_evidence"] == ["service.status.running"]


def test_execute_observatory_action_rejects_unknown_action() -> None:
    result = execute_observatory_action(ObservatoryActionRequest(action_id="unknown:thing"))
    assert result.status == "failed"
    assert "Unsupported" in result.message


def test_execute_observatory_action_returns_skipped_for_unimplemented_known_family() -> None:
    result = execute_observatory_action(
        ObservatoryActionRequest(action_id="quality:orders:not_null:rerun")
    )
    assert result.status == "skipped"
    assert result.action.kind == "quality.rerun"
    assert result.action.required_capability == "quality_backend"


def test_execute_observatory_action_returns_skipped_for_dataset_workflow() -> None:
    result = execute_observatory_action(
        ObservatoryActionRequest(action_id="dataset:gold.orders:publish")
    )
    assert result.status == "skipped"
    assert result.action.kind == "dataset.workflow"
    assert result.action.required_capability == "metadata_catalog"


def test_execute_observatory_action_runs_registered_quality_check() -> None:
    registry = CapabilityRegistry()
    seen_context = {}

    def check_fn(context):
        seen_context["action_id"] = context.tags["phlo/action_id"]
        return CheckResult(
            check_name="not_null",
            asset_key="orders",
            passed=True,
            metadata={"rows_checked": 3},
        )

    registry.register("check", AssetCheckSpec(name="not_null", asset_key="orders", fn=check_fn))

    result = execute_observatory_action(
        ObservatoryActionRequest(action_id="quality:orders:not_null:rerun"),
        registry=registry,
    )

    assert result.status == "succeeded"
    assert result.action.enabled is True
    assert result.operation is not None
    assert result.operation.status == "succeeded"
    assert result.operation.target is not None
    assert result.operation.target.kind == "quality"
    assert result.operation.metadata["metadata"] == {"rows_checked": 3}
    assert seen_context == {"action_id": "quality:orders:not_null:rerun"}


def test_execute_observatory_action_runs_registered_alert_sink() -> None:
    class Sink:
        def __init__(self) -> None:
            self.messages = []

        def send_alert(self, **kwargs) -> bool:
            self.messages.append(kwargs)
            return True

    sink = Sink()
    registry = CapabilityRegistry()
    registry.register("alert_sink", AlertSinkSpec(name="alerts", provider=sink))

    result = execute_observatory_action(
        ObservatoryActionRequest(action_id="alert:incident"), registry=registry
    )

    assert result.status == "succeeded"
    assert result.action.enabled is True
    assert result.operation is not None
    assert result.operation.metadata["provider"] == "alerts"
    assert sink.messages[0]["severity"] == "warning"
