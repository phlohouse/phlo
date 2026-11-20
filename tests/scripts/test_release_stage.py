"""Release build pinning and the GitHub reads the release relies on, with no external writes."""

import hashlib
import importlib
import io
import json
import subprocess
import sys
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
bom_module = importlib.import_module("release_candidate_bom")
golden = importlib.import_module("release_golden_path")
github = importlib.import_module("github_api")
publish = importlib.import_module("release_publish")
staging = importlib.import_module("release_stage")

SHA = "a" * 40
WORKFLOW_SHA = "b" * 40


@pytest.mark.parametrize("change", [None, "foreign", "ordinary", "unmerged", "wrong_sha", "prefix"])
def test_only_the_exact_release_merge_is_published(monkeypatch, change) -> None:
    repo = {"full_name": github.REPOSITORY}
    pr = {
        "merged_at": "2026-10-01T00:00:00Z",
        "merge_commit_sha": SHA,
        "title": "chore(release): phlo 0.18.0 + 38 packages",
        "base": {"ref": "main", "repo": repo},
        "head": {"ref": "relx/release/monorepo/main-release-set", "repo": repo},
    }
    if change == "foreign":
        pr["head"]["repo"] = {"full_name": "attacker/phlo"}
    elif change == "ordinary":
        pr["head"]["ref"] = "feature/release"
    elif change == "unmerged":
        pr["merged_at"] = None
    elif change == "wrong_sha":
        pr["merge_commit_sha"] = WORKFLOW_SHA
    elif change == "prefix":
        pr["head"]["ref"] = "relx/release/unrelated"
    monkeypatch.setattr(github, "pages", lambda *_: [pr])
    assert (github.merged_release_pr(SHA) is not None) is (change is None)
    assert publish.is_release_merge(SHA) is (change is None)


def run_record(run_id: int = 10) -> dict:
    return {
        "id": run_id,
        "repository": {"full_name": github.REPOSITORY},
        "head_repository": {"full_name": github.REPOSITORY},
        "head_branch": "main",
        "head_sha": WORKFLOW_SHA,
        "event": "workflow_dispatch",
        "status": "completed",
        "conclusion": "success",
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
        github.validate_artifact(artifact, run, "candidate")


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
    monkeypatch.setattr(github, "pages", lambda *a: [artifact])
    monkeypatch.setattr(
        github.subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a, 0, payload)
    )
    if name.startswith("../"):
        with pytest.raises(ValueError, match="unsafe"):
            github.download_artifact(run, "candidate", tmp_path)
        assert not (tmp_path.parent / "escape.json").exists()
    else:
        github.download_artifact(run, "candidate", tmp_path)
        assert (tmp_path / "candidate" / name).read_bytes() == b"{}"


