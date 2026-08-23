"""Tests for RBAC sync controller.

Drives SyncController with fake loaders and compilers to pin plan, sync,
verify, and revert behavior: dry-run applies nothing, apply errors are
reported rather than raised, and unknown backends are skipped.
"""

from __future__ import annotations

import pytest

from phlo.rbac.compiler import CompilerContext, GovernanceCompiler
from phlo.rbac.models import (
    BackendArtifact,
    CanonicalRBAC,
    PoliciesConfig,
    PolicyChange,
    RolesConfig,
    SyncPlan,
    VerifyResult,
)
from phlo.rbac.sync import SyncController


def _make_rbac() -> CanonicalRBAC:
    roles = RolesConfig.from_dict({"version": 1, "roles": {"admin": {"inherits": []}}})
    policies = PoliciesConfig.from_dict(
        {
            "version": 1,
            "policies": [
                {
                    "policy_id": "p1",
                    "effect": "allow",
                    "principal": {"roles": ["admin"]},
                    "action": "dataset.read",
                    "resource": {"type": "dataset", "id_pattern": "*"},
                },
            ],
        }
    )
    return CanonicalRBAC.from_configs(roles, policies)


_ARTIFACT = BackendArtifact(
    backend="fake",
    artifact_type="grant",
    name="phlo_admin_grant",
    statement="GRANT SELECT ON *",
)
_CHANGE = PolicyChange(
    change_type="create",
    backend="fake",
    artifact=_ARTIFACT,
    revert_id="fake_abc123",
)


class _FakeLoader:
    def __init__(self, rbac: CanonicalRBAC) -> None:
        self._rbac = rbac

    def load(self) -> CanonicalRBAC:
        return self._rbac

    def validate(self) -> tuple[bool, list[str]]:
        return True, []


class _FakeCompiler(GovernanceCompiler):
    """Minimal compiler for injection via constructor."""

    def __init__(
        self,
        *,
        plan_result: SyncPlan | None = None,
        apply_result: tuple[list[str], list[str]] | None = None,
        apply_error: Exception | None = None,
        verify_result: VerifyResult | None = None,
        revert_result: tuple[list[str], list[str]] | None = None,
        revert_error: Exception | None = None,
    ) -> None:
        super().__init__(backend=None)
        self._plan_result = plan_result
        self._apply_result = apply_result or ([], [])
        self._apply_error = apply_error
        self._verify_result = verify_result
        self._revert_result = revert_result or ([], [])
        self._revert_error = revert_error
        self.plan_called = False
        self.apply_called = False
        self.revert_called = False

    @property
    def backend_name(self) -> str:
        return "fake"

    def supports_action(self, action: str) -> bool:
        return True

    def compile(self, rbac: CanonicalRBAC, context: CompilerContext) -> list[BackendArtifact]:
        return []

    def read_current_state(self, context: CompilerContext) -> list[BackendArtifact]:
        return []

    def plan(self, rbac: CanonicalRBAC, context: CompilerContext) -> SyncPlan:
        self.plan_called = True
        if self._plan_result is not None:
            return self._plan_result
        return SyncPlan(version_hash="abc", backend="fake", changes=())

    def apply(self, plan: SyncPlan, context: CompilerContext) -> tuple[list[str], list[str]]:
        self.apply_called = True
        if self._apply_error:
            raise self._apply_error
        return self._apply_result

    def verify(self, rbac: CanonicalRBAC, context: CompilerContext) -> VerifyResult:
        if self._verify_result is not None:
            return self._verify_result
        return VerifyResult(backend="fake", in_sync=True)

    def revert(
        self, revert_ids: list[str], context: CompilerContext
    ) -> tuple[list[str], list[str]]:
        self.revert_called = True
        if self._revert_error:
            raise self._revert_error
        return self._revert_result


# -------------------------------------------------------------------
# Tests
# -------------------------------------------------------------------


