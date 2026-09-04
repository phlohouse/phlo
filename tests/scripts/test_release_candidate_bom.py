"""Focused tests for the release-candidate BOM module.

Pins the canonical candidate digest definition, every structural BOM
invariant, staged-distribution digest verification, and the append-only
staging refusal.
"""

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location(
    "release_candidate_bom", REPO_ROOT / "scripts" / "release_candidate_bom.py"
)
assert _spec and _spec.loader
release_candidate_bom = importlib.util.module_from_spec(_spec)
sys.modules["release_candidate_bom"] = release_candidate_bom
_spec.loader.exec_module(release_candidate_bom)

WHEEL_DIGEST = "a" * 64
SDIST_DIGEST = "b" * 64
IMAGE_DIGEST = "sha256:" + "c" * 64
PROVIDER_DIGEST = "sha256:" + "d" * 64
COMMIT = "e" * 40
SUPPORT_DIGEST = "f" * 64


def _bom(canonical: str | None = None) -> dict[str, object]:
    artifacts = [
        {
            "kind": "source",
            "name": "phlohouse/phlo",
            "version": "0.14.0",
            "digest": COMMIT,
            "source": "git",
        },
        {
            "kind": "support-manifest",
            "name": "registry/support/v1.json",
            "version": "0.14.0",
            "digest": SUPPORT_DIGEST,
            "source": "git:registry/support/v1.json",
        },
        {
            "kind": "sdist",
            "name": "phlo",
            "version": "0.14.0",
            "digest": SDIST_DIGEST,
            "source": "pypi:phlo/0.14.0",
        },
        {
            "kind": "wheel",
            "name": "phlo",
            "version": "0.14.0",
            "digest": WHEEL_DIGEST,
            "source": "pypi:phlo/0.14.0",
        },
        {
            "kind": "first-party-image",
            "name": "ghcr.io/phlohouse/phlo-api",
            "version": "0.14.0",
            "digest": IMAGE_DIGEST,
            "source": "packages/phlo-api/src/phlo_api/service.yaml",
        },
        {
            "kind": "provider-image",
            "name": "postgres",
            "version": "18.4-alpine3.24",
            "digest": PROVIDER_DIGEST,
            "source": "packages/phlo-postgres/src/phlo_postgres/service.yaml",
        },
    ]
    return {
        "schema": release_candidate_bom.BOM_SCHEMA,
        "release_commit": COMMIT,
        "release_ref": "v0.14.0",
        "artifacts": artifacts,
        "canonical_candidate_digest": canonical
        or release_candidate_bom.canonical_candidate_digest(artifacts),
    }


def _bom_with_staged_files(tmp_path: Path) -> tuple[dict[str, object], Path]:
    """Write staged distribution files and bind their real digests into a BOM."""
    import hashlib

    distributions = tmp_path / "distributions"
    distributions.mkdir(parents=True, exist_ok=True)
    sdist = distributions / "phlo-0.14.0.tar.gz"
    wheel = distributions / "phlo-0.14.0-py3-none-any.whl"
    sdist.write_bytes(b"sdist-bytes")
    wheel.write_bytes(b"wheel-bytes")
    bom = _bom()
    bom["artifacts"][2]["digest"] = hashlib.sha256(b"sdist-bytes").hexdigest()  # type: ignore[index]
    bom["artifacts"][3]["digest"] = hashlib.sha256(b"wheel-bytes").hexdigest()  # type: ignore[index]
    bom["canonical_candidate_digest"] = release_candidate_bom.canonical_candidate_digest(
        bom["artifacts"]  # type: ignore[arg-type]
    )
    return bom, tmp_path


def test_canonical_digest_hashes_the_digest_array_in_bom_order() -> None:
    artifacts = _bom()["artifacts"]
    first = release_candidate_bom.canonical_candidate_digest(artifacts)  # type: ignore[arg-type]
    # The array order is part of the identity: two candidates with the same
    # artifacts in a different BOM order have different canonical digests.
    assert first != release_candidate_bom.canonical_candidate_digest(list(reversed(artifacts)))  # type: ignore[arg-type]
    assert first == release_candidate_bom.canonical_candidate_digest(list(artifacts))  # type: ignore[arg-type]

    import hashlib

    digests = [artifact["digest"] for artifact in artifacts]  # type: ignore[union-attr,index]
    canonical = json.dumps(digests, sort_keys=True, separators=(",", ":")).encode()
    assert first == hashlib.sha256(canonical).hexdigest()


