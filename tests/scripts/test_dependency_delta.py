"""Regression contracts for dependency risk and failed assessment handling."""

import importlib.util
import json
import subprocess
import sys
from datetime import UTC
from pathlib import Path

import pytest

SPEC = importlib.util.spec_from_file_location(
    "dependency_delta", Path(__file__).resolve().parents[2] / "scripts/dependency_delta.py"
)
assert SPEC and SPEC.loader
delta = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(delta)


def test_new_advisory_on_unchanged_dependency_does_not_block() -> None:
    package = ("PyPI", "example", "1.0")
    inventory = {"uv.lock": {package}}
    result = delta.compare(inventory, inventory, {package: ["NEW-ADVISORY"]})
    assert result["introduced"] == []
    assert result["existing"][0]["advisories"] == ["NEW-ADVISORY"]


def test_added_or_changed_vulnerable_version_blocks_even_with_same_advisory() -> None:
    old, new = ("npm", "example", "1.0.0"), ("npm", "example", "1.0.1")
    report = delta.compare({"app": {old}}, {"app": {new}}, {new: ["CVE-1"]})
    assert report["introduced"][0]["version"] == "1.0.1"
    assert report["existing"] == []


def test_moving_vulnerable_dependency_to_another_product_is_new_risk() -> None:
    package = ("npm", "example", "1.0.0")
    report = delta.compare({"docs": {package}}, {"agent": {package}}, {package: ["CVE-1"]})
    assert report["introduced"][0]["lockfile"] == "agent"


def test_fixed_dependency_and_removed_dependency_do_not_block() -> None:
    old, new = ("npm", "example", "1.0.0"), ("npm", "example", "2.0.0")
    assert delta.compare({"app": {old}}, {"app": {new}}, {new: []})["introduced"] == []
    assert delta.compare({"app": {old}}, {"app": set()}, {})["introduced"] == []


def test_uv_registry_names_are_normalized_and_workspace_packages_excluded() -> None:
    content = b"""[[package]]
name = "Some_Package"
version = "1.2"
source = { registry = "https://pypi.org/simple" }
[[package]]
name = "phlo"
version = "0.14.0"
source = { editable = "." }
"""
    assert delta.parse_lock("uv.lock", content) == {("PyPI", "some-package", "1.2")}


def test_uv_git_sourced_packages_are_excluded() -> None:
    """Git-pinned deps have no PyPI version OSV can assess — excluded like
    workspace links, not a parse error."""
    content = b"""[[package]]
name = "observe-core"
version = "0.3.0"
source = { git = "https://github.com/phlohouse/phlo-observe.git?tag=observe-core%2Fv0.3.0#b478056dfadd5ff275d7fbaf7171f2319c52d48f" }
[[package]]
name = "some_package"
version = "1.0"
source = { registry = "https://pypi.org/simple" }
"""
    assert delta.parse_lock("uv.lock", content) == {("PyPI", "some-package", "1.0")}


def test_npm_nested_scopes_and_aliases_preserve_real_package_identity() -> None:
    content = json.dumps(
        {
            "lockfileVersion": 3,
            "packages": {
                "": {"name": "local-app", "version": "1.0.0"},
                "node_modules/a/node_modules/@scope/dep": {
                    "version": "2.0.0",
                    "resolved": "https://registry.npmjs.org/@scope/dep/-/dep-2.0.0.tgz",
                },
                "node_modules/alias": {
                    "name": "real-name",
                    "version": "1.0.0",
                    "resolved": "https://registry.npmjs.org/real-name/-/real-name-1.0.0.tgz",
                },
                "node_modules/local": {"link": True},
            },
        }
    ).encode()
    assert delta.parse_lock("package-lock.json", content) == {
        ("npm", "@scope/dep", "2.0.0"),
        ("npm", "real-name", "1.0.0"),
    }


def test_npm_workspace_metadata_is_excluded_but_its_dependencies_are_audited() -> None:
    content = json.dumps(
        {
            "lockfileVersion": 3,
            "packages": {
                "": {"workspaces": ["replacement"]},
                "replacement": {"name": "private-ui"},
                "node_modules/private-ui": {"link": True},
                "replacement/node_modules/example": {
                    "version": "2.3.4",
                    "resolved": "https://registry.npmjs.org/example/-/example-2.3.4.tgz",
                },
            },
        }
    ).encode()
    assert delta.parse_lock("package-lock.json", content) == {("npm", "example", "2.3.4")}


