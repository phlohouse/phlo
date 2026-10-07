"""File export contracts through executable capabilities and local manifests."""

import hashlib
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier, Event
from types import SimpleNamespace

import pytest

import phlo
from phlo.capabilities.registry import clear_capabilities, get_capability_registry
from phlo.capabilities.runtime import RuntimeRouting
from phlo.exports import ExportContext, resolve_export_manifest
from phlo.helpers.artifacts import verify_manifest_checksums


@pytest.fixture(autouse=True)
def isolated_assets():
    previous = get_capability_registry().list("asset")
    clear_capabilities("asset")
    yield
    clear_capabilities("asset")
    for spec in previous:
        get_capability_registry().register("asset", spec)


def execute(run_id="run-1", **routing):
    spec = get_capability_registry().list("asset")[0]
    runtime = SimpleNamespace(
        routing=RuntimeRouting(run_id=run_id, **routing),
        run_id=run_id,
        resources={},
        tags={},
        partition_key=None,
    )
    return list(spec.run.fn(runtime))[0]


def test_complete_export_has_named_checksummed_artifacts(tmp_path):
    @phlo.export(
        name="sky_survey",
        destination=tmp_path,
        depends_on=["observed_stars"],
        outputs={"catalog": "catalog.csv", "summary": "reports/summary.json"},
    )
    def write(context: ExportContext):
        assert context.run_id == "run-1"
        assert context.runtime.routing.ref == "survey-branch"
        context.upstream_versions["observed_stars"] = "snapshot-42"
        (context.staging_dir / "catalog.csv").write_bytes(b"star,x\nSirius,17\n")
        (context.staging_dir / "reports").mkdir()
        (context.staging_dir / "reports/summary.json").write_bytes(b'{"count":1}')

    result = execute(ref="survey-branch", partition_key="2026-10")
    spec = get_capability_registry().list("asset")[0]
    assert spec.key == "sky_survey"
    assert spec.deps == ["observed_stars"]
    manifest = resolve_export_manifest(tmp_path, "sky_survey")
    assert manifest.metadata["run_id"] == "run-1"
    assert manifest.metadata["partition_key"] == "2026-10"
    assert manifest.metadata["upstream_versions"] == {"observed_stars": "snapshot-42"}
    catalog, summary = manifest.artifacts
    assert catalog.metadata["name"] == "catalog"
    assert summary.metadata["name"] == "summary"
    assert catalog.size_bytes == 17
    assert summary.checksum == hashlib.sha256(b'{"count":1}').hexdigest()
    assert all(verify_manifest_checksums(manifest).values())
    assert result.metadata["phlo/export_manifest"]["metadata"] == manifest.metadata
    assert Path(result.metadata["phlo/export_manifest_path"]).is_file()


@pytest.mark.parametrize("failure", ["writer", "missing"])
def test_incomplete_set_preserves_current_and_retry_starts_empty(tmp_path, failure):
    attempts = []
    fail = False

    @phlo.export(
        name="survey",
        destination=tmp_path,
        outputs={"catalog": "catalog.csv", "summary": "summary.json"},
    )
    def write(context):
        attempts.append((context.run_id, context.staging_dir))
        assert list(context.staging_dir.iterdir()) == []
        (context.staging_dir / "catalog.csv").write_text(context.run_id, encoding="utf-8")
        if fail:
            (context.staging_dir / "scratch.txt").write_text("partial", encoding="utf-8")
            if failure == "writer":
                raise RuntimeError("summary writer failed")
            return
        (context.staging_dir / "summary.json").write_text(context.run_id, encoding="utf-8")

    execute("prior")
    prior = resolve_export_manifest(tmp_path, "survey")
    pointer = (tmp_path / "survey/current.json").read_bytes()
    fail = True
    with pytest.raises((RuntimeError, FileNotFoundError)):
        execute("retry-run")
    assert (tmp_path / "survey/current.json").read_bytes() == pointer
    assert resolve_export_manifest(tmp_path, "survey") == prior
    assert not list((tmp_path / "survey").glob(".attempt-*"))
    fail = False
    execute("retry-run", attempt=2)
    assert attempts[-1][0] == attempts[-2][0] == "retry-run"
    assert attempts[-1][1] != attempts[-2][1]
    current = resolve_export_manifest(tmp_path, "survey")
    assert current.metadata["run_id"] == "retry-run"
    assert [Path(entry.uri).read_text() for entry in current.artifacts] == ["retry-run"] * 2
    assert all(verify_manifest_checksums(prior).values())
    assert len(list((tmp_path / "survey/runs").iterdir())) == 2


