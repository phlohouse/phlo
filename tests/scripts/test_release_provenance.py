"""Exercise producer authentication and native approval with no external writes."""

import copy
import hashlib
import importlib
import io
import json
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
bom_module = importlib.import_module("release_candidate_bom")
golden = importlib.import_module("release_golden_path")
provenance = importlib.import_module("release_provenance")
staging = importlib.import_module("release_stage")

SHA = "a" * 40
WORKFLOW_SHA = "b" * 40
STAGE_WORKFLOW = {"id": 1, "path": provenance.STAGE}
ACCEPTANCE_WORKFLOW = {"id": 2, "path": provenance.ACCEPTANCE}


def run_record(run_id: int = 10, *, acceptance: bool = False) -> dict:
    workflow = ACCEPTANCE_WORKFLOW if acceptance else STAGE_WORKFLOW
    return {
        "id": run_id,
        "workflow_id": workflow["id"],
        "path": workflow["path"],
        "repository": {"full_name": provenance.REPOSITORY},
        "head_repository": {"full_name": provenance.REPOSITORY},
        "head_branch": "main",
        "head_sha": WORKFLOW_SHA,
        "event": "workflow_dispatch",
        "status": "completed",
        "conclusion": "success",
        "display_title": f"accept {SHA} stage 10" if acceptance else f"stage {SHA}",
        "run_attempt": 1,
        "run_started_at": "2026-10-01T00:00:00Z",
        "updated_at": "2026-10-01T01:00:00Z",
    }


def artifact_record(run: dict, name: str, payload: bytes = b"") -> dict:
    return {
        "id": 20,
        "name": name,
        "expired": False,
        "digest": "sha256:" + hashlib.sha256(payload).hexdigest(),
        "workflow_run": {"id": run["id"], "head_sha": run["head_sha"]},
        "created_at": "2026-10-01T00:59:00Z",
    }


@pytest.mark.parametrize(
    "mutation",
    [
        {"head_repository": {"full_name": "attacker/phlo"}},
        {"workflow_id": 99},
        {"path": ".github/workflows/nightly.yml"},
        {"head_branch": "feature"},
        {"display_title": f"stage {'c' * 40}"},
        {"status": "in_progress"},
        {"conclusion": "failure"},
        {"conclusion": "cancelled"},
        {"event": "pull_request"},
    ],
)
def test_wrong_producer_sha_or_incomplete_run_is_rejected(mutation: dict) -> None:
    run = {**run_record(), **mutation}
    with pytest.raises(ValueError):
        provenance.validate_run(run, STAGE_WORKFLOW, SHA, f"stage {SHA}")


def test_candidate_sha_is_distinct_from_trusted_workflow_sha() -> None:
    provenance.validate_run(run_record(), STAGE_WORKFLOW, SHA, f"stage {SHA}")


@pytest.mark.parametrize(
    "mutation",
    [
        {"expired": True},
        {"created_at": "2026-09-30T23:59:00Z"},
        {"workflow_run": {"id": 11, "head_sha": WORKFLOW_SHA}},
        {"workflow_run": {"id": 10, "head_sha": SHA}},
        {"digest": ""},
        {"name": "candidate-other-sha"},
    ],
)
def test_stale_attempt_missing_digest_or_wrong_artifact_is_rejected(mutation: dict) -> None:
    run = run_record()
    artifact = {**artifact_record(run, "candidate"), **mutation}
    with pytest.raises(ValueError, match="stale-attempt"):
        provenance.validate_artifact(artifact, run, "candidate")


def archive_bytes(files: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, value in files.items():
            archive.writestr(name, value)
    return buffer.getvalue()


@pytest.mark.parametrize("name", ["file.json", "../../escape.json"])
def test_download_checks_archive_hash_and_paths(tmp_path: Path, monkeypatch, name: str) -> None:
    payload = archive_bytes({name: b"{}"})
    run = run_record()
    artifact = artifact_record(run, "candidate", payload)
    monkeypatch.setattr(provenance, "pages", lambda *a: [artifact])
    monkeypatch.setattr(
        provenance.subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a, 0, payload)
    )
    if name.startswith("../"):
        with pytest.raises(ValueError, match="unsafe"):
            provenance.download(run, STAGE_WORKFLOW, SHA, f"stage {SHA}", ["candidate"], tmp_path)
        assert not (tmp_path.parent / "escape.json").exists()
    else:
        provenance.download(run, STAGE_WORKFLOW, SHA, f"stage {SHA}", ["candidate"], tmp_path)
        assert (tmp_path / "candidate" / name).read_bytes() == b"{}"


