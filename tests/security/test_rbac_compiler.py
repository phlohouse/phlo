"""Tests for RBAC compiler behavior.

Covers the Trino policy compiler: privilege-to-action mapping, revert ids
that round-trip to artifact names (invalid ones rejected), control-plane
and deny-rule exclusion, and rejection of unsafe role names or wildcard
patterns outside the final resource segment.
"""

from __future__ import annotations

from typing import cast

import pytest

from phlo.capabilities.interfaces import GovernanceBackend
from phlo.rbac.compiler import CompilerContext, TrinoCompiler
from phlo.rbac.models import (
    BackendArtifact,
    CanonicalRBAC,
    PoliciesConfig,
    PolicyChange,
    RolesConfig,
    SyncPlan,
)


class _FakeTrinoBackend:
    def __init__(self) -> None:
        self.applied_policies = []
        self.revoked_policy_ids = []
        self._policies = []

    def list_policies(self, *, table_name: str | None = None) -> list[dict[str, str]]:
        return list(self._policies)

    def apply_policy(self, *, policy) -> None:
        self.applied_policies.append(policy)

    def revoke_policy(self, *, policy_id: str) -> None:
        self.revoked_policy_ids.append(policy_id)


def test_trino_apply_uses_privilege_as_action() -> None:
    backend = _FakeTrinoBackend()
    compiler = TrinoCompiler(backend=cast(GovernanceBackend, backend))
    artifact = BackendArtifact(
        backend="trino",
        artifact_type="grant",
        name="phlo_admin_dataset_analytics.table",
        statement="GRANT SELECT ON TABLE analytics.table TO ROLE phlo_admin",
        metadata={
            "role": "phlo_admin",
            "privilege": "SELECT",
            "resource": "analytics.table",
        },
    )

    compiler._apply_artifact(artifact)

    assert len(backend.applied_policies) == 1
    assert backend.applied_policies[0].action == "SELECT"
    assert backend.applied_policies[0].effect == "ALLOW"
    assert backend.applied_policies[0].table_pattern == "analytics.table"


def test_trino_revert_decodes_revert_id_to_artifact_name() -> None:
    backend = _FakeTrinoBackend()
    backend._policies = [
        {
            "grantee": "phlo_admin",
            "schema": "analytics",
            "table": "table",
            "privilege": "SELECT",
        }
    ]
    compiler = TrinoCompiler(backend=cast(GovernanceBackend, backend))
    artifact_name = "phlo_admin_dataset_analytics.table"
    revert_id = compiler._encode_revert_id(artifact_name)
    context = CompilerContext(environment="test", backend_name="trino")

    success_ids, errors = compiler.revert([revert_id], context)

    assert success_ids == [revert_id]
    assert errors == []
    assert backend.revoked_policy_ids == ["SELECT:analytics.table:phlo_admin"]


def test_trino_plan_emits_revert_ids_that_round_trip() -> None:
    compiler = TrinoCompiler()
    artifact = BackendArtifact(
        backend="trino",
        artifact_type="grant",
        name="phlo_admin_dataset_analytics.table",
        statement="GRANT SELECT ON TABLE analytics.table TO ROLE phlo_admin",
        metadata={},
    )
    plan = SyncPlan(
        version_hash="abc123",
        backend="trino",
        changes=(
            PolicyChange(
                change_type="create",
                backend="trino",
                artifact=artifact,
                revert_id=compiler._encode_revert_id(artifact.name),
            ),
        ),
    )

    revert_id = plan.changes[0].revert_id

    assert revert_id is not None
    assert compiler._decode_revert_id(revert_id) == artifact.name


@pytest.mark.parametrize("revert_id", ["postgresql:abc123", "trino:", "trino:!!!!"])
def test_trino_decode_rejects_invalid_revert_ids(revert_id: str) -> None:
    compiler = TrinoCompiler()

    with pytest.raises(ValueError, match="Invalid Trino revert ID"):
        compiler._decode_revert_id(revert_id)


