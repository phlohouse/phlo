"""Smoke-test API imports on the native Windows portability runner."""

import os
from importlib import import_module

import pytest


def test_main_and_branch_router_import_without_posix_lock_module():
    for module in (
        "phlo_api.api.operation_controls",
        "phlo_api.api.v1_branch_workflows",
        "phlo_api.main",
    ):
        import_module(module)


@pytest.mark.skipif(os.name != "nt", reason="requires a native Windows runner")
def test_native_windows_workflow_apply_fails_at_the_capability_boundary(tmp_path):
    from phlo_api.errors import UnsupportedCapabilityError
    from phlo_api.observatory_api.observatory_workflow_wizard import (
        ObservatoryWorkflowActionRequest,
        apply_workflow_action,
    )

    with pytest.raises(UnsupportedCapabilityError, match="Linux or WSL 2") as caught:
        apply_workflow_action(
            tmp_path,
            ObservatoryWorkflowActionRequest(action_id="action", proposal_id="proposal"),
            "operator",
        )

    assert caught.value.status_code == 501
    assert list(tmp_path.iterdir()) == []
