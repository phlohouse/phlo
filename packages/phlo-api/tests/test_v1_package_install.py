"""Plan-then-apply contract of the /api/v1 administrator package install."""

from __future__ import annotations

from pathlib import Path

import pytest
from phlo.operations.journal_store import FileOperationJournalStore

from phlo_api.api.continuity import JOURNAL_DIR_ENV
from phlo_api.observatory_api import package_install
from security_test_support import authenticated_client

PLAN = "/api/v1/admin/packages/openmetadata/install-plan"
APPLY = "/api/v1/admin/packages/openmetadata/install"


@pytest.fixture
def installs(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> list[str]:
    monkeypatch.setenv("PHLO_PROJECT_PATH", str(tmp_path))
    monkeypatch.setenv(JOURNAL_DIR_ENV, str(tmp_path / "journal"))
    monkeypatch.setattr(
        package_install,
        "get_registry_data",
        lambda: {"plugins": {"openmetadata": {"type": "service", "package": "phlo-openmetadata"}}},
    )
    monkeypatch.setattr(
        package_install,
        "resolve_install_target",
        lambda name: ("phlo-openmetadata==1.2.0", name),
    )
    monkeypatch.setattr(package_install, "_load_services", lambda: [])
    installed: list[str] = []

    def fake_install(package_spec: str) -> tuple[bool, str]:
        installed.append(package_spec)
        return True, "installed"

    monkeypatch.setattr(package_install, "run_package_install", fake_install)
    return installed


def _apply_body(plan: dict, key: str = "install-1") -> dict:
    return {
        "plan": plan["plan"],
        "confirmation_token": plan["plan_token"],
        "idempotency_key": key,
    }


def test_plan_then_apply_installs_once_and_journals_the_planned_id(installs, tmp_path) -> None:
    client = authenticated_client("admin")
    plan = client.post(PLAN)
    assert plan.status_code == 200, plan.text
    body = plan.json()
    assert body["plan"]["package_name"] == "phlo-openmetadata"
    assert body["plan"]["package_spec"] == "phlo-openmetadata==1.2.0"
    assert installs == []

    applied = client.post(APPLY, json=_apply_body(body))
    replayed = client.post(APPLY, json=_apply_body(body))

    assert applied.status_code == 200, applied.text
    assert applied.json()["status"] == "succeeded"
    assert applied.json()["operation_id"] == body["operation_id"]
    assert replayed.status_code == 200, replayed.text
    assert replayed.json() == applied.json()
    assert installs == ["phlo-openmetadata==1.2.0"]
    entry = FileOperationJournalStore(tmp_path / "journal").read(body["operation_id"])
    assert entry is not None
    assert entry.state.value == "succeeded"
    assert entry.plan_token == body["plan_token"]

    # A reused key cannot be bound to a different plan.
    other = client.post(PLAN).json()
    conflict = client.post(APPLY, json=_apply_body(other))
    assert conflict.status_code == 409, conflict.text
    assert conflict.json()["error"]["code"] == "idempotency_key_conflict"
    assert installs == ["phlo-openmetadata==1.2.0"]


def test_apply_rejects_unconfirmed_or_stale_plans_before_installing(
    installs, monkeypatch, tmp_path
) -> None:
    client = authenticated_client("admin")
    plan = client.post(PLAN).json()

    wrong_token = client.post(APPLY, json={**_apply_body(plan), "confirmation_token": "guess"})
    tampered = client.post(
        APPLY,
        json={
            **_apply_body(plan, "tampered"),
            "plan": {**plan["plan"], "package_spec": "phlo-openmetadata==9.9.9"},
        },
    )
    other_package = client.post(
        "/api/v1/admin/packages/dbt/install", json=_apply_body(plan, "other-package")
    )
    # The registry pin moved after the administrator reviewed the plan.
    monkeypatch.setattr(
        package_install,
        "resolve_install_target",
        lambda name: ("phlo-openmetadata==1.3.0", name),
    )
    stale = client.post(APPLY, json=_apply_body(plan, "stale"))

    assert wrong_token.status_code == 400
    assert wrong_token.json()["error"]["code"] == "token_mismatch"
    assert tampered.status_code == 400
    assert tampered.json()["error"]["code"] == "token_mismatch"
    assert other_package.status_code == 400
    assert other_package.json()["error"]["code"] == "package_mismatch"
    assert stale.status_code == 409, stale.text
    assert stale.json()["error"]["code"] == "plan_stale"
    assert installs == []
    assert FileOperationJournalStore(tmp_path / "journal").read(plan["operation_id"]) is None

    fresh = client.post(PLAN).json()
    applied = client.post(APPLY, json=_apply_body(fresh, "fresh"))
    assert applied.status_code == 200, applied.text
    assert installs == ["phlo-openmetadata==1.3.0"]


def test_unknown_install_outcome_blocks_a_second_apply(installs, monkeypatch, tmp_path) -> None:
    client = authenticated_client("admin")
    plan = client.post(PLAN).json()

    def interrupted(_package_spec: str) -> tuple[bool, str]:
        raise TimeoutError("installer timed out")

    monkeypatch.setattr(package_install, "run_package_install", interrupted)
    unknown = client.post(APPLY, json=_apply_body(plan))
    same_key = client.post(APPLY, json=_apply_body(plan))
    new_key = client.post(APPLY, json=_apply_body(plan, "second-key"))

    assert unknown.status_code == 502
    assert unknown.json()["error"]["code"] == "apply_outcome_unknown"
    for retry in (same_key, new_key):
        assert retry.status_code == 409, retry.text
        assert retry.json()["error"]["code"] == "conflicting_claim"
    entry = FileOperationJournalStore(tmp_path / "journal").read(plan["operation_id"])
    assert entry is not None
    assert entry.state.value == "unknown"


def test_failed_or_unavailable_installer_is_a_definite_outcome(
    installs, monkeypatch, tmp_path
) -> None:
    client = authenticated_client("admin")

    def no_installer(_package_spec: str) -> tuple[list[str], None]:
        raise RuntimeError("Neither uv nor pip is available to install packages.")

    monkeypatch.setattr(package_install, "package_installer_command", no_installer)
    unavailable_plan = client.post(PLAN).json()
    unavailable = client.post(APPLY, json=_apply_body(unavailable_plan, "no-installer"))
    assert unavailable.status_code == 503
    assert unavailable.json()["error"]["code"] == "installer_unavailable"
    journal = FileOperationJournalStore(tmp_path / "journal")
    assert journal.read(unavailable_plan["operation_id"]) is None

    monkeypatch.setattr(package_install, "package_installer_command", lambda spec: (["uv"], None))
    monkeypatch.setattr(package_install, "run_package_install", lambda spec: (False, "no wheel"))
    plan = client.post(PLAN).json()
    failed = client.post(APPLY, json=_apply_body(plan, "failing-install"))
    assert failed.status_code == 200, failed.text
    assert failed.json()["status"] == "failed"
    assert failed.json()["accepted"] is False
    assert failed.json()["message"] == "no wheel"
    entry = journal.read(plan["operation_id"])
    assert entry is not None
    assert entry.state.value == "failed"


def test_package_install_requires_admin_and_a_trusted_package(installs) -> None:
    operator = authenticated_client("operator")
    admin = authenticated_client("admin")

    assert operator.post(PLAN).status_code == 403
    untrusted = admin.post("/api/v1/admin/packages/not-a-phlo-package/install-plan")
    assert untrusted.status_code == 400
    assert untrusted.json()["error"]["code"] == "untrusted_package"
    assert installs == []
