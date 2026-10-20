"""Filtered CI checkouts retain ancestry and fail closed on unavailable historical blobs."""

import importlib.util
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]


def _checkout_options(workflow, job):
    document = yaml.safe_load((ROOT / ".github/workflows" / workflow).read_text())
    return next(
        step["with"]
        for step in document["jobs"][job]["steps"]
        if step.get("uses", "").startswith("actions/checkout@")
    )


def _git(root, *args):
    return subprocess.run(
        ["git", "-C", str(root), *args],
        capture_output=True,
        text=True,
        check=True,
        timeout=20,
    ).stdout.strip()


def _commit(root, message):
    _git(root, "add", ".")
    _git(root, "commit", "-qm", message)
    return _git(root, "rev-parse", "HEAD")


@pytest.fixture
def history(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    _git(source, "init", "-q", "--initial-branch=main")
    for key, value in {
        "user.name": "Test",
        "user.email": "test@example.com",
        "commit.gpgsign": "false",
        "tag.gpgsign": "false",
        "core.hooksPath": "/dev/null",
        "uploadpack.allowFilter": "true",
        "uploadpack.allowAnySHA1InWant": "true",
    }.items():
        _git(source, "config", key, value)
    (source / "legacy.py").write_text("value = 1\n")
    (source / "pyproject.toml").write_text('[project]\nname = "example"\nversion = "1.2.3"\n')
    baseline = _commit(source, "release baseline")
    _git(source, "tag", "-a", "v1.2.3", "-m", "release baseline")
    _git(source, "checkout", "-qb", "pull-head")
    (source / "legacy.py").write_text("value = 2\n")
    (source / "pyproject.toml").write_text('[project]\nname = "example"\nversion = "1.2.4"\n')
    _commit(source, "pull request")
    _git(source, "checkout", "-q", "main")
    (source / "base-only.txt").write_text("first parent\n")
    first_parent = _commit(source, "advance main")
    _git(source, "merge", "--no-ff", "-qm", "synthetic merge", "pull-head")
    head = _git(source, "rev-parse", "HEAD")
    _git(source, "tag", "v1.2.4")
    _git(source, "checkout", "-qb", "event-base", first_parent)
    (source / "base-only.txt").write_text("later event base\n")
    event_base = _commit(source, "advance event base beyond the merge parent")

    checkout = tmp_path / "checkout"
    checkout.mkdir()
    _git(checkout, "init", "-q")
    _git(checkout, "remote", "add", "origin", source.as_uri())
    options = _checkout_options("ci.yml", "python-quality")
    assert options["fetch-depth"] == 0
    _git(
        checkout,
        "-c",
        "protocol.version=2",
        "fetch",
        "--no-tags",
        f"--filter={options['filter']}",
        "origin",
        "+refs/heads/*:refs/remotes/origin/*",
        "+refs/tags/*:refs/tags/*",
        f"+{head}:refs/remotes/pull/999/merge",
    )
    _git(checkout, "checkout", "-q", "--detach", head)
    return SimpleNamespace(
        checkout=checkout,
        baseline=baseline,
        first_parent=first_parent,
        event_base=event_base,
        head=head,
    )


def _missing(history, path):
    oid = _git(history.checkout, "rev-parse", f"{history.baseline}:{path}")
    missing = _git(history.checkout, "rev-list", "--objects", "--all", "--missing=print")
    return f"?{oid}" in missing.splitlines()


@pytest.mark.parametrize(
    "workflow,job,depth",
    [
        ("ci.yml", "python-quality", 0),
        ("pr.yml", "security", 0),
        (
            "container-security.yml",
            "checks",
            "${{ github.event_name == 'pull_request' && '0' || '1' }}",
        ),
    ],
)
def test_history_consumers_filter_blobs_without_changing_depth_or_credentials(workflow, job, depth):
    options = _checkout_options(workflow, job)
    assert options["filter"] == "blob:none"
    assert options["fetch-depth"] == depth
    assert options["persist-credentials"] is False


def test_filtered_checkout_preserves_exact_merge_ancestry_and_previous_release_content(history):
    root = history.checkout
    assert _git(root, "rev-parse", "HEAD") == history.head
    assert _git(root, "rev-parse", "--is-shallow-repository") == "false"
    assert _git(root, "rev-parse", "HEAD^") == history.first_parent
    assert history.first_parent != history.event_base
    assert _git(root, "merge-base", history.event_base, history.head) == history.first_parent
    describe = ("describe", "--tags", "--match", "v[0-9]*", "--abbrev=0")
    assert _git(root, *describe, "HEAD^") == "v1.2.3"
    assert _git(root, *describe, "HEAD") == "v1.2.4"
    assert _missing(history, "pyproject.toml")
    assert _git(root, "show", "v1.2.3:pyproject.toml") == (
        '[project]\nname = "example"\nversion = "1.2.3"'
    )
    assert not _missing(history, "pyproject.toml")


@pytest.mark.parametrize("remote_available,expected", [(True, 0), (False, 1)])
def test_header_baseline_requires_the_promised_blob(
    history, monkeypatch, tmp_path, remote_available, expected
):
    spec = importlib.util.spec_from_file_location(
        "checkout_headers", ROOT / "scripts/check_file_headers.py"
    )
    assert spec and spec.loader
    headers = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(headers)
    monkeypatch.chdir(history.checkout)
    monkeypatch.setattr(headers, "BASELINE", history.baseline)
    assert _missing(history, "legacy.py")
    Path("legacy.py").write_text("value = 1\n")
    if not remote_available:
        _git(history.checkout, "remote", "set-url", "origin", (tmp_path / "unavailable").as_uri())
    assert headers.main(["legacy.py"]) == expected
