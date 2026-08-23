"""Tests the dbt workflow wizard contribution.

phlo_dbt.plugin must expose a single dbt.transform proposal-mode contribution
whose fields cover the transform knobs (renames, casts, where, partition_by,
metrics).
"""

from phlo.capabilities import WorkflowContributionMode
from phlo_dbt.plugin import get_workflow_wizard_contributions


def test_dbt_contributes_transform_wizard_options() -> None:
    contributions = get_workflow_wizard_contributions()

    ids = [contribution.id for contribution in contributions]

    assert ids == ["dbt.transform"]
    assert all(contribution.stage == "transform" for contribution in contributions)
    assert all(
        WorkflowContributionMode.PROPOSAL in contribution.modes for contribution in contributions
    )
    fields = [field.name for field in contributions[0].fields]
    assert "project_name" in fields
    assert "staging_model_name" in fields
    assert "renames" in fields
    assert "casts" in fields
    assert "where" in fields
    assert "partition_by" in fields
    assert "metrics" in fields