def test_trino_revert_reports_missing_artifact() -> None:
    backend = _FakeTrinoBackend()
    compiler = TrinoCompiler(backend=cast(GovernanceBackend, backend))
    revert_id = compiler._encode_revert_id("phlo_admin_dataset_missing.table")
    context = CompilerContext(environment="test", backend_name="trino")

    success_ids, errors = compiler.revert([revert_id], context)

    assert success_ids == []
    assert errors == [
        f"Failed to revert {revert_id}: artifact 'phlo_admin_dataset_missing.table' not found"
    ]
    assert backend.revoked_policy_ids == []


def test_trino_read_current_state_uses_compile_compatible_names() -> None:
    backend = _FakeTrinoBackend()
    backend._policies = [
        {
            "grantee": "phlo_admin",
            "schema": "analytics",
            "table": "table",
            "privilege": "SELECT",
        }
    ]
    compiler = TrinoCompiler(backend=cast(GovernanceBackend, backend))
    context = CompilerContext(environment="test", backend_name="trino")

    artifacts = compiler.read_current_state(context)

    assert len(artifacts) == 1
    assert artifacts[0].name == "phlo_admin_dataset_analytics.table"
    assert artifacts[0].metadata["resource_type"] == "dataset"


def test_trino_does_not_compile_control_plane_policies() -> None:
    compiler = TrinoCompiler()
    roles = RolesConfig.from_dict(
        {
            "version": 1,
            "roles": {
                "viewer": {"inherits": []},
                "analyst": {"inherits": ["viewer"]},
                "admin": {"inherits": ["analyst"]},
            },
        }
    )
    policies = PoliciesConfig.from_dict(
        {
            "version": 1,
            "policies": [
                {
                    "policy_id": "allow_admin_manage",
                    "effect": "allow",
                    "principal": {"roles": ["admin"]},
                    "action": "admin.manage",
                    "resource": {"type": "admin", "id_pattern": "dagster"},
                }
            ],
        }
    )
    rbac = CanonicalRBAC.from_configs(roles, policies)
    context = CompilerContext(environment="test", backend_name="trino")

    artifacts = compiler.compile(rbac, context)

    assert artifacts == []


def test_trino_compile_allows_catch_all_resource_pattern() -> None:
    compiler = TrinoCompiler()
    roles = RolesConfig.from_dict({"version": 1, "roles": {"admin": {"inherits": []}}})
    policies = PoliciesConfig.from_dict(
        {
            "version": 1,
            "policies": [
                {
                    "policy_id": "allow_all",
                    "effect": "allow",
                    "principal": {"roles": ["admin"]},
                    "action": "dataset.read",
                    "resource": {"type": "dataset", "id_pattern": "*"},
                }
            ],
        }
    )
    rbac = CanonicalRBAC.from_configs(roles, policies)
    context = CompilerContext(environment="test", backend_name="trino")

    artifacts = compiler.compile(rbac, context)

    assert len(artifacts) == 1
    assert artifacts[0].statement == "GRANT SELECT ON TABLE % TO ROLE admin"
    assert artifacts[0].metadata["resource"] == "%"


def test_trino_compile_rejects_deny_rules() -> None:
    compiler = TrinoCompiler()
    roles = RolesConfig.from_dict({"version": 1, "roles": {"admin": {"inherits": []}}})
    policies = PoliciesConfig.from_dict(
        {
            "version": 1,
            "policies": [
                {
                    "policy_id": "deny_read",
                    "effect": "deny",
                    "principal": {"roles": ["admin"]},
                    "action": "dataset.read",
                    "resource": {"type": "dataset", "id_pattern": "analytics.table"},
                },
                {
                    "policy_id": "allow_object",
                    "effect": "allow",
                    "principal": {"roles": ["admin"]},
                    "action": "object.read",
                    "resource": {"type": "object", "id_pattern": "bucket/*"},
                },
            ],
        }
    )
    rbac = CanonicalRBAC.from_configs(roles, policies)
    context = CompilerContext(environment="test", backend_name="trino")

    with pytest.raises(ValueError, match="does not support canonical 'deny' policies"):
        compiler.compile(rbac, context)