def test_canonical_digest_changes_when_an_artifact_digest_changes() -> None:
    bom = _bom()
    bom["artifacts"][3]["digest"] = "9" * 64  # type: ignore[index]
    assert bom["canonical_candidate_digest"] != release_candidate_bom.canonical_candidate_digest(
        bom["artifacts"]  # type: ignore[arg-type]
    )


def test_validate_bom_accepts_a_complete_document() -> None:
    assert release_candidate_bom.validate_bom(_bom()) == _bom()


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda b: b.update(schema="other/v1"), "schema"),
        (lambda b: b.update(release_commit="abc123"), "release_commit"),
        (lambda b: b.update(artifacts=[]), "non-empty"),
        (lambda b: b["artifacts"][2].update(extra="field"), "exactly"),
        (lambda b: b["artifacts"][2].update(digest="xyz"), "digest"),
        (lambda b: b["artifacts"][4].update(digest="a" * 64), "sha256:<64-hex>"),
        (lambda b: b["artifacts"][0].update(digest="a" * 64), "git SHA"),
        (
            lambda b: b.update(canonical_candidate_digest="0" * 64),
            "canonical_candidate_digest",
        ),
        (lambda b: b["artifacts"].pop(2), "one sdist and one wheel"),
        (lambda b: b["artifacts"].pop(5), "missing required artifact kinds"),
        (lambda b: b["artifacts"][2].update(kind="mystery"), "unknown kind"),
        (lambda b: b["artifacts"][5].update(digest=PROVIDER_DIGEST), "duplicate image"),
    ],
)
def test_validate_bom_rejects_incoherent_documents(mutation, message) -> None:
    bom = _bom()
    if message == "duplicate image":
        bom["artifacts"].append(dict(bom["artifacts"][5]))  # type: ignore[index]
    mutation(bom)
    with pytest.raises(release_candidate_bom.BomError, match=message):
        release_candidate_bom.validate_bom(bom)


@pytest.mark.parametrize(
    ("reference", "name", "tag", "digest"),
    [
        ("postgres:18@sha256:" + "d" * 64, "postgres", "18", "sha256:" + "d" * 64),
        ("ghcr.io/phlohouse/phlo-api:0.14.0", "ghcr.io/phlohouse/phlo-api", "0.14.0", None),
        ("quay.io/minio/minio:v1", "quay.io/minio/minio", "v1", None),
        ("alpine", "alpine", "", None),
    ],
)
def test_parse_image_reference_splits_repositories_tags_and_digests(
    reference: str, name: str, tag: str, digest: str | None
) -> None:
    assert release_candidate_bom.parse_image_reference(reference) == (name, tag, digest)


def test_parse_image_reference_rejects_env_templates_and_bad_digests() -> None:
    with pytest.raises(release_candidate_bom.BomError):
        release_candidate_bom.parse_image_reference("${UNSET_IMAGE}")
    with pytest.raises(release_candidate_bom.BomError):
        release_candidate_bom.parse_image_reference("postgres:18@notadigest")


def test_staged_distributions_verify_against_bom_digests(tmp_path: Path) -> None:
    bom, staging_dir = _bom_with_staged_files(tmp_path)
    verified = release_candidate_bom.verify_staged_distributions(bom, staging_dir)
    assert [path.name for path in verified] == [
        "phlo-0.14.0.tar.gz",
        "phlo-0.14.0-py3-none-any.whl",
    ]


def test_staged_distributions_reject_a_digest_mismatch(tmp_path: Path) -> None:
    bom, staging_dir = _bom_with_staged_files(tmp_path)
    (staging_dir / "distributions" / "phlo-0.14.0.tar.gz").write_bytes(b"tampered")
    with pytest.raises(release_candidate_bom.BomError, match="missing or digest-mismatched"):
        release_candidate_bom.verify_staged_distributions(bom, staging_dir)


def test_staged_distributions_reject_unexpected_files(tmp_path: Path) -> None:
    bom, staging_dir = _bom_with_staged_files(tmp_path)
    (staging_dir / "distributions" / "stray-0.14.0-py3-none-any.whl").write_bytes(b"stray")
    with pytest.raises(release_candidate_bom.BomError, match="no BOM artifact covers"):
        release_candidate_bom.verify_staged_distributions(bom, staging_dir)