def stage_files() -> dict[str, bytes]:
    artifacts = [
        {
            "kind": "source",
            "name": provenance.REPOSITORY,
            "version": "0.15.0",
            "digest": SHA,
            "source": "git",
        },
        {
            "kind": "support-manifest",
            "name": "registry/support/v1.json",
            "version": "0.15.0",
            "digest": "d" * 64,
            "source": "git:registry/support/v1.json",
        },
        {
            "kind": "sdist",
            "name": "phlo",
            "version": "0.15.0",
            "digest": hashlib.sha256(b"sdist").hexdigest(),
            "source": "staged:phlo.tar.gz",
        },
        {
            "kind": "wheel",
            "name": "phlo",
            "version": "0.15.0",
            "digest": hashlib.sha256(b"wheel").hexdigest(),
            "source": "staged:phlo.whl",
        },
        {
            "kind": "first-party-image",
            "name": "ghcr.io/phlohouse/phlo-api",
            "version": "0.15.0",
            "digest": "sha256:" + "e" * 64,
            "source": "service.yaml",
        },
        {
            "kind": "provider-image",
            "name": "postgres",
            "version": "18",
            "digest": "sha256:" + "f" * 64,
            "source": "service.yaml",
        },
    ]
    bom = bom_module.make_bom(artifacts, SHA, SHA)
    encoded = json.dumps(bom).encode()
    record = {
        "candidate_sha": SHA,
        "workflow_sha": WORKFLOW_SHA,
        "run_id": 10,
        "run_attempt": 1,
        "bom_sha256": hashlib.sha256(encoded).hexdigest(),
        "canonical_candidate_digest": bom["canonical_candidate_digest"],
        "staged_utc": "2026-10-01T00:00:00Z",
    }
    return {
        "bom.json": encoded,
        "provenance.json": json.dumps(record).encode(),
        "distributions/phlo.tar.gz": b"sdist",
        "distributions/phlo.whl": b"wheel",
    }


@pytest.mark.parametrize(
    "failure",
    ["failed_prior_attempt", "wrong_stage", "restaged", "missing_bom", "wrong_bom_sha", "passed"],
)
def test_collect_does_not_allow_omitting_failed_attempts(
    tmp_path: Path, monkeypatch, failure: str
) -> None:
    stage = run_record()
    if failure == "restaged":
        stage["run_attempt"] = 2
    acceptance = run_record(11, acceptance=True)
    acceptance["run_attempt"] = 2
    first = copy.deepcopy(acceptance)
    first["run_attempt"] = 1
    if failure == "failed_prior_attempt":
        first["conclusion"] = "failure"
    if failure == "wrong_stage":
        first["display_title"] = f"accept {SHA} stage 9"
    files = stage_files()
    if failure == "missing_bom":
        files.pop("bom.json")
    if failure == "wrong_bom_sha":
        record = json.loads(files["provenance.json"])
        record["candidate_sha"] = "c" * 40
        files["provenance.json"] = json.dumps(record).encode()
    calls = []

    def fake_api(path: str):
        calls.append(path)
        if path.endswith("branches/main"):
            return {"protected": True}
        if path.endswith("workflows/release-stage.yml"):
            return STAGE_WORKFLOW
        if path.endswith("workflows/release-artifact-acceptance.yml"):
            return ACCEPTANCE_WORKFLOW
        if path.endswith("runs/10"):
            return stage
        if path.endswith("runs/11"):
            return acceptance
        if path.endswith("attempts/1"):
            return first
        if path.endswith("attempts/2"):
            return acceptance
        raise AssertionError(path)

    def fake_download(run, workflow, sha, title, names, dest):
        provenance.validate_run(run, workflow, sha, title)
        if workflow == STAGE_WORKFLOW:
            for name, value in files.items():
                path = dest / names[0] / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(value)

    monkeypatch.setattr(provenance, "api", fake_api)
    monkeypatch.setattr(provenance, "pages", lambda *a: [acceptance])
    monkeypatch.setattr(provenance, "download", fake_download)
    if failure == "passed":
        provenance.collect(SHA, 10, tmp_path, evidence=True)
        assert any(path.endswith("attempts/1") for path in calls)
        assert any(path.endswith("attempts/2") for path in calls)
    else:
        with pytest.raises((ValueError, bom_module.BomError)):
            provenance.collect(SHA, 10, tmp_path, evidence=True)


