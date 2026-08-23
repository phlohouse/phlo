"""Tests for RBAC authz CLI commands.

Drives validate, plan, and sync against faked loaders and controllers to cover
success, failure, deny-rule rejection, and dry-run behavior.
"""

from __future__ import annotations

import pytest
import yaml
from click.testing import CliRunner

from phlo.cli.commands import authz as authz_mod
from phlo.cli.commands.authz import authz_group
from phlo.rbac.models import BackendArtifact, PolicyChange, SyncPlan, SyncResult, VerifyResult

pytestmark = pytest.mark.core_regression

_ARTIFACT = BackendArtifact(
    backend="trino",
    artifact_type="grant",
    name="grant_reader_select",
    statement="GRANT SELECT ON warehouse.* TO reader",
)

_CHANGE = PolicyChange(
    change_type="create",
    backend="trino",
    artifact=_ARTIFACT,
    revert_id="rev-001",
)


@pytest.fixture(autouse=True)
def _noop_discover(monkeypatch):
    monkeypatch.setattr(authz_mod, "discover_capabilities", lambda: None)


@pytest.fixture()
def runner():
    return CliRunner()


# -- helpers ------------------------------------------------------------------


class _FakeLoader:
    def __init__(self, *, valid=True, errors=None, **_kw):
        self._valid = valid
        self._errors = errors or []

    def validate(self):
        return self._valid, self._errors


class _FakeController:
    def __init__(self, **_kw):
        self.plan_return = {}
        self.sync_return = {}
        self.verify_return = {}
        self.revert_return = {}
        self._sync_kwargs = {}

    def plan(self, *, backends=None, environment="development"):
        if isinstance(self.plan_return, Exception):
            raise self.plan_return
        return self.plan_return

    def sync(self, *, backends=None, environment="development", dry_run=False):
        self._sync_kwargs = {
            "backends": backends,
            "environment": environment,
            "dry_run": dry_run,
        }
        if isinstance(self.sync_return, Exception):
            raise self.sync_return
        return self.sync_return

    def verify(self, *, backends=None, environment="development"):
        if isinstance(self.verify_return, Exception):
            raise self.verify_return
        return self.verify_return

    def revert(self, *, revert_ids, backends=None, environment="development"):
        if isinstance(self.revert_return, Exception):
            raise self.revert_return
        return self.revert_return


def _patch(monkeypatch, *, loader=None, controller=None):
    _loader = loader or _FakeLoader()
    _ctrl = controller or _FakeController()
    monkeypatch.setattr(authz_mod, "RBACConfigLoader", lambda **_kw: _loader)
    monkeypatch.setattr(authz_mod, "SyncController", lambda **_kw: _ctrl)
    return _loader, _ctrl


# -- validate -----------------------------------------------------------------


def test_validate_passes(runner, monkeypatch, tmp_path):
    _patch(monkeypatch, loader=_FakeLoader(valid=True))
    result = runner.invoke(authz_group, ["validate", "--path", str(tmp_path)])
    assert result.exit_code == 0
    assert "Validation passed" in result.output


def test_validate_fails(runner, monkeypatch, tmp_path):
    _patch(monkeypatch, loader=_FakeLoader(valid=False, errors=["error1"]))
    result = runner.invoke(authz_group, ["validate", "--path", str(tmp_path)])
    assert result.exit_code == 1
    assert "Validation failed" in result.output
    assert "error1" in result.output


def test_validate_rejects_deny_rules(runner, tmp_path):
    auth_dir = tmp_path / "authorization"
    auth_dir.mkdir()
    (auth_dir / "roles.yaml").write_text(
        yaml.safe_dump(
            {
                "version": 1,
                "roles": {"admin": {"inherits": []}},
            }
        )
    )
    (auth_dir / "policies.yaml").write_text(
        yaml.safe_dump(
            {
                "version": 1,
                "policies": [
                    {
                        "policy_id": "deny_admin_read",
                        "effect": "deny",
                        "principal": {"roles": ["admin"]},
                        "action": "dataset.read",
                        "resource": {"type": "dataset", "id_pattern": "analytics.*"},
                    }
                ],
            }
        )
    )

    result = runner.invoke(authz_group, ["validate", "--path", str(tmp_path)])

    assert result.exit_code == 1
    assert "Validation failed" in result.output
    assert "does not support 'deny' policies yet" in result.output


# -- plan ---------------------------------------------------------------------


def test_plan_no_plans(runner, monkeypatch, tmp_path):
    ctrl = _FakeController()
    ctrl.plan_return = {}
    _patch(monkeypatch, controller=ctrl)
    result = runner.invoke(authz_group, ["plan", "--path", str(tmp_path)])
    assert result.exit_code == 0
    assert "No plans generated" in result.output


