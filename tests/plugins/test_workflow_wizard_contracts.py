"""Tests for workflow wizard contracts: browser-safe contribution
serialization, file conflict detection, and proposal validation."""

from pathlib import Path

from phlo.capabilities.workflow_wizard import (
    WorkflowApplyAction,
    WorkflowContributionMode,
    WorkflowFilePreview,
    WorkflowProposal,
    WorkflowProposalRequest,
    WorkflowWizardContribution,
    WorkflowWizardField,
    detect_file_conflicts,
    validate_proposal_request,
)


def test_workflow_contribution_serializes_browser_safe_shape() -> None:
    contribution = WorkflowWizardContribution(
        id="dlt.rest-api-source",
        package="phlo-dlt",
        stage="source",
        label="REST API source",
        description="Ingest records from a REST API with dlt.",
        required_capabilities=["table_store"],
        fields=[
            WorkflowWizardField(
                name="api_base_url",
                label="Base URL",
                field_type="text",
                required=True,
                secret=True,
            )
        ],
        modes={WorkflowContributionMode.PROPOSAL, WorkflowContributionMode.APPLY},
        metadata={
            "safe": "kept",
            "token": "redacted",
            "url": "https://internal.example",
        },
    )

    payload = contribution.to_browser_dict()

    assert payload["id"] == "dlt.rest-api-source"
    assert payload["stage"] == "source"
    assert payload["fields"][0]["secret"] is True
    assert payload["metadata"] == {"safe": "kept"}


def test_validate_proposal_request_requires_source_stage() -> None:
    request = WorkflowProposalRequest(
        workflow_name="customer_health",
        domain="customers",
        selections={"transform": {"contribution_id": "dbt.basic-model", "values": {}}},
    )

    errors = validate_proposal_request(request)

    assert errors == {"source": ["Select a source contribution."]}


def test_detect_file_conflicts_reports_existing_file(tmp_path: Path) -> None:
    target = tmp_path / "workflows" / "ingestion" / "customers" / "orders.py"
    target.parent.mkdir(parents=True)
    target.write_text("# existing\n", encoding="utf-8")
    proposal = WorkflowProposal(
        workflow_name="customer_health",
        domain="customers",
        files=[
            WorkflowFilePreview(
                path="workflows/ingestion/customers/orders.py",
                content="# generated\n",
                mode="create",
            )
        ],
    )

    conflicts = detect_file_conflicts(tmp_path, proposal)

    assert conflicts == ["workflows/ingestion/customers/orders.py"]


def test_apply_action_serializes_file_guardrails() -> None:
    action = WorkflowApplyAction(
        id="workflow.apply.customer-health",
        label="Create workflow files",
        target_files=["workflows/ingestion/customers/orders.py"],
        conflict_policy="fail-on-conflict",
        enabled=False,
        reason="Resolve file conflicts before applying.",
        expected_evidence=["Created workflows/ingestion/customers/orders.py"],
    )

    payload = action.to_browser_dict()

    assert payload["conflict_policy"] == "fail-on-conflict"
    assert payload["enabled"] is False
    assert payload["target_files"] == ["workflows/ingestion/customers/orders.py"]
