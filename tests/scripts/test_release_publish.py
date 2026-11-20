"""Publishing resumes a partial release and fails closed on any public difference."""

import hashlib
import importlib
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
bom_module = importlib.import_module("release_candidate_bom")
publish = importlib.import_module("release_publish")

SHA = "a" * 40
IMAGE = "ghcr.io/phlohouse/phlo-api"
IMAGE_DIGEST = "sha256:" + "e" * 64
FILES = {"phlo-0.19.1.tar.gz": b"sdist", "phlo-0.19.1-py3-none-any.whl": b"wheel"}


def digest(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def candidate(tmp_path: Path, *, rehearsal: bool = False) -> Path:
    root = tmp_path / "candidate"
    (root / "distributions").mkdir(parents=True)
    for name, payload in FILES.items():
        (root / "distributions" / name).write_bytes(payload)
    artifacts = [
        {
            "kind": "source",
            "name": "phlohouse/phlo",
            "version": "0.19.1",
            "digest": SHA,
            "source": "git",
        },
        {
            "kind": "support-manifest",
            "name": "registry/support/v1.json",
            "version": "0.19.1",
            "digest": "d" * 64,
            "source": "git:registry/support/v1.json",
        },
        {
            "kind": "sdist",
            "name": "phlo",
            "version": "0.19.1",
            "digest": digest(b"sdist"),
            "source": "local-build",
        },
        {
            "kind": "wheel",
            "name": "phlo",
            "version": "0.19.1",
            "digest": digest(b"wheel"),
            "source": "local-build",
        },
        {
            "kind": "first-party-image",
            "name": IMAGE,
            "version": "0.19.1",
            "digest": IMAGE_DIGEST,
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
    (root / "bom.json").write_text(json.dumps(bom_module.make_bom(artifacts, SHA, SHA)))
    (root / "provenance.json").write_text(
        json.dumps({"candidate_sha": SHA, "rehearsal": rehearsal})
    )
    return root


class Recorder:
    """Fake command runner: records commands and answers the reads publishing makes."""

    def __init__(self, *, remote_tag: str = "", release: dict | None = None) -> None:
        self.commands: list[list[str]] = []
        self.remote_tag = remote_tag
        self.release = release

    def __call__(self, command: list[str]) -> str:
        self.commands.append(command)
        if command[:2] == ["git", "ls-remote"]:
            return f"{self.remote_tag}\trefs/tags/v0.19.1\n" if self.remote_tag else ""
        if command[:2] == ["gh", "api"]:
            return json.dumps([[self.release]] if self.release else [[]])
        if command[:3] == ["gh", "release", "create"]:
            self.release = {"tag_name": "v0.19.1", "draft": True, "assets": []}
        if command[:3] == ["gh", "release", "upload"]:
            path = Path(command[4])
            self.release["assets"].append(
                {"name": path.name, "digest": f"sha256:{digest(path.read_bytes())}"}
            )
        return ""

    def ran(self, *prefix: str) -> list[list[str]]:
        return [command for command in self.commands if command[: len(prefix)] == list(prefix)]


def test_refuses_a_rehearsal_bom(tmp_path: Path) -> None:
    with pytest.raises(publish.PublishError, match="rehearsal"):
        publish.Release(candidate(tmp_path, rehearsal=True))


def test_tag_is_created_once_and_must_name_the_release_commit(tmp_path: Path) -> None:
    release = publish.Release(candidate(tmp_path))
    fresh = Recorder()
    publish.publish_tag(release, fresh)
    assert fresh.ran("git", "tag") == [["git", "tag", "v0.19.1", SHA]]
    again = Recorder(remote_tag=SHA)
    publish.publish_tag(release, again)
    assert not again.ran("git", "push")
    with pytest.raises(publish.PublishError, match="names"):
        publish.publish_tag(release, Recorder(remote_tag="b" * 40))


@pytest.mark.parametrize("already", [set(), {"phlo-0.19.1.tar.gz"}, set(FILES)])
def test_pypi_uploads_only_missing_files_and_waits_for_them(
    tmp_path: Path, monkeypatch, already: set[str]
) -> None:
    release = publish.Release(candidate(tmp_path))
    public = {name: (digest(FILES[name]), "url") for name in already}
    runner = Recorder()

    def upload(command: list[str]) -> str:
        runner(command)
        public.update(
            {Path(path).name: (digest(Path(path).read_bytes()), "url") for path in command[6:]}
        )
        return ""

    monkeypatch.delenv("UV_PUBLISH_TOKEN", raising=False)
    publish.publish_pypi(release, upload, lambda *_: dict(public))
    uploads = runner.ran("uv", "publish")
    missing = sorted(set(FILES) - already)
    if missing:
        assert sorted(Path(path).name for path in uploads[0][6:]) == missing
        assert uploads[0][:6] == [
            "uv",
            "publish",
            "--trusted-publishing",
            "always",
            "--check-url",
            publish.PYPI_SIMPLE,
        ]
    else:
        assert uploads == []
    assert set(public) == set(FILES)


def test_pypi_stops_before_uploading_when_a_public_file_differs(tmp_path: Path) -> None:
    release = publish.Release(candidate(tmp_path))
    runner = Recorder()
    with pytest.raises(publish.PublishError, match="differs"):
        publish.publish_pypi(release, runner, lambda *_: {"phlo-0.19.1.tar.gz": ("0" * 64, "url")})
    assert runner.commands == []


def test_pypi_refuses_token_credentials(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("UV_PUBLISH_TOKEN", "pypi-secret")
    with pytest.raises(publish.PublishError, match="trusted publishing"):
        publish.publish_pypi(publish.Release(candidate(tmp_path)), Recorder(), lambda *_: {})


def test_pypi_read_after_write_is_retried_then_fails_closed(tmp_path: Path) -> None:
    release = publish.Release(candidate(tmp_path))
    answers = iter([{}, {}, {name: (digest(data), "url") for name, data in FILES.items()}])
    sleeps: list[float] = []
    publish.verify_pypi(release, lambda *_: next(answers), attempts=3, delay=1, sleep=sleeps.append)
    assert sleeps == [1, 1]
    with pytest.raises(publish.PublishError, match="still does not list"):
        publish.verify_pypi(release, lambda *_: {}, attempts=2, delay=0, sleep=lambda _: None)


@pytest.mark.parametrize("existing", [None, IMAGE_DIGEST])
@pytest.mark.parametrize("signed", [False, True])
def test_images_are_tagged_from_the_tested_digest_then_signed_once(
    tmp_path: Path, existing: str | None, signed: bool
) -> None:
    release = publish.Release(candidate(tmp_path))
    runner = Recorder()
    state = {"digest": existing, "signed": signed}

    def resolve(_: str) -> str | None:
        return state["digest"]

    def run(command: list[str]) -> str:
        runner(command)
        if command[:3] == ["docker", "buildx", "imagetools"]:
            state["digest"] = IMAGE_DIGEST
        if command[:2] == ["cosign", "sign"]:
            state["signed"] = True
        if command[:2] == ["cosign", "verify"] and not state["signed"]:
            raise publish.PublishError("no signatures found")
        return ""

    publish.publish_images(release, run, resolve)
    assert bool(runner.ran("docker", "buildx", "imagetools", "create")) is (existing is None)
    expected_sign = [] if signed else [["cosign", "sign", "--yes", f"{IMAGE}@{IMAGE_DIGEST}"]]
    assert runner.ran("cosign", "sign") == expected_sign
    assert publish.SIGNING_IDENTITY in runner.ran("cosign", "verify")[-1]
    assert state["signed"]


def test_dry_run_reads_public_state_and_changes_nothing(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    release = publish.Release(candidate(tmp_path))
    executed: list[list[str]] = []

    def read_only(command: list[str]) -> str:
        executed.append(command)
        return "[[]]" if command[:2] == ["gh", "api"] else ""

    monkeypatch.setattr(publish, "run", read_only)
    dry = publish.DryRun()
    publish.publish_tag(release, dry)
    publish.publish_pypi(release, dry, lambda *_: {})
    publish.publish_images(release, dry, lambda _: None)
    publish.publish_github_release(release, dry, tmp_path / "notes.md")
    assert {tuple(command[:2]) for command in executed} == {("git", "ls-remote"), ("gh", "api")}
    planned = capsys.readouterr().out
    for action in ("git tag", "uv publish", "docker buildx imagetools create", "gh release create"):
        assert f"would run: {action}" in planned


def test_an_image_tag_with_another_digest_stops_the_release(tmp_path: Path) -> None:
    runner = Recorder()
    with pytest.raises(publish.PublishError, match="not the tested"):
        publish.publish_images(
            publish.Release(candidate(tmp_path)), runner, lambda _: "sha256:" + "0" * 64
        )
    assert not runner.ran("cosign")


def test_github_release_uploads_missing_assets_then_publishes(tmp_path: Path) -> None:
    root = candidate(tmp_path)
    release = publish.Release(root)
    notes = tmp_path / "notes.md"
    notes.write_text("notes\n")
    partial = {
        "tag_name": "v0.19.1",
        "draft": True,
        "assets": [
            {"name": "bom.json", "digest": f"sha256:{bom_module.file_sha256(root / 'bom.json')}"}
        ],
    }
    runner = Recorder(release=partial)
    publish.publish_github_release(release, runner, notes)
    assert not runner.ran("gh", "release", "create")
    assert sorted(
        Path(command[4]).name for command in runner.ran("gh", "release", "upload")
    ) == sorted(FILES)
    assert runner.ran("gh", "release", "edit")


def test_github_release_asset_with_other_bytes_stops_the_release(tmp_path: Path) -> None:
    release = publish.Release(candidate(tmp_path))
    notes = tmp_path / "notes.md"
    notes.write_text("notes\n")
    wrong = {
        "tag_name": "v0.19.1",
        "draft": True,
        "assets": [{"name": "phlo-0.19.1.tar.gz", "digest": "sha256:" + "0" * 64}],
    }
    runner = Recorder(release=wrong)
    with pytest.raises(publish.PublishError, match="differs"):
        publish.publish_github_release(release, runner, notes)
    assert not runner.ran("gh", "release", "edit")


def test_release_notes_come_from_the_version_changelog_section(tmp_path: Path) -> None:
    release = publish.Release(candidate(tmp_path))
    changelog = tmp_path / "CHANGELOG.md"
    changelog.write_text(
        "## [phlo 0.19.10 + 1 packages] - 2026-12-01\n\nlater\n\n"
        "## [phlo 0.19.1 + 39 packages] - 2026-11-21\n\n### Bug Fixes\n\n- fix\n\n"
        "## [phlo 0.19.0 + 39 packages] - 2026-11-15\n\nolder\n"
    )
    notes = publish.release_notes(release, changelog, tmp_path / "notes.md")
    assert notes.read_text() == "### Bug Fixes\n\n- fix\n"
    changelog.write_text("## [phlo 0.19.0 + 39 packages] - 2026-11-15\n")
    with pytest.raises(publish.PublishError, match="no section"):
        publish.release_notes(release, changelog, tmp_path / "notes.md")


def test_select_requires_a_release_merge_on_manual_dispatch(monkeypatch, capsys) -> None:
    monkeypatch.setattr(publish.github, "merged_release_pr", lambda sha: None)
    assert publish.main(["select", "--sha", SHA]) == 0
    assert capsys.readouterr().out == "release=false\n"
    assert publish.main(["select", "--sha", SHA, "--require"]) == 1
    monkeypatch.setattr(publish.github, "merged_release_pr", lambda sha: {"number": 1})
    assert publish.main(["select", "--sha", SHA, "--require"]) == 0
    assert capsys.readouterr().out == "release=true\n"
    assert publish.main(["select", "--sha", "main"]) == 1


def test_run_returns_output_and_reports_the_failing_command(monkeypatch) -> None:
    results = iter(
        [
            SimpleNamespace(returncode=0, stdout="ok\n", stderr=""),
            SimpleNamespace(returncode=3, stdout="", stderr="denied\n"),
        ]
    )
    monkeypatch.setattr(publish.subprocess, "run", lambda *a, **k: next(results))
    assert publish.run(["gh", "api", "x"]) == "ok\n"
    with pytest.raises(publish.PublishError, match=r"gh failed \(3\): denied"):
        publish.run(["gh", "api", "x"])


@pytest.mark.parametrize(
    ("error", "expected"),
    [("MANIFEST_UNKNOWN: manifest unknown", None), ("404 Not Found", None)],
)
def test_a_missing_image_tag_reads_as_absent(monkeypatch, error, expected) -> None:
    def resolve(_):
        raise bom_module.BomError(error)

    monkeypatch.setattr(bom_module, "resolve_image_digest", resolve)
    assert publish._remote_digest(f"{IMAGE}:0.19.1") is expected


def test_other_registry_errors_stop_the_release(monkeypatch) -> None:
    def resolve(_):
        raise bom_module.BomError("unauthorized")

    monkeypatch.setattr(bom_module, "resolve_image_digest", resolve)
    with pytest.raises(bom_module.BomError, match="unauthorized"):
        publish._remote_digest(f"{IMAGE}:0.19.1")


def test_publish_runs_every_step_in_order_and_summarises(tmp_path: Path, monkeypatch) -> None:
    root = candidate(tmp_path)
    changelog = tmp_path / "CHANGELOG.md"
    changelog.write_text("## [phlo 0.19.1](x) (2026-11-20)\n\n- fix\n")
    steps = []
    for name in ("publish_tag", "publish_images"):
        monkeypatch.setattr(publish, name, lambda release, runner, n=name: steps.append(n))
    monkeypatch.setattr(
        publish, "publish_pypi", lambda release, runner, files: steps.append("publish_pypi")
    )
    monkeypatch.setattr(
        publish,
        "publish_github_release",
        lambda release, runner, notes: steps.append(notes.read_text()),
    )
    summary = tmp_path / "summary"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    assert publish.main(["publish", "--candidate", str(root), "--changelog", str(changelog)]) == 0
    assert steps == ["publish_tag", "publish_pypi", "publish_images", "- fix\n"]
    assert summary.read_text() == "Published v0.19.1: 2 distributions, 1 signed images.\n"


def test_publish_reports_a_missing_changelog_section(tmp_path: Path, capsys) -> None:
    changelog = tmp_path / "CHANGELOG.md"
    changelog.write_text("## [phlo 0.19.0](x)\n")
    root = candidate(tmp_path)
    assert publish.main(["publish", "--candidate", str(root), "--changelog", str(changelog)]) == 1
    assert "no section for 0.19.1" in capsys.readouterr().err
