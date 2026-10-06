"""Daily handoff contract; every GitHub read/write is mocked."""

import importlib
import json
import sys
from pathlib import Path
from unittest.mock import Mock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
delta = importlib.import_module("dependency_delta")
risk = importlib.import_module("report_dependency_risk")

SHA = "a" * 40
WORKFLOW = {"id": 7, "path": ".github/workflows/security.yml"}


@pytest.fixture
def context(monkeypatch, tmp_path):
    run = {
        "id": 10,
        "run_attempt": 2,
        "head_sha": SHA,
        "workflow_id": 7,
        "path": WORKFLOW["path"],
        "event": "schedule",
        "status": "completed",
        "conclusion": "failure",
        "head_branch": "main",
        "repository": {"full_name": risk.github.REPOSITORY},
        "head_repository": {"full_name": risk.github.REPOSITORY},
        "html_url": "https://github.com/phlohouse/phlo/actions/runs/10/attempts/2",
    }
    package = ("npm", "example", "1.0.0")
    inventory = {"a/package-lock.json": {package}, "b/package-lock.json": {package}}
    report = delta.compare(inventory, inventory, {package: ["GHSA-example"]})
    report.update(
        base=SHA,
        head=SHA,
        mode="queue",
        assessment="complete",
        inventory={path: sorted(packages) for path, packages in inventory.items()},
        unaccepted=report["existing"],
    )
    (tmp_path / "dependency-assessment.json").write_text(json.dumps(report))
    api = Mock(
        side_effect=lambda path: (
            run
            if "/attempts/" in path
            else WORKFLOW
            if "/workflows/" in path
            else {"default_branch": "main"}
        )
    )
    download = Mock(return_value=tmp_path)
    write = Mock()
    monkeypatch.setattr(risk.github, "api", api)
    monkeypatch.setattr(risk.github, "pages", Mock(return_value=[]))
    monkeypatch.setattr(risk.github, "download_artifact", download)
    monkeypatch.setattr(risk.subprocess, "run", write)
    return run, report, download, write, tmp_path


def test_handoff_schema_and_deduplication(context):
    run, report, download, write, _ = context
    assert risk.valid_report(report, SHA)
    risk.handoff(10, 2, SHA)
    assert download.call_args.args[:2] == (run, "dependency-assessment-10-2")
    write.assert_called_once()
    args = write.call_args.args[0]
    assert args[:3] == ["gh", "issue", "create"]
    body = args[args.index("--body") + 1]
    assert "a/package-lock.json" in body and "b/package-lock.json" in body
    assert "Maintainers own escalation" in body


@pytest.mark.parametrize(
    "mutation",
    [
        {"workflow_id": 99},
        {"path": ".github/workflows/pr.yml"},
        {"head_repository": {"full_name": "foreign/phlo"}},
        {"repository": {"full_name": "foreign/phlo"}},
        {"event": "workflow_dispatch"},
        {"head_branch": "feature"},
        {"status": "in_progress"},
        {"head_sha": "b" * 40},
        {"run_attempt": 1},
    ],
)
def test_wrong_producer_sha_attempt_rejected(context, mutation):
    run, _, download, write, _ = context
    run.update(mutation)
    with pytest.raises(ValueError):
        risk.handoff(10, 2, SHA)
    download.assert_not_called()
    write.assert_not_called()


def test_wrong_report_sha_rejected(context):
    _, report, _, write, directory = context
    report["head"] = "b" * 40
    (directory / "dependency-assessment.json").write_text(json.dumps(report))
    with pytest.raises(ValueError, match="different inspected SHA"):
        risk.handoff(10, 2, SHA)
    write.assert_not_called()


@pytest.mark.parametrize("kind", ["unavailable", "missing", "fragment", "no-artifact"])
def test_unavailable_scan_escalates(context, kind):
    _, _, download, write, directory = context
    if kind == "missing":
        (directory / "dependency-assessment.json").unlink()
    elif kind == "no-artifact":
        download.side_effect = ValueError("expected exactly one artifact")
    else:
        (directory / "dependency-assessment.json").write_text(
            json.dumps({"assessment": "complete" if kind == "fragment" else "unavailable"})
        )
    risk.handoff(10, 2, SHA)
    write.assert_called_once()
    args = write.call_args.args[0]
    assert "unavailable" in args[args.index("--title") + 1]
    assert "Repository maintainers own" in args[args.index("--body") + 1]
    assert "No clean verdict" in args[args.index("--body") + 1]


def test_duplicate_security_issue_preserves_paths(context, monkeypatch):
    run, report, _, write, _ = context
    title, body = next(iter(risk.requests(report, run["html_url"]).items()))
    issue = {"number": 42, "title": title, "body": body + "\n- `old/uv.lock`"}
    monkeypatch.setattr(risk.github, "pages", Mock(return_value=[issue]))
    risk.handoff(10, 2, SHA)
    write.assert_not_called()
    issue["body"] = "Maintainer notes\n- `old/uv.lock`"
    risk.handoff(10, 2, SHA)
    args = write.call_args.args[0]
    assert args[:4] == ["gh", "issue", "edit", "42"]
    assert all(
        path in args[-1]
        for path in (
            "old/uv.lock",
            "a/package-lock.json",
            "b/package-lock.json",
            "Maintainer notes",
        )
    )