def test_image_references_collects_committed_service_images(tmp_path: Path) -> None:
    service = tmp_path / "packages" / "phlo-x" / "src" / "phlo_x" / "service.yaml"
    service.parent.mkdir(parents=True)
    service.write_text(
        "image: ghcr.io/phlohouse/phlo-api:0.14.0\n"
        "other: value\n"
        "image: postgres:18.4-alpine3.24@sha256:" + "d" * 64 + "\n"
        "image: ${CLICKHOUSE_IMAGE:-clickhouse/clickhouse-server:26@sha256:" + "c" * 64 + "}\n",
        encoding="utf-8",
    )
    tree = release_candidate_bom.ReleaseTree(tmp_path, None)
    references = tree.image_references("packages")
    assert references == {
        "packages/phlo-x/src/phlo_x/service.yaml": [
            "ghcr.io/phlohouse/phlo-api:0.14.0",
            "postgres:18.4-alpine3.24@sha256:" + "d" * 64,
            "${CLICKHOUSE_IMAGE:-clickhouse/clickhouse-server:26@sha256:" + "c" * 64 + "}",
        ]
    }


def test_stage_refuses_to_overwrite_a_staged_bom(tmp_path: Path, monkeypatch) -> None:
    (tmp_path / "bom.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(
        release_candidate_bom,
        "build_bom_artifacts",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("enumeration must not run")),
    )
    with pytest.raises(release_candidate_bom.BomError, match="append-only"):
        release_candidate_bom.stage(tmp_path, "v0.14.0", tmp_path)


def test_distribution_version_parsing_handles_wheels_and_sdists() -> None:
    parse = release_candidate_bom._distribution_version_from_filename
    assert parse("phlo-0.14.0.tar.gz", "phlo") == "0.14.0"
    assert parse("phlo-0.14.0-py3-none-any.whl", "phlo") == "0.14.0"
    assert parse("phlo_api-0.15.0.dev1-py3-none-any.whl", "phlo-api") == "0.15.0.dev1"


def test_build_from_tree_stages_built_bytes_with_local_build_sources(
    tmp_path: Path, monkeypatch
) -> None:
    export_dir = tmp_path / "export"
    dist_dir = export_dir / "dist"
    dist_dir.mkdir(parents=True)
    built = [
        "phlo-0.14.0.tar.gz",
        "phlo-0.14.0-py3-none-any.whl",
        "phlo_api-0.14.0.tar.gz",
        "phlo_api-0.14.0-py3-none-any.whl",
        "phlo_unlisted-0.14.0-py3-none-any.whl",
    ]
    for name in built:
        (dist_dir / name).write_bytes(f"bytes-of-{name}".encode())
    distributions_dir = tmp_path / "distributions"
    distributions_dir.mkdir()

    monkeypatch.setattr(release_candidate_bom, "_run_git", lambda repo_root, *args: "1" * 40)
    monkeypatch.setattr(
        release_candidate_bom.tempfile,
        "TemporaryDirectory",
        lambda prefix: type(
            "FakeTemp",
            (),
            {"__enter__": lambda self: str(export_dir), "__exit__": lambda self, *a: None},
        )(),
    )

    def fake_build(args, **kwargs):
        if args[0] == "uv":
            assert "--all-packages" in args
        return subprocess.CompletedProcess(args, 0, b"", b"")

    monkeypatch.setattr(release_candidate_bom.subprocess, "run", fake_build)

    class FakeTree:
        repo_root = tmp_path
        release_ref = "vNext"

    staged = release_candidate_bom._build_distributions_from_tree(
        FakeTree(), ["phlo", "phlo-api"], "0.14.0", distributions_dir
    )

    assert {(entry["name"], entry["kind"]) for entry in staged} == {
        ("phlo", "sdist"),
        ("phlo", "wheel"),
        ("phlo-api", "sdist"),
        ("phlo-api", "wheel"),
    }
    assert all(str(entry["source"]).startswith("local-build:") for entry in staged)
    assert (distributions_dir / "phlo-0.14.0-py3-none-any.whl").exists()
    # The wheel not in the release set is never staged.
    assert not (distributions_dir / "phlo_unlisted-0.14.0-py3-none-any.whl").exists()


def test_cli_verify_reports_a_valid_bom(tmp_path: Path, capsys) -> None:
    bom_path = tmp_path / "bom.json"
    bom_path.write_text(json.dumps(_bom()), encoding="utf-8")
    exit_code = release_candidate_bom.main(["verify", "--bom", str(bom_path)])
    assert exit_code == 0
    assert "is valid" in capsys.readouterr().out


def test_cli_verify_fails_closed_on_a_tampered_bom(tmp_path: Path, capsys) -> None:
    bom = _bom()
    bom["release_commit"] = "not-a-commit-sha"
    bom_path = tmp_path / "bom.json"
    bom_path.write_text(json.dumps(bom), encoding="utf-8")
    exit_code = release_candidate_bom.main(["verify", "--bom", str(bom_path)])
    assert exit_code == 1
    assert "error" in capsys.readouterr().err
