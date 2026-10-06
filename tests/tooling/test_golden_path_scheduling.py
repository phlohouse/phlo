"""Keep nightly maintenance separate from immutable candidate acceptance."""

from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
WORKFLOW_ROOT = REPO_ROOT / ".github" / "workflows"


def test_nightly_maintenance_does_not_claim_artifact_qualification() -> None:
    ci = yaml.safe_load((WORKFLOW_ROOT / "ci.yml").read_text(encoding="utf-8"))
    nightly = yaml.safe_load((WORKFLOW_ROOT / "nightly.yml").read_text(encoding="utf-8"))
    assert "release-golden-path" not in ci["jobs"]
    assert ci["jobs"]["windows-release-contract"]["name"] == (
        "windows / release golden path contract"
    )
    report = nightly["jobs"]["release-artifact-acceptance"]
    assert "artifact-not-staged" in report["steps"][0]["run"]
    assert "--candidate-bom" not in str(report)
    summary = nightly["jobs"]["nightly-status"]
    assert "upstream-compatibility" in summary["needs"]
    assert "$UPSTREAM_COMPATIBILITY" in summary["steps"][0]["run"]


def test_nightly_release_golden_path_keeps_dispatch_and_schedule() -> None:
    nightly = yaml.safe_load((WORKFLOW_ROOT / "nightly.yml").read_text(encoding="utf-8"))
    triggers = nightly.get("on") or nightly[True]

    assert "workflow_dispatch" in triggers
    assert {"cron": "0 3 * * *"} in triggers["schedule"]


def test_artifact_acceptance_requires_authenticated_staging_without_missing_bom_escape() -> None:
    workflow = yaml.safe_load(
        (WORKFLOW_ROOT / "release-artifact-acceptance.yml").read_text(encoding="utf-8")
    )
    steps = workflow["jobs"]["acceptance"]["steps"]
    scripts = [step for step in steps if "run" in step]
    assert "release_provenance.py collect" in scripts[0]["run"]
    assert "--candidate-bom" in scripts[1]["run"]
    assert all("if" not in step for step in scripts)
    assert steps[-1]["with"]["if-no-files-found"] == "error"