def test_plan_returns_plans_from_compilers() -> None:
    rbac = _make_rbac()
    plan = SyncPlan(version_hash="h1", backend="fake", changes=(_CHANGE,))
    compiler = _FakeCompiler(plan_result=plan)
    ctrl = SyncController(loader=_FakeLoader(rbac), compilers={"fake": compiler})

    result = ctrl.plan(backends=["fake"], environment="dev")

    assert "fake" in result
    assert result["fake"] is plan
    assert compiler.plan_called


def test_plan_skips_unknown_backend() -> None:
    rbac = _make_rbac()
    ctrl = SyncController(loader=_FakeLoader(rbac), compilers={})

    result = ctrl.plan(backends=["nonexistent"], environment="dev")

    assert result == {}


def test_sync_applies_changes() -> None:
    rbac = _make_rbac()
    plan = SyncPlan(version_hash="h1", backend="fake", changes=(_CHANGE,))
    compiler = _FakeCompiler(
        plan_result=plan,
        apply_result=(["fake_abc123"], []),
    )
    ctrl = SyncController(loader=_FakeLoader(rbac), compilers={"fake": compiler})

    result = ctrl.sync(backends=["fake"], environment="dev")

    assert result["fake"].success is True
    assert result["fake"].applied_count == 1
    assert result["fake"].failed_count == 0
    assert "fake_abc123" in result["fake"].revert_ids
    assert compiler.apply_called


def test_sync_dry_run_does_not_apply() -> None:
    rbac = _make_rbac()
    plan = SyncPlan(version_hash="h1", backend="fake", changes=(_CHANGE,))
    compiler = _FakeCompiler(plan_result=plan)
    ctrl = SyncController(loader=_FakeLoader(rbac), compilers={"fake": compiler})

    result = ctrl.sync(backends=["fake"], environment="dev", dry_run=True)

    assert result["fake"].success is True
    assert result["fake"].applied_count == 0
    assert not compiler.apply_called


def test_sync_handles_apply_error() -> None:
    rbac = _make_rbac()
    plan = SyncPlan(version_hash="h1", backend="fake", changes=(_CHANGE,))
    compiler = _FakeCompiler(
        plan_result=plan,
        apply_error=RuntimeError("connection lost"),
    )
    ctrl = SyncController(loader=_FakeLoader(rbac), compilers={"fake": compiler})

    result = ctrl.sync(backends=["fake"], environment="dev")

    assert result["fake"].success is False
    assert "connection lost" in result["fake"].errors[0]


def test_verify_returns_results() -> None:
    rbac = _make_rbac()
    vr = VerifyResult(backend="fake", in_sync=False, missing=(_ARTIFACT,))
    compiler = _FakeCompiler(verify_result=vr)
    ctrl = SyncController(loader=_FakeLoader(rbac), compilers={"fake": compiler})

    result = ctrl.verify(backends=["fake"], environment="dev")

    assert result["fake"] is vr
    assert result["fake"].in_sync is False
    assert len(result["fake"].missing) == 1


def test_revert_calls_compiler_revert() -> None:
    rbac = _make_rbac()
    compiler = _FakeCompiler(revert_result=(["id1"], []))
    ctrl = SyncController(loader=_FakeLoader(rbac), compilers={"fake": compiler})

    result = ctrl.revert(revert_ids=["id1"], backends=["fake"], environment="dev")

    assert compiler.revert_called
    assert result["fake"] == (["id1"], [])


def test_revert_handles_exception() -> None:
    rbac = _make_rbac()
    compiler = _FakeCompiler(revert_error=RuntimeError("revert failed"))
    ctrl = SyncController(loader=_FakeLoader(rbac), compilers={"fake": compiler})

    result = ctrl.revert(revert_ids=["id1"], backends=["fake"], environment="dev")

    assert result["fake"] == ([], ["revert failed"])


def test_list_available_backends(monkeypatch: pytest.MonkeyPatch) -> None:
    from phlo.rbac import sync as sync_mod

    monkeypatch.setattr(sync_mod, "COMPILER_REGISTRY", {"alpha": object, "beta": object})

    ctrl = SyncController()
    backends = ctrl.list_available_backends()

    assert sorted(backends) == ["alpha", "beta"]