def protected_environment() -> dict:
    return {
        "protection_rules": [
            {
                "type": "required_reviewers",
                "prevent_self_review": True,
                "reviewers": [{"type": "User", "reviewer": {"login": "owner"}}],
            }
        ]
    }


def approved_review() -> list[dict]:
    return [
        {
            "state": "approved",
            "user": {"login": "owner", "type": "User"},
            "environments": [{"name": "release"}],
        }
    ]


@pytest.mark.parametrize(
    "failure",
    [
        "unprotected",
        "self_review_allowed",
        "no_reviewers",
        "wrong_owner",
        "self_approval",
        "wrong_environment",
        "bot_approval",
        "rejected",
    ],
)
def test_native_approval_fails_closed(failure: str) -> None:
    environment = protected_environment()
    reviews = approved_review()
    actor = "operator"
    if failure == "unprotected":
        environment = {}
    elif failure == "self_review_allowed":
        environment["protection_rules"][0]["prevent_self_review"] = False
    elif failure == "no_reviewers":
        environment["protection_rules"][0]["reviewers"] = []
    elif failure == "wrong_owner":
        reviews[0]["user"]["login"] = "someone"
    elif failure == "self_approval":
        actor = "owner"
    elif failure == "wrong_environment":
        reviews[0]["environments"] = [{"name": "pypi"}]
    elif failure == "bot_approval":
        reviews[0]["user"]["type"] = "Bot"
    else:
        reviews[0]["state"] = "rejected"
    with pytest.raises(ValueError):
        provenance.validate_approval(environment, reviews, actor)


def test_authenticated_required_reviewer_can_approve() -> None:
    assert (
        provenance.validate_approval(protected_environment(), approved_review(), "operator")[
            "user"
        ]["login"]
        == "owner"
    )


@pytest.mark.parametrize("mode", ["rerun", "already_reserved"])
def test_staging_refuses_rebuild_before_any_write(monkeypatch, mode: str) -> None:
    monkeypatch.setattr(staging, "validate_candidate", lambda sha: None)
    monkeypatch.setenv("GITHUB_RUN_ATTEMPT", "2" if mode == "rerun" else "1")
    monkeypatch.setattr(provenance, "pages", lambda *a: [{"tag_name": f"candidate-{SHA}"}])
    monkeypatch.setattr(staging.subprocess, "run", lambda *a, **k: pytest.fail("must not write"))
    with pytest.raises(ValueError):
        staging.reserve(SHA)


def test_prebuilt_staging_pins_bytes_without_build_or_registry_resolution(
    tmp_path: Path, monkeypatch
) -> None:
    support = tmp_path / "registry/support/v1.json"
    support.parent.mkdir(parents=True)
    support.write_text(
        json.dumps(
            {
                "current_release": {"version": "0.15.0"},
                "release_set": {"packages": [{"name": "phlo"}]},
            }
        ),
        encoding="utf-8",
    )
    built = tmp_path / "built"
    built.mkdir()
    (built / "phlo-0.15.0.tar.gz").write_bytes(b"sdist")
    (built / "phlo-0.15.0-py3-none-any.whl").write_bytes(b"wheel")
    monkeypatch.setattr(
        bom_module,
        "_run_git",
        lambda root, *args: provenance.REPOSITORY if args[0] == "remote" else SHA,
    )
    monkeypatch.setattr(
        bom_module.ReleaseTree,
        "image_references",
        lambda *a: {
            "service.yaml": ["ghcr.io/phlohouse/phlo-api:0.15.0", "postgres:18@sha256:" + "f" * 64]
        },
    )
    monkeypatch.setattr(
        bom_module.subprocess,
        "run",
        lambda *a, **k: pytest.fail("prebuilt staging must not rebuild"),
    )
    monkeypatch.setattr(
        bom_module,
        "resolve_image_digest",
        lambda *a: pytest.fail("must not resolve mutable image tags"),
    )
    output = tmp_path / "candidate"
    candidate = bom_module.stage(
        tmp_path,
        None,
        output,
        built_distributions=built,
        image_digests={"ghcr.io/phlohouse/phlo-api": "sha256:" + "e" * 64},
    )
    assert candidate.release_commit == SHA
    assert (output / "distributions/phlo-0.15.0-py3-none-any.whl").read_bytes() == b"wheel"
    bom_module.verify_staged_distributions(candidate.bom, output)
    with pytest.raises(bom_module.BomError, match="append-only"):
        bom_module.stage(tmp_path, None, output, built_distributions=built)