def test_trino_compile_rejects_unsafe_role_names() -> None:
    compiler = TrinoCompiler()
    roles = RolesConfig.from_dict({"version": 1, "roles": {"bad-role": {"inherits": []}}})
    policies = PoliciesConfig.from_dict(
        {
            "version": 1,
            "policies": [
                {
                    "policy_id": "allow_read",
                    "effect": "allow",
                    "principal": {"roles": ["bad-role"]},
                    "action": "dataset.read",
                    "resource": {"type": "dataset", "id_pattern": "analytics.table"},
                }
            ],
        }
    )
    rbac = CanonicalRBAC.from_configs(roles, policies)
    context = CompilerContext(environment="test", backend_name="trino")

    with pytest.raises(ValueError, match="Unsafe role_name"):
        compiler.compile(rbac, context)


def test_trino_compile_rejects_wildcards_before_final_resource_segment() -> None:
    compiler = TrinoCompiler()
    roles = RolesConfig.from_dict({"version": 1, "roles": {"admin": {"inherits": []}}})
    policies = PoliciesConfig.from_dict(
        {
            "version": 1,
            "policies": [
                {
                    "policy_id": "allow_read",
                    "effect": "allow",
                    "principal": {"roles": ["admin"]},
                    "action": "dataset.read",
                    "resource": {"type": "dataset", "id_pattern": "analytics.*.events"},
                }
            ],
        }
    )
    rbac = CanonicalRBAC.from_configs(roles, policies)
    context = CompilerContext(environment="test", backend_name="trino")

    with pytest.raises(ValueError, match="Wildcards only allowed in final segment"):
        compiler.compile(rbac, context)


def test_trino_does_not_compile_service_control_plane_grants() -> None:
    compiler = TrinoCompiler()
    roles = RolesConfig.from_dict({"version": 1, "roles": {"operator": {"inherits": []}}})
    policies = PoliciesConfig.from_dict(
        {
            "version": 1,
            "policies": [
                {
                    "policy_id": "allow_service_manage",
                    "effect": "allow",
                    "principal": {"roles": ["operator"]},
                    "action": "service.manage",
                    "resource": {"type": "service", "id_pattern": "dagster"},
                }
            ],
        }
    )
    rbac = CanonicalRBAC.from_configs(roles, policies)
    context = CompilerContext(environment="test", backend_name="trino")

    artifacts = compiler.compile(rbac, context)

    assert artifacts == []


def test_trino_skips_control_plane_deny_for_surface_pdp() -> None:
    compiler = TrinoCompiler()
    roles = RolesConfig.from_dict({"version": 1, "roles": {"operator": {"inherits": []}}})
    policies = PoliciesConfig.from_dict(
        {
            "version": 1,
            "policies": [
                {
                    "policy_id": "deny_service_manage",
                    "effect": "deny",
                    "principal": {"roles": ["operator"]},
                    "action": "service.manage",
                    "resource": {"type": "service", "id_pattern": "dagster"},
                }
            ],
        }
    )

    assert (
        compiler.compile(
            CanonicalRBAC.from_configs(roles, policies),
            CompilerContext(environment="test", backend_name="trino"),
        )
        == []
    )


def test_trino_rejects_invalid_action_resource_pair() -> None:
    compiler = TrinoCompiler()
    roles = RolesConfig.from_dict({"version": 1, "roles": {"operator": {"inherits": []}}})
    policies = PoliciesConfig.from_dict(
        {
            "version": 1,
            "policies": [
                {
                    "policy_id": "invalid_asset_read",
                    "effect": "allow",
                    "principal": {"roles": ["operator"]},
                    "action": "dataset.read",
                    "resource": {"type": "asset", "id_pattern": "orders"},
                }
            ],
        }
    )
    rbac = CanonicalRBAC.from_configs(roles, policies)

    with pytest.raises(ValueError, match="cannot compile policy"):
        compiler.compile(rbac, CompilerContext(environment="test", backend_name="trino"))