def test_all_current_product_locks_are_readable() -> None:
    root = Path(__file__).resolve().parents[2]
    sha = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root).decode().strip()
    assert len(delta.inventory(sha)) >= 15


def test_renamed_product_lock_uses_its_previous_path(monkeypatch) -> None:
    lock = json.dumps(
        {
            "lockfileVersion": 3,
            "packages": {
                "node_modules/example": {
                    "version": "1.0.0",
                    "resolved": "https://registry.npmjs.org/example/-/example-1.0.0.tgz",
                }
            },
        }
    ).encode()

    def git_show(command, *, capture_output, check):
        del capture_output
        if command[1] == "ls-tree":
            return subprocess.CompletedProcess(
                command, 0, stdout=b"apps/phlo-agent/package-lock.json\0"
            )
        if command[-1].endswith(":apps/phlo-agent/package-lock.json"):
            return subprocess.CompletedProcess(command, 0, stdout=lock)
        return subprocess.CompletedProcess(command, 128, stdout=b"")

    monkeypatch.setattr(delta.subprocess, "run", git_show)
    assert delta.inventory("a" * 40) == {
        "apps/phlo-github-writer/package-lock.json": {("npm", "example", "1.0.0")}
    }


@pytest.mark.parametrize("response", [{"results": []}, {"results": [{"next_page_token": "x"}]}])
def test_incomplete_scanner_response_is_not_clean(monkeypatch, response) -> None:
    import io

    monkeypatch.setattr(
        delta.urllib.request, "urlopen", lambda *a, **kw: io.BytesIO(json.dumps(response).encode())
    )
    with pytest.raises(ValueError):
        delta.query_osv([("npm", "example", "1.0.0")])


def test_bad_base_writes_unavailable_report_and_fails(tmp_path) -> None:
    root = Path(__file__).resolve().parents[2]
    output = tmp_path / "report.json"
    result = subprocess.run(
        [
            sys.executable,
            str(root / "scripts/dependency_delta.py"),
            "--base",
            "not-a-sha",
            "--head",
            "0" * 40,
            "--output",
            str(output),
        ],
        capture_output=True,
    )
    assert result.returncode == 2
    assert json.loads(output.read_text())["assessment"] == "unavailable"


@pytest.mark.parametrize(
    "source",
    ["git+https://github.com/example/dep.git", "file:../dep", "https://example.com/dep.tgz", ""],
)
def test_non_registry_npm_source_is_unavailable_even_with_a_normal_version(source):
    content = json.dumps(
        {
            "lockfileVersion": 3,
            "packages": {"node_modules/example": {"version": "1.0.0", "resolved": source}},
        }
    ).encode()
    with pytest.raises(ValueError, match="Unsupported npm source"):
        delta.parse_lock("package-lock.json", content)


def test_added_nested_lock_is_new_risk_and_deleted_lock_disappears():
    package = ("PyPI", "example", "1.0")
    report = delta.compare(
        {"uv.lock": {package}}, {"examples/nested/uv.lock": {package}}, {package: ["CVE-1"]}
    )
    assert report["introduced"][0]["lockfile"] == "examples/nested/uv.lock"
    assert not report["existing"]


def test_reviewed_window_and_invalid_records(tmp_path, monkeypatch):
    import io
    from datetime import datetime

    ledger = tmp_path / "ledger.json"
    record = {
        "lockfile": "uv.lock",
        "ecosystem": "PyPI",
        "name": "example",
        "version": "1.0",
        "advisory": "CVE-1",
        "severity": "high",
        "exploited": False,
        "owner": "maintainer-a",
        "rationale": "Fix is being validated",
        "first_seen": "2026-10-01T00:00:00+00:00",
        "expires": "2026-10-08T00:00:00+00:00",
        "reviewed_by": "maintainer-b",
        "review_url": "https://github.com/phlohouse/phlo/pull/123",
    }
    finding = {key: record[key] for key in ("lockfile", "ecosystem", "name", "version")}
    finding["advisories"] = ["CVE-1"]
    now = datetime(2026, 10, 6, tzinfo=UTC)
    monkeypatch.setattr(
        delta.urllib.request,
        "urlopen",
        lambda *a, **kw: io.BytesIO(b'{"database_specific":{"severity":"HIGH"}}'),
    )
    monkeypatch.setattr(delta, "verify_risk_review", lambda record: None)
    ledger.write_text(json.dumps([record]))
    assert delta.assess_existing([finding], ledger, now) == []
    for changes in (
        {"severity": "unknown"},
        {"expires": "2026-10-05T00:00:00+00:00"},
        {"expires": "2026-11-01T00:00:00+00:00"},
        {"reviewed_by": "phlo-agent"},
        {"owner": ""},
        {"exploited": True},
    ):
        ledger.write_text(json.dumps([{**record, **changes}]))
        with pytest.raises(ValueError):
            delta.assess_existing([finding], ledger, now)
    ledger.write_text("[]")
    assert delta.assess_existing([finding], ledger, now) == [finding]
    ledger.write_text("[{}]")
    with pytest.raises(ValueError):
        delta.assess_existing([], ledger, now)