def test_published_identity_is_idempotent_but_cannot_change(tmp_path):
    content = "original"

    @phlo.export(name="survey", destination=tmp_path, outputs={"data": "data.txt"})
    def write(context):
        (context.staging_dir / "data.txt").write_text(content, encoding="utf-8")

    first = execute()
    first_manifest = resolve_export_manifest(tmp_path, "survey")
    assert execute(attempt=2).metadata == first.metadata
    content = "changed"
    with pytest.raises(ValueError, match="already has different content"):
        execute(attempt=3)
    assert resolve_export_manifest(tmp_path, "survey") == first_manifest
    assert Path(first_manifest.artifacts[0].uri).read_text() == "original"
    assert len(list((tmp_path / "survey/runs").iterdir())) == 1
    # Detect external corruption as well as a writer changing its output.
    Path(first_manifest.artifacts[0].uri).write_text("tampered", encoding="utf-8")
    assert verify_manifest_checksums(first_manifest) == {first_manifest.artifacts[0].uri: False}
    content = "original"
    with pytest.raises(ValueError, match="already has different content"):
        execute()


def test_concurrent_runs_publish_in_completion_order_and_readers_keep_one_set(tmp_path):
    slow_started = Event()
    release_slow = Event()

    @phlo.export(name="survey", destination=tmp_path, outputs={"data": "data.txt"})
    def write(context):
        if context.run_id == "slow":
            slow_started.set()
            assert release_slow.wait(10)
        (context.staging_dir / "data.txt").write_text(context.run_id, encoding="utf-8")

    with ThreadPoolExecutor() as pool:
        slow = pool.submit(execute, "slow")
        assert slow_started.wait(10)
        pool.submit(execute, "fast").result(timeout=10)
        fast_manifest = resolve_export_manifest(tmp_path, "survey")
        release_slow.set()
        slow.result(timeout=10)
    assert resolve_export_manifest(tmp_path, "survey").metadata["run_id"] == "slow"
    assert fast_manifest.metadata["run_id"] == "fast"
    assert Path(fast_manifest.artifacts[0].uri).read_text() == "fast"


@pytest.mark.parametrize("path", ["../escape", "/absolute", "manifest.json", "", "."])
def test_output_paths_cannot_escape_or_replace_manifest(tmp_path, path):
    with pytest.raises(ValueError):
        phlo.export(name="survey", destination=tmp_path, outputs={"data": path})


def test_symlink_outputs_are_not_published(tmp_path):
    outside = tmp_path / "outside"
    outside.write_text("mutable", encoding="utf-8")

    @phlo.export(name="survey", destination=tmp_path, outputs={"data": "data.txt"})
    def write(context):
        (context.staging_dir / "data.txt").symlink_to(outside)

    with pytest.raises(ValueError, match="symlink"):
        execute()
    with pytest.raises(FileNotFoundError):
        resolve_export_manifest(tmp_path, "survey")


def test_concurrent_attempts_cannot_overwrite_same_identity(tmp_path):
    rendezvous = Barrier(2)

    @phlo.export(name="survey", destination=tmp_path, outputs={"data": "data.txt"})
    def write(context):
        (context.staging_dir / "data.txt").write_text(
            str(context.runtime.routing.attempt), encoding="utf-8"
        )
        rendezvous.wait(timeout=10)

    with ThreadPoolExecutor() as pool:
        futures = [pool.submit(execute, "same-id", attempt=index) for index in (1, 2)]
        errors = [future.exception(timeout=10) for future in futures]
    assert sum(error is None for error in errors) == 1
    assert sum(isinstance(error, ValueError) for error in errors) == 1
    winner = futures[errors.index(None)].result()
    manifest = resolve_export_manifest(tmp_path, "survey")
    assert winner.metadata["phlo/export_manifest_path"] == str(
        Path(manifest.artifacts[0].uri).parent / "manifest.json"
    )
    assert all(verify_manifest_checksums(manifest).values())
    assert len(list((tmp_path / "survey/runs").iterdir())) == 1


def test_pointer_failure_preserves_previous_set_and_can_retry(tmp_path, monkeypatch):
    @phlo.export(name="survey", destination=tmp_path, outputs={"data": "data.txt"})
    def write(context):
        (context.staging_dir / "data.txt").write_text(context.run_id, encoding="utf-8")

    execute("prior")
    prior = resolve_export_manifest(tmp_path, "survey")
    with monkeypatch.context() as patch:

        def fail_replace(self, target):
            raise OSError("publication unavailable")

        patch.setattr(Path, "replace", fail_replace)
        with pytest.raises(OSError, match="publication unavailable"):
            execute("next")
    assert resolve_export_manifest(tmp_path, "survey") == prior
    assert len(list((tmp_path / "survey/runs").iterdir())) == 2
    execute("next")
    assert resolve_export_manifest(tmp_path, "survey").metadata["run_id"] == "next"
    assert all(verify_manifest_checksums(prior).values())


def test_missing_identity_fails_before_writer(tmp_path):
    @phlo.export(name="survey", destination=tmp_path, outputs={"data": "data.txt"})
    def write(context):
        pytest.fail("writer must not run without identity")

    with pytest.raises(ValueError, match="stable runtime run_id"):
        execute(None)
    assert not (tmp_path / "survey").exists()
