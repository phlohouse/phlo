"""Tests for scripts/nightly_evidence.py: failure reporting, cleanup, and
the evidence contract each step must produce."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

SCRIPT_PATH = Path(__file__).parents[2] / "scripts" / "nightly_evidence.py"
SPEC = importlib.util.spec_from_file_location("nightly_evidence", SCRIPT_PATH)
assert SPEC and SPEC.loader
nightly_evidence = importlib.util.module_from_spec(SPEC)
sys.modules["nightly_evidence"] = nightly_evidence
SPEC.loader.exec_module(nightly_evidence)


@pytest.fixture
def project(tmp_path):
    """A minimal generated-project shape: phlo.yaml plus an empty .phlo."""
    root = tmp_path / "proj"
    (root / ".phlo").mkdir(parents=True)
    (root / "phlo.yaml").write_text("name: nightly-evidence-project\n", encoding="utf-8")
    return root


def _stub_pipeline(monkeypatch, project, **overrides):
    """Replace every external step with a recorder; overrides may inject."""
    calls: list[str] = []
    steps = {
        "init_project": lambda scratch: project,
        "write_rbac_config": lambda p: p,
        "write_host_endpoint_override": lambda p: p,
        "start_services": lambda p: None,
        "wait_stack_ready": lambda p: None,
        "create_postgres_roles": lambda p: None,
        "create_minio_groups": lambda p: None,
        "sync_and_verify": lambda p: {"converged": True, "verify_exit_code": 0},
        "_iceberg_env": lambda p: None,
        "create_iceberg_table": lambda: "raw.nightly_evidence",
        "journaled_maintenance": lambda p, j: {"execute_result": {"status": "noop"}},
        "enforcement_probe": lambda p: {"postgres": {"allow": True, "deny": True}},
        "teardown": lambda p: calls.append("teardown"),
    }
    steps.update(overrides)
    for name, fn in steps.items():
        if name == "teardown":
            monkeypatch.setattr(nightly_evidence, name, fn)
        else:
            monkeypatch.setattr(
                nightly_evidence,
                name,
                (lambda n, f: lambda *a, **k: (calls.append(n), f(*a, **k))[1])(name, fn),
            )
    return calls


def _run_main(monkeypatch, tmp_path):
    out = tmp_path / "evidence"
    monkeypatch.setattr(sys, "argv", ["nightly_evidence.py", "--output", str(out)])
    return nightly_evidence.main(), json.loads((out / "nightly-evidence.json").read_text())


def test_main_success_writes_full_evidence(monkeypatch, tmp_path, project):
    calls = _stub_pipeline(monkeypatch, project)

    code, report = _run_main(monkeypatch, tmp_path)

    assert code == 0
    assert report["steps"]["init"] == "ok"
    assert report["steps"]["authz_sync_verify"] == "ok"
    assert report["steps"]["journaled_maintenance"] == "ok"
    assert report["steps"]["enforcement_probe"] == "ok"
    assert report["maintenance"]["table"] == "raw.nightly_evidence"
    assert report["environment"]["host"]
    assert calls[-1] == "teardown"


def test_main_drift_fails_but_still_reports_and_cleans_up(monkeypatch, tmp_path, project):
    calls = _stub_pipeline(monkeypatch, project, sync_and_verify=lambda p: {"converged": False})

    code, report = _run_main(monkeypatch, tmp_path)

    assert code == 1
    assert "drift" in report["error"]
    assert report["steps"]["authz_sync_verify"] == "ok"
    assert "journaled_maintenance" not in report["steps"]
    assert calls[-1] == "teardown"


def test_journaled_maintenance_rejects_unaccepted_apply(monkeypatch, tmp_path, project):
    journal_dir = tmp_path / "journal"
    journal_dir.mkdir()
    (journal_dir / "op.json").write_text(
        json.dumps({"action": "x", "state": "failed", "target": "t"}), encoding="utf-8"
    )
    plan = {"plan_token": "tok", "before_revision": 1}
    apply = {"accepted": False, "status": "blocked", "blockers": ["concurrent_change"]}
    outputs = iter([json.dumps(plan), json.dumps(apply)])
    monkeypatch.setattr(nightly_evidence, "phlo", lambda *a, **k: next(outputs))

    with pytest.raises(nightly_evidence.StepError, match="not accepted"):
        nightly_evidence.journaled_maintenance(project, journal_dir)


def test_journaled_maintenance_requires_succeeded_journal(monkeypatch, tmp_path, project):
    journal_dir = tmp_path / "journal"
    journal_dir.mkdir()
    (journal_dir / "op.json").write_text(
        json.dumps({"action": "x", "state": "submitted", "target": "t"}), encoding="utf-8"
    )
    plan = {"plan_token": "tok", "before_revision": 1}
    apply = {"accepted": True, "status": "noop"}
    outputs = iter([json.dumps(plan), json.dumps(apply)])
    monkeypatch.setattr(nightly_evidence, "phlo", lambda *a, **k: next(outputs))

    with pytest.raises(nightly_evidence.StepError, match="no succeeded"):
        nightly_evidence.journaled_maintenance(project, journal_dir)


def test_journaled_maintenance_happy_path(monkeypatch, tmp_path, project):
    journal_dir = tmp_path / "journal"
    journal_dir.mkdir()
    (journal_dir / "op.json").write_text(
        json.dumps({"action": "x", "state": "succeeded", "target": "t"}), encoding="utf-8"
    )
    plan = {"plan_token": "tok", "before_revision": 1}
    apply = {"accepted": True, "status": "noop"}
    outputs = iter([json.dumps(plan), json.dumps(apply)])
    monkeypatch.setattr(nightly_evidence, "phlo", lambda *a, **k: next(outputs))

    result = nightly_evidence.journaled_maintenance(project, journal_dir)

    assert result["execute_result"]["status"] == "noop"
    assert result["journal_states"] == ["succeeded"]
    assert (journal_dir.parent / "maintenance-plan.json").exists()


def _probe_results(*pairs):
    """Yield (rc, output) tuples for each probe_compose call."""
    return iter(pairs)


def test_enforcement_probe_records_allow_deny_and_nessie_boundary(monkeypatch, project):
    probes = _probe_results(
        (0, "1 row"),  # pg allow
        (1, "ERROR: permission denied for schema nightly_private"),  # pg deny
        (0, "raw/"),  # mc allow
        (0, "mc: <ERROR> Unable to list folder. Access Denied."),  # mc deny (rc 0!)
    )
    monkeypatch.setattr(nightly_evidence, "probe_compose", lambda *a, **k: next(probes))
    monkeypatch.setattr(nightly_evidence, "_http_status", lambda url: 200)
    monkeypatch.setattr(nightly_evidence, "compose", lambda *a, **k: "")
    monkeypatch.setattr(nightly_evidence, "wait_http", lambda url: None)

    result = nightly_evidence.enforcement_probe(project)

    assert result["postgres"]["allow"] is True
    assert result["postgres"]["deny"] is True
    assert "permission denied" in result["postgres"]["deny_evidence"]
    assert result["minio"]["allow"] is True
    assert result["minio"]["deny"] is True
    assert result["nessie"]["anonymous_status"] == 200
    assert result["nessie"]["enforcement_evaluated"] is False
    override = (project / ".phlo" / "overrides" / "compose.yaml").read_text()
    assert 'NESSIE_SERVER_AUTHORIZATION_ENABLED: "true"' in override


def test_enforcement_probe_fails_when_postgres_deny_allows(monkeypatch, project):
    probes = _probe_results(
        (0, "1 row"),
        (0, "1 row"),  # deny probe wrongly succeeds
        (0, "raw/"),
        (0, "Access Denied"),
    )
    monkeypatch.setattr(nightly_evidence, "probe_compose", lambda *a, **k: next(probes))

    with pytest.raises(nightly_evidence.StepError, match="deny probe failed"):
        nightly_evidence.enforcement_probe(project)


def test_enforcement_probe_fails_when_minio_deny_allows(monkeypatch, project):
    probes = _probe_results(
        (0, "1 row"),
        (1, "permission denied"),
        (0, "raw/"),
        (0, "raw/"),  # bucket-root listing wrongly succeeds — mc exits 0 either way
    )
    monkeypatch.setattr(nightly_evidence, "probe_compose", lambda *a, **k: next(probes))

    with pytest.raises(nightly_evidence.StepError, match="deny probe failed"):
        nightly_evidence.enforcement_probe(project)


def test_host_endpoint_override_precreates_nessie_mount_source(project):
    """The bind-mount source must exist host-owned before services start."""
    nightly_evidence.write_host_endpoint_override(project)

    assert (project / ".phlo" / "nessie").is_dir()
    assert (project / ".phlo" / "overrides" / "compose.yaml").is_file()


def test_compose_includes_override_layer(monkeypatch, tmp_path, project):
    override = project / ".phlo" / "overrides" / "compose.yaml"
    override.parent.mkdir(parents=True)
    override.write_text("services: {}\n", encoding="utf-8")
    seen: list[list[str]] = []

    class _Done:
        stdout = ""

    monkeypatch.setattr(nightly_evidence, "run", lambda cmd, **k: (seen.append(cmd), _Done())[1])

    nightly_evidence.compose(project, "ps")

    cmd = seen[0]
    compose_files = [cmd[i + 1] for i, flag in enumerate(cmd) if flag == "-f"]
    assert str(override) in compose_files
    assert str(project / ".phlo" / "docker-compose.yml") in compose_files