@pytest.mark.parametrize("mode, expected", [("delta", 0), ("queue", 1), ("release", 1)])
def test_full_modes_block_existing_debt(mode, expected, tmp_path, monkeypatch):
    package = ("npm", "example", "1.0.0")
    monkeypatch.setattr(delta, "inventory", lambda ref: {"nested/package-lock.json": {package}})
    monkeypatch.setattr(delta, "query_osv", lambda packages: {package: ["CVE-1"]})
    ledger = tmp_path / "ledger.json"
    ledger.write_text("[]")
    output = tmp_path / "assessment.json"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "dependency_delta",
            "--base",
            "a" * 40,
            "--head",
            "b" * 40,
            "--mode",
            mode,
            "--ledger",
            str(ledger),
            "--output",
            str(output),
        ],
    )
    assert delta.main() == expected
    report = json.loads(output.read_text())
    assert report["assessment"] == "complete"
    assert report["existing"] and not report["introduced"]


def test_tracked_nested_inventory_reads_revision_not_worktree(tmp_path, monkeypatch):
    def git(*args):
        return subprocess.check_output(["git", *args], cwd=tmp_path).decode().strip()

    git("init", "-q")
    lock = tmp_path / "examples/nested/uv.lock"
    lock.parent.mkdir(parents=True)
    lock.write_text(
        '[[package]]\nname="example"\nversion="1.0"\nsource={registry="https://pypi.org/simple"}\n'
    )
    git("add", ".")
    git("-c", "user.name=Test", "-c", "user.email=test@example.com", "commit", "-qm", "fixture")
    sha = git("rev-parse", "HEAD")
    lock.write_text("invalid untrusted worktree content")
    monkeypatch.chdir(tmp_path)
    package = ("PyPI", "example", "1.0")
    inventory = delta.inventory(sha)
    assert inventory == {"examples/nested/uv.lock": {package}}
    assert delta.compare({}, inventory, {package: ["CVE-1"]})["introduced"]


@pytest.mark.parametrize(
    "latest,accepted", [("COMMENTED", True), ("CHANGES_REQUESTED", False), ("DISMISSED", False)]
)
def test_risk_review_checks_authenticated_final_revision_and_revocation(
    monkeypatch, latest, accepted
):
    record = {
        "reviewed_by": "maintainer",
        "review_url": "https://github.com/phlohouse/phlo/pull/123",
    }
    head = "a" * 40
    approval = {
        "id": 1,
        "state": "APPROVED",
        "commit_id": head,
        "user": {"login": "maintainer", "type": "User"},
    }
    responses = {
        "repos/phlohouse/phlo/pulls/123": {
            "merged_at": "2026-10-06",
            "base": {"ref": "main"},
            "head": {"sha": head},
        },
        "repos/phlohouse/phlo/collaborators/maintainer/permission": {"permission": "maintain"},
        "repos/phlohouse/phlo/pulls/123/reviews?per_page=100": [
            [approval, {**approval, "id": 2, "state": latest}]
        ],
    }

    def process(args, **kwargs):
        value = [record] if args[0] == "git" else responses[args[2]]
        return subprocess.CompletedProcess(args, 0, stdout=json.dumps(value))

    monkeypatch.setattr(delta.subprocess, "run", process)
    if accepted:
        delta.verify_risk_review(record)
        approval["commit_id"] = "b" * 40
    with pytest.raises(ValueError, match="exact final revision"):
        delta.verify_risk_review(record)