def test_plan_with_changes(runner, monkeypatch, tmp_path):
    ctrl = _FakeController()
    ctrl.plan_return = {
        "trino": SyncPlan(
            version_hash="abc123",
            backend="trino",
            changes=(_CHANGE,),
        ),
    }
    _patch(monkeypatch, controller=ctrl)
    result = runner.invoke(authz_group, ["plan", "--path", str(tmp_path)])
    assert result.exit_code == 0
    assert "abc123" in result.output
    assert "Changes: 1" in result.output
    assert "grant_reader_select" in result.output


def test_plan_exception(runner, monkeypatch, tmp_path):
    ctrl = _FakeController()
    ctrl.plan_return = RuntimeError("boom")
    _patch(monkeypatch, controller=ctrl)
    result = runner.invoke(authz_group, ["plan", "--path", str(tmp_path)])
    assert result.exit_code == 1
    assert "Planning failed" in result.output


# -- sync ---------------------------------------------------------------------


def test_sync_success(runner, monkeypatch, tmp_path):
    ctrl = _FakeController()
    ctrl.sync_return = {
        "trino": SyncResult(
            success=True,
            backend="trino",
            version_hash="abc123",
            applied_count=2,
            failed_count=0,
            errors=(),
        ),
    }
    _patch(monkeypatch, controller=ctrl)
    result = runner.invoke(authz_group, ["sync", "--path", str(tmp_path)])
    assert result.exit_code == 0
    assert "Success: True" in result.output


def test_sync_failure(runner, monkeypatch, tmp_path):
    ctrl = _FakeController()
    ctrl.sync_return = {
        "trino": SyncResult(
            success=False,
            backend="trino",
            version_hash="abc123",
            applied_count=0,
            failed_count=1,
            errors=("grant failed",),
        ),
    }
    _patch(monkeypatch, controller=ctrl)
    result = runner.invoke(authz_group, ["sync", "--path", str(tmp_path)])
    assert result.exit_code == 1
    assert "Success: False" in result.output
    assert "grant failed" in result.output


def test_sync_dry_run(runner, monkeypatch, tmp_path):
    ctrl = _FakeController()
    ctrl.sync_return = {
        "trino": SyncResult(
            success=True,
            backend="trino",
            version_hash="abc123",
            applied_count=0,
            failed_count=0,
            errors=(),
        ),
    }
    _patch(monkeypatch, controller=ctrl)
    result = runner.invoke(authz_group, ["sync", "--path", str(tmp_path), "--dry-run"])
    assert result.exit_code == 0
    assert ctrl._sync_kwargs["dry_run"] is True


# -- verify -------------------------------------------------------------------


def test_verify_in_sync(runner, monkeypatch, tmp_path):
    ctrl = _FakeController()
    ctrl.verify_return = {
        "trino": VerifyResult(backend="trino", in_sync=True),
    }
    _patch(monkeypatch, controller=ctrl)
    result = runner.invoke(authz_group, ["verify", "--path", str(tmp_path)])
    assert result.exit_code == 0
    assert "In sync: True" in result.output


def test_verify_drift(runner, monkeypatch, tmp_path):
    ctrl = _FakeController()
    ctrl.verify_return = {
        "trino": VerifyResult(
            backend="trino",
            in_sync=False,
            missing=(_ARTIFACT,),
        ),
    }
    _patch(monkeypatch, controller=ctrl)
    result = runner.invoke(authz_group, ["verify", "--path", str(tmp_path)])
    assert result.exit_code == 1
    assert "Missing artifacts: 1" in result.output
    assert "grant_reader_select" in result.output


# -- revert -------------------------------------------------------------------


def test_revert_success(runner, monkeypatch, tmp_path):
    ctrl = _FakeController()
    ctrl.revert_return = {
        "trino": (["rev-001"], []),
    }
    _patch(monkeypatch, controller=ctrl)
    result = runner.invoke(
        authz_group,
        ["revert", "rev-001", "--path", str(tmp_path)],
    )
    assert result.exit_code == 0
    assert "Reverted: 1" in result.output


def test_revert_with_errors(runner, monkeypatch, tmp_path):
    ctrl = _FakeController()
    ctrl.revert_return = {
        "trino": ([], ["revert failed"]),
    }
    _patch(monkeypatch, controller=ctrl)
    result = runner.invoke(
        authz_group,
        ["revert", "rev-001", "--path", str(tmp_path)],
    )
    assert result.exit_code == 1
    assert "Errors: 1" in result.output
    assert "revert failed" in result.output