def stage_files() -> dict[str, bytes]:
    artifacts = [
        {
            "kind": "source",
            "name": github.REPOSITORY,
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


@pytest.mark.parametrize("rehearsal", [False, True])
def test_pin_tags_rehearsals_apart_from_candidates(
    tmp_path: Path, monkeypatch, rehearsal: bool
) -> None:
    digest = "sha256:" + "e" * 64
    images = tmp_path / "images.json"
    images.write_text(
        json.dumps([{"image": f"ghcr.io/phlohouse/phlo-api-development:{SHA}", "digest": digest}])
    )
    validated: list[str] = []
    monkeypatch.setattr(staging, "validate_candidate", lambda sha: validated.append("candidate"))
    monkeypatch.setattr(staging, "validate_rehearsal", lambda sha: validated.append("rehearsal"))
    tags: list[str] = []
    monkeypatch.setattr(
        staging.subprocess,
        "run",
        lambda command, **k: tags.append(command[command.index("--tag") + 1]),
    )
    monkeypatch.setattr(staging.bom, "resolve_image_digest", lambda ref: digest)
    staged: dict[str, object] = {}

    def fake_stage(root, sha, output, *, built_distributions, image_digests, reuse_published):
        assert reuse_published is (not rehearsal)
        output.mkdir(parents=True)
        (output / "bom.json").write_text("{}")
        staged.update(image_digests)
        return SimpleNamespace(canonical_candidate_digest="d" * 64)

    monkeypatch.setattr(staging.bom, "stage", fake_stage)
    for name, value in {
        "GITHUB_SHA": WORKFLOW_SHA,
        "GITHUB_RUN_ID": "7",
        "GITHUB_RUN_ATTEMPT": "1",
    }.items():
        monkeypatch.setenv(name, value)
    output = tmp_path / "candidate"
    staging.pin(SHA, images, tmp_path / "dist", output, rehearsal=rehearsal)
    prefix = "rehearsal" if rehearsal else "candidate"
    assert validated == [prefix]
    assert tags == [f"ghcr.io/phlohouse/phlo-api:{prefix}-{SHA}"]
    assert staged == {"ghcr.io/phlohouse/phlo-api": digest}
    assert json.loads((output / "provenance.json").read_text())["rehearsal"] is rehearsal


def test_rehearsal_refuses_foreign_repositories(monkeypatch) -> None:
    monkeypatch.setenv("GITHUB_REPOSITORY", "someone/phlo")
    with pytest.raises(ValueError, match="allowlisted"):
        staging.validate_rehearsal(SHA)


def test_prebuilt_staging_consumes_independent_minio_without_rebuilding(
    tmp_path: Path, monkeypatch
) -> None:
    support = tmp_path / "registry/support/v1.json"
    support.parent.mkdir(parents=True)
    support.write_text(
        json.dumps(
            {
                "current_release": {"version": "0.15.0"},
                "release_set": {
                    "packages": [
                        {"name": "phlo", "version": "0.15.0"},
                        {"name": "phlo-core-plugins", "version": "0.14.1"},
                    ]
                },
            }
        ),
        encoding="utf-8",
    )
    built = tmp_path / "built"
    built.mkdir()
    (built / "phlo-0.15.0.tar.gz").write_bytes(b"sdist")
    (built / "phlo-0.15.0-py3-none-any.whl").write_bytes(b"wheel")
    (built / "phlo_core_plugins-0.14.1.tar.gz").write_bytes(b"plugins-sdist")
    (built / "phlo_core_plugins-0.14.1-py3-none-any.whl").write_bytes(b"plugins-wheel")
    monkeypatch.setattr(
        bom_module,
        "_run_git",
        lambda root, *args: github.REPOSITORY if args[0] == "remote" else SHA,
    )
    monkeypatch.setattr(
        bom_module.ReleaseTree,
        "image_references",
        lambda *a: {
            "service.yaml": [
                "ghcr.io/phlohouse/phlo-api:0.15.0",
                "ghcr.io/phlohouse/phlo-minio:0.29.1",
                "ghcr.io/phlohouse/phlo-observe/phlo-observer:0.2.3@sha256:" + "d" * 64,
                "postgres:18@sha256:" + "f" * 64,
            ]
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
        lambda image: (
            "sha256:" + "a" * 64
            if image == "ghcr.io/phlohouse/phlo-minio:0.29.1"
            else pytest.fail("only independent MinIO may resolve an already-published tag")
        ),
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
    assert (
        output / "distributions/phlo_core_plugins-0.14.1-py3-none-any.whl"
    ).read_bytes() == b"plugins-wheel"
    assert {
        artifact["name"]: artifact["version"]
        for artifact in candidate.bom["artifacts"]
        if artifact["kind"] == "wheel"
    } == {"phlo": "0.15.0", "phlo-core-plugins": "0.14.1"}
    observer = next(
        artifact
        for artifact in candidate.bom["artifacts"]
        if artifact["name"] == "ghcr.io/phlohouse/phlo-observe/phlo-observer"
    )
    assert observer["kind"] == "provider-image"
    assert observer["digest"] == "sha256:" + "d" * 64
    minio = next(
        artifact
        for artifact in candidate.bom["artifacts"]
        if artifact["name"] == "ghcr.io/phlohouse/phlo-minio"
    )
    assert minio == {
        "kind": "provider-image",
        "name": "ghcr.io/phlohouse/phlo-minio",
        "version": "0.29.1",
        "digest": "sha256:" + "a" * 64,
        "source": "service.yaml",
    }
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


def test_release_build_stages_unchanged_packages_from_pypi(tmp_path: Path, monkeypatch) -> None:
    """An already-published version is tested and released as PyPI's bytes, not a rebuild."""
    support = tmp_path / "registry/support/v1.json"
    support.parent.mkdir(parents=True)
    support.write_text(
        json.dumps(
            {
                "current_release": {"version": "0.15.0"},
                "release_set": {
                    "packages": [
                        {"name": "phlo", "version": "0.15.0"},
                        {"name": "phlo-core-plugins", "version": "0.14.1"},
                    ]
                },
            }
        ),
        encoding="utf-8",
    )
    built = tmp_path / "built"
    built.mkdir()
    for name in (
        "phlo-0.15.0.tar.gz",
        "phlo-0.15.0-py3-none-any.whl",
        "phlo_core_plugins-0.14.1.tar.gz",
        "phlo_core_plugins-0.14.1-py3-none-any.whl",
    ):
        (built / name).write_bytes(b"rebuilt " + name.encode())
    monkeypatch.setattr(
        bom_module,
        "_run_git",
        lambda root, *args: github.REPOSITORY if args[0] == "remote" else SHA,
    )
    monkeypatch.setattr(
        bom_module.ReleaseTree,
        "image_references",
        lambda *a: {
            "service.yaml": [
                "ghcr.io/phlohouse/phlo-api:0.15.0",
                "postgres:18@sha256:" + "f" * 64,
            ]
        },
    )
    published = {("phlo-core-plugins", "0.14.1")}
    monkeypatch.setattr(
        bom_module,
        "_pypi_release_files",
        lambda project, version, allow_missing=False: (
            {"file": ("0" * 64, "url")} if (project, version) in published else {}
        ),
    )

    def from_pypi(project, version, kind, distributions_dir):
        name = f"phlo_core_plugins-0.14.1{'.tar.gz' if kind == 'sdist' else '-py3-none-any.whl'}"
        (distributions_dir / name).write_bytes(b"pypi " + name.encode())
        return {
            "kind": kind,
            "name": project,
            "version": version,
            "digest": bom_module.file_sha256(distributions_dir / name),
            "source": f"pypi:{project}/{version}",
        }

    monkeypatch.setattr(bom_module, "_download_distribution", from_pypi)
    output = tmp_path / "candidate"
    candidate = bom_module.stage(
        tmp_path,
        None,
        output,
        built_distributions=built,
        image_digests={"ghcr.io/phlohouse/phlo-api": "sha256:" + "e" * 64},
        reuse_published=True,
    )
    sources = {
        (artifact["name"], artifact["kind"]): artifact["source"]
        for artifact in candidate.bom["artifacts"]
        if artifact["kind"] in ("sdist", "wheel")
    }
    assert sources[("phlo-core-plugins", "wheel")] == "pypi:phlo-core-plugins/0.14.1"
    assert sources[("phlo", "wheel")].startswith("local-build:")
    assert (output / "distributions/phlo_core_plugins-0.14.1-py3-none-any.whl").read_bytes() == (
        b"pypi phlo_core_plugins-0.14.1-py3-none-any.whl"
    )
    bom_module.verify_staged_distributions(candidate.bom, output)


def test_github_pages_reads_every_page_and_refuses_truncated_listings(monkeypatch) -> None:
    calls = []

    def fake_run(command, **_):
        calls.append(command[-1])
        page = int(command[-1].rsplit("page=", 1)[1])
        rows = [{"id": n} for n in range(100 if page == 1 else 3)]
        return SimpleNamespace(stdout=json.dumps({"artifacts": rows, "total_count": 103}))

    monkeypatch.setattr(github.subprocess, "run", fake_run)
    assert len(github.pages("repos/x/runs/1/artifacts", "artifacts")) == 103
    assert calls == [
        "repos/x/runs/1/artifacts?per_page=100&page=1",
        "repos/x/runs/1/artifacts?per_page=100&page=2",
    ]

    def truncated(command, **_):
        return SimpleNamespace(stdout=json.dumps({"artifacts": [{}], "total_count": 5}))

    monkeypatch.setattr(github.subprocess, "run", truncated)
    with pytest.raises(ValueError, match="truncated"):
        github.pages("repos/x/runs/1/artifacts?status=completed", "artifacts")


@pytest.mark.parametrize(
    ("sha", "env", "protected", "message"),
    [
        ("A" * 40, {}, True, "full lowercase SHA"),
        (SHA, {"GITHUB_REPOSITORY": "someone/phlo"}, True, "allowlisted"),
        (SHA, {"GITHUB_REF": "refs/heads/feature"}, True, "from main"),
        (SHA, {}, False, "not protected"),
    ],
)
def test_release_pin_requires_a_protected_main_commit(
    monkeypatch, sha, env, protected, message
) -> None:
    monkeypatch.setenv("GITHUB_REPOSITORY", github.REPOSITORY)
    monkeypatch.setenv("GITHUB_REF", "refs/heads/main")
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setattr(github, "api", lambda _: {"protected": protected})
    monkeypatch.setattr(staging.subprocess, "run", lambda *a, **k: pytest.fail("no git call"))
    with pytest.raises(ValueError, match=message):
        staging.validate_candidate(sha)


def test_release_pin_checks_the_commit_is_on_main(monkeypatch) -> None:
    monkeypatch.setenv("GITHUB_REPOSITORY", github.REPOSITORY)
    monkeypatch.setenv("GITHUB_REF", "refs/heads/main")
    monkeypatch.setattr(github, "api", lambda _: {"protected": True})
    commands = []
    monkeypatch.setattr(staging.subprocess, "run", lambda command, **_: commands.append(command))
    staging.validate_candidate(SHA)
    assert commands == [["git", "merge-base", "--is-ancestor", SHA, "origin/main"]]


@pytest.mark.parametrize("rehearsal", [False, True])
def test_stage_cli_only_pins(monkeypatch, tmp_path: Path, rehearsal: bool) -> None:
    seen = {}
    monkeypatch.setattr(staging, "pin", lambda *args, **kwargs: seen.update(args=args, **kwargs))
    argv = ["release_stage.py", "pin", "--candidate-sha", SHA, "--output", str(tmp_path)]
    monkeypatch.setattr(sys, "argv", argv + (["--rehearsal"] if rehearsal else []))
    staging.main()
    assert seen == {"args": (SHA, None, None, tmp_path), "rehearsal": rehearsal}
    monkeypatch.setattr(sys, "argv", ["release_stage.py", "reserve", "--candidate-sha", SHA])
    with pytest.raises(SystemExit):
        staging.main()
