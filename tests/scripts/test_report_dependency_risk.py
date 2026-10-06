"""Daily handoff contract; every GitHub read/write is mocked."""

import importlib
import json
import re
import subprocess
import sys
from pathlib import Path
from unittest.mock import Mock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
delta = importlib.import_module("dependency_delta")
risk = importlib.import_module("report_dependency_risk")

SHA = "a" * 40
WORKFLOW = {"id": 7, "path": ".github/workflows/security.yml"}


class GitHubStore:
    """Apply CLI writes to in-memory issues, PRs and comments."""

    def __init__(self):
        self.issues = []
        self.pulls = []
        self.comments = {}

    def pages(self, path, key):
        if "/issues?" in path:
            return self.issues
        if "/pulls?" in path:
            return self.pulls
        if path.endswith("/comments"):
            return self.comments.get(int(path.split("/")[-2]), [])
        raise AssertionError(path)

    def write(self, args, *, check):
        assert check is True
        assert args[args.index("--repo") + 1] == risk.github.REPOSITORY
        body = args[args.index("--body") + 1]
        if args[:3] == ["gh", "pr", "comment"]:
            self.comments.setdefault(int(args[3]), []).append({"body": body})
        elif args[:3] == ["gh", "issue", "create"]:
            self.issues.append(
                {
                    "number": len(self.issues) + 1,
                    "title": args[args.index("--title") + 1],
                    "body": body,
                    "labels": [args[i + 1] for i, value in enumerate(args) if value == "--label"],
                }
            )
        elif args[:3] == ["gh", "issue", "edit"]:
            issue = next(issue for issue in self.issues if issue["number"] == int(args[3]))
            issue["body"] = body
        else:
            raise AssertionError(args)
        return subprocess.CompletedProcess(args, 0)


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
    store = GitHubStore()
    write = Mock(side_effect=store.write)
    monkeypatch.setattr(risk.github, "api", api)
    monkeypatch.setattr(risk.github, "pages", store.pages)
    monkeypatch.setattr(risk.github, "download_artifact", download)
    monkeypatch.setattr(risk.subprocess, "run", write)
    return run, report, download, write, tmp_path, store


def test_handoff_schema_and_deduplication(context):
    _, report, _, _, _, store = context
    assert risk.valid_report(report, SHA)
    risk.handoff(10, 2, SHA)
    assert len(store.issues) == 1
    issue = store.issues[0]
    assert issue["title"] == "security(deps): GHSA-example in npm/example 1.0.0"
    assert issue["labels"] == ["security", "dependencies"]
    body = issue["body"]
    assert set(re.findall(r"^- `([^`]+)`$", body, re.M)) == {
        "a/package-lock.json",
        "b/package-lock.json",
    }
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
    run, _, download, write, _, _ = context
    run.update(mutation)
    with pytest.raises(ValueError):
        risk.handoff(10, 2, SHA)
    download.assert_not_called()
    write.assert_not_called()


def test_wrong_report_sha_rejected(context):
    _, report, _, write, directory, _ = context
    report["head"] = "b" * 40
    (directory / "dependency-assessment.json").write_text(json.dumps(report))
    with pytest.raises(ValueError, match="different inspected SHA"):
        risk.handoff(10, 2, SHA)
    write.assert_not_called()


@pytest.mark.parametrize("kind", ["unavailable", "missing", "fragment", "no-artifact"])
def test_unavailable_scan_escalates(context, kind):
    _, _, download, _, directory, store = context
    if kind == "missing":
        (directory / "dependency-assessment.json").unlink()
    elif kind == "no-artifact":
        download.side_effect = ValueError("expected exactly one artifact")
    else:
        (directory / "dependency-assessment.json").write_text(
            json.dumps({"assessment": "complete" if kind == "fragment" else "unavailable"})
        )
    risk.handoff(10, 2, SHA)
    assert len(store.issues) == 1
    issue = store.issues[0]
    assert issue["title"] == "security(deps): dependency assessment unavailable"
    assert "Repository maintainers own" in issue["body"]
    assert "No clean verdict" in issue["body"]


def test_duplicate_security_issue_preserves_paths(context):
    run, report, _, write, _, store = context
    title, body = next(iter(risk.requests(report, run["html_url"]).items()))
    issue = {"number": 42, "title": title, "body": body + "\n- `old/uv.lock`"}
    store.issues.append(issue)
    risk.handoff(10, 2, SHA)
    write.assert_not_called()
    issue["body"] = "Maintainer notes\n- `old/uv.lock`"
    risk.handoff(10, 2, SHA)
    assert len(store.issues) == 1
    assert store.issues[0]["number"] == 42
    assert set(re.findall(r"^- `([^`]+)`$", issue["body"], re.M)) == {
        "old/uv.lock",
        "a/package-lock.json",
        "b/package-lock.json",
    }
    assert issue["body"].splitlines()[0] == "Maintainer notes"


def test_existing_advisory_pr_reused_with_all_consumers(context):
    _, _, _, _, _, store = context
    store.pulls.append({"number": 63, "title": "fix(example): GHSA-example", "body": ""})
    risk.handoff(10, 2, SHA)
    risk.handoff(10, 2, SHA)
    assert store.issues == []
    assert len(store.comments[63]) == 1
    assert set(re.findall(r"^- `([^`]+)`$", store.comments[63][0]["body"], re.M)) == {
        "a/package-lock.json",
        "b/package-lock.json",
    }


@pytest.mark.parametrize(
    "title",
    [
        "Update example to 2.0.0",
        "Fix GHSA-example-other in example",
        "Fix GHSA-example in other/example",
    ],
)
def test_unrelated_update_does_not_suppress_intake(context, title):
    _, _, _, _, _, store = context
    store.pulls.append({"number": 63, "title": title, "body": ""})
    risk.handoff(10, 2, SHA)
    assert len(store.issues) == 1
    assert store.comments == {}


def test_scoped_package_and_new_consumers_are_preserved(context):
    run, _, _, _, _, store = context
    title = "security(deps): GHSA-example in npm/@scope/example 1.0.0"
    store.pulls.extend(
        [
            {"number": 62, "title": "Fix GHSA-example in @different/example", "body": ""},
            {"number": 63, "title": "Fix GHSA-example in @scope/example", "body": ""},
        ]
    )
    package = ("npm", "@scope/example", "1.0.0")
    inventory = {"c/package-lock.json": {package}}
    report = delta.compare(inventory, inventory, {package: ["GHSA-example"]})
    report["assessment"] = "complete"
    store.comments[63] = [{"body": f"{title}\n\n- `a/package-lock.json`"}]
    risk.publish(report, run)
    risk.publish(report, run)
    assert store.issues == []
    assert 62 not in store.comments
    assert len(store.comments[63]) == 2
    assert "- `c/package-lock.json`" in store.comments[63][-1]["body"]