def test_support_validation_never_executes_candidate_scripts(tmp_path: Path, monkeypatch) -> None:
    tree = tmp_path / f"release-tree-{SHA[:12]}"
    manifest = tree / "registry/support/v1.json"
    manifest.parent.mkdir(parents=True)
    manifest.write_text("{}", encoding="utf-8")
    script = tree / "scripts/validate_support_manifest.py"
    script.parent.mkdir()
    script.write_text("raise AssertionError('candidate code must not execute')", encoding="utf-8")
    bom = json.loads(stage_files()["bom.json"])
    bom["artifacts"][1]["digest"] = bom_module.file_sha256(manifest)
    config = golden.RunConfig(
        repo_root=tmp_path,
        project_dir=tmp_path / "project",
        wheelhouse=tmp_path / "wheelhouse",
        operator_env=tmp_path / "operator",
        project_name="owned",
        bom=bom,
        staging_dir=tmp_path / "candidate",
    )
    called = []
    monkeypatch.setattr(golden, "run", lambda *a, **k: pytest.fail("must not run candidate script"))
    monkeypatch.setattr(
        golden.validate_support_manifest,
        "validate_manifest",
        lambda document, *, repo_root: called.append(repo_root) or [],
    )
    assert golden.verify_support_boundary(config)["exit_code"] == 0
    assert called == [tree]


@pytest.mark.parametrize(
    "failure",
    [
        "none",
        "wrong_sha",
        "stale_attempt",
        "wrong_workflow",
        "unprotected_branch",
        "unprotected_environment",
        "self_rerun",
    ],
)
def test_live_approval_uses_current_authenticated_run(monkeypatch, failure: str) -> None:
    for key, value in {
        "GITHUB_REPOSITORY": provenance.REPOSITORY,
        "GITHUB_REF": "refs/heads/main",
        "GITHUB_SHA": WORKFLOW_SHA,
        "GITHUB_RUN_ID": "10",
        "GITHUB_RUN_ATTEMPT": "1",
    }.items():
        monkeypatch.setenv(key, value)
    workflow = {"id": 3, "path": ".github/workflows/release-promotion.yml"}
    run = {
        **run_record(),
        "workflow_id": 3,
        "path": workflow["path"],
        "actor": {"login": "operator"},
        "triggering_actor": {"login": "operator"},
    }
    if failure == "wrong_sha":
        run["head_sha"] = SHA
    elif failure == "stale_attempt":
        run["run_attempt"] = 2
    elif failure == "wrong_workflow":
        run["workflow_id"] = 99
    elif failure == "self_rerun":
        run["triggering_actor"]["login"] = "owner"
    responses = {
        f"repos/{provenance.REPOSITORY}/branches/main": {
            "protected": failure != "unprotected_branch"
        },
        f"repos/{provenance.REPOSITORY}/actions/runs/10": run,
        f"repos/{provenance.REPOSITORY}/actions/workflows/release-promotion.yml": workflow,
        f"repos/{provenance.REPOSITORY}/environments/release": {}
        if failure == "unprotected_environment"
        else protected_environment(),
        f"repos/{provenance.REPOSITORY}/actions/runs/10/approvals": approved_review(),
    }
    monkeypatch.setattr(provenance, "api", lambda path: responses[path])
    if failure == "none":
        assert provenance.live_approval()["user"]["login"] == "owner"
    else:
        with pytest.raises(ValueError):
            provenance.live_approval()


def test_deleted_draft_does_not_permit_restaging(monkeypatch) -> None:
    monkeypatch.setattr(staging, "validate_candidate", lambda sha: None)
    monkeypatch.setenv("GITHUB_RUN_ATTEMPT", "1")
    monkeypatch.setenv("GITHUB_RUN_ID", "11")
    monkeypatch.setattr(provenance, "api", lambda *a: STAGE_WORKFLOW)
    monkeypatch.setattr(provenance, "pages", lambda path, key: [run_record()] if key else [])
    monkeypatch.setattr(staging.subprocess, "run", lambda *a, **k: pytest.fail("must not write"))
    with pytest.raises(ValueError, match="prior staging attempt"):
        staging.reserve(SHA)
