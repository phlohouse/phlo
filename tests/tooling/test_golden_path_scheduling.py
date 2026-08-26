"""Workflow contracts for the scheduled release golden path.

Parses the checked-in GitHub workflows and locks their structure: the
nightly run owns the release-golden-path job (scheduled and dispatchable),
the release-candidate workflow delegates to it, and CI carries only the
Windows contract job.
"""

from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
WORKFLOW_ROOT = REPO_ROOT / ".github" / "workflows"


def test_release_golden_path_is_required_candidate_evidence() -> None:
    ci = yaml.safe_load((WORKFLOW_ROOT / "ci.yml").read_text(encoding="utf-8"))
    nightly = yaml.safe_load((WORKFLOW_ROOT / "nightly.yml").read_text(encoding="utf-8"))
    candidate = yaml.safe_load(
        (WORKFLOW_ROOT / "release-candidate.yml").read_text(encoding="utf-8")
    )

    assert "release-golden-path" not in ci["jobs"]
    assert candidate["jobs"]["nightly"] == {
        "name": "release candidate / release evidence",
        "uses": "./.github/workflows/nightly.yml",
        "secrets": {
            "POSTGRES_PASSWORD": "${{ secrets.POSTGRES_PASSWORD }}",
            "MINIO_ROOT_PASSWORD": "${{ secrets.MINIO_ROOT_PASSWORD }}",
            "SUPERSET_ADMIN_PASSWORD": "${{ secrets.SUPERSET_ADMIN_PASSWORD }}",
        },
    }
    assert ci["jobs"]["windows-release-contract"]["name"] == (
        "windows / release golden path contract"
    )
    assert nightly["jobs"]["release-golden-path"] == {
        "name": "python / release golden path",
        "runs-on": "ubuntu-latest",
        "timeout-minutes": 45,
        "steps": [
            {
                "uses": "actions/checkout@de0fac2e4500dabe0009e67214ff5f5447ce83dd",
                "with": {"persist-credentials": False},
            },
            {
                "name": "Install uv",
                "uses": "astral-sh/setup-uv@5a095e7a2014a4212f075830d4f7277575a9d098",
                "with": {"version": "0.12.1"},
            },
            {
                "name": "Run release artifact golden path",
                "run": "python3 scripts/release_golden_path.py",
            },
        ],
    }
    assert nightly["jobs"]["nightly-status"]["needs"] == [
        "full-integration",
        "release-golden-path",
    ]


def test_nightly_release_golden_path_keeps_dispatch_and_schedule() -> None:
    nightly = yaml.safe_load((WORKFLOW_ROOT / "nightly.yml").read_text(encoding="utf-8"))
    triggers = nightly.get("on") or nightly[True]

    assert "workflow_dispatch" in triggers
    assert {"cron": "0 3 * * *"} in triggers["schedule"]
