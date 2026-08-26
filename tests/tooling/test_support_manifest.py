"""Tests for the machine-readable v1 support boundary.

The checked-in registry/support/v1.json manifest must validate against
the repository inventory, stay byte-identical to the packaged copy, and
enforce its rules: release-set metadata derived from package sources,
v1-only schema compatibility, maturity-gated promotions, and no
deletions of required capabilities still referenced elsewhere.
"""

from __future__ import annotations

import importlib.util
import json
import shutil
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[2]
MANIFEST_PATH = ROOT / "registry/support/v1.json"
PACKAGED_MANIFEST_PATH = ROOT / "src/phlo/support_data/v1.json"
FIXTURE_DIR = ROOT / "tests/fixtures/support_manifest"
VALIDATOR_PATH = ROOT / "scripts/validate_support_manifest.py"
SPEC = importlib.util.spec_from_file_location("support_manifest_validator", VALIDATOR_PATH)
assert SPEC and SPEC.loader
VALIDATOR = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(VALIDATOR)
validate_manifest = VALIDATOR.validate_manifest
service_inventory_at = VALIDATOR._service_inventory_at


def _manifest() -> dict[str, object]:
    return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))


def _package(manifest: dict[str, object], name: str) -> dict[str, object]:
    packages = manifest["packages"]
    assert isinstance(packages, list)
    entry = next(package for package in packages if package["name"] == name)
    assert isinstance(entry, dict)
    return entry


# Build a disposable mirror of the repository layout so the validator can be
# run against mutated manifests: symlinks keep inventory and classifier reads
# pointed at the real workspace, while docs/ is copied so evidence checks
# never touch the live tree.
def _linked_repo(tmp_path: Path) -> Path:
    for name in (".github", "packages", "registry", "src"):
        (tmp_path / name).symlink_to(ROOT / name, target_is_directory=True)
    shutil.copytree(ROOT / "docs", tmp_path / "docs")
    shutil.copy2(ROOT / "README.md", tmp_path / "README.md")
    (tmp_path / "pyproject.toml").symlink_to(ROOT / "pyproject.toml")
    return tmp_path


def test_checked_in_manifest_matches_schema_and_repository_inventory() -> None:
    assert validate_manifest(_manifest()) == []


def test_packaged_manifest_exactly_matches_the_registry_manifest() -> None:
    assert PACKAGED_MANIFEST_PATH.read_bytes() == MANIFEST_PATH.read_bytes()


def test_release_set_package_versions_and_service_images_are_derived_from_metadata() -> None:
    manifest = _manifest()
    manifest["release_set"]["packages"][0]["version"] = "0.0.0"
    manifest["release_set"]["services"][0]["image_reference"] = "example:latest"

    errors = validate_manifest(manifest)

    assert any("release_set package 'phlo' version" in error for error in errors)
    assert any("release_set service 'dagster' image_reference" in error for error in errors)


def test_release_set_rejects_duplicate_package_and_service_names() -> None:
    manifest = _manifest()
    manifest["release_set"]["packages"].append(manifest["release_set"]["packages"][0])
    manifest["release_set"]["services"].append(manifest["release_set"]["services"][0])

    errors = validate_manifest(manifest)

    assert "release_set.packages contains duplicate package names" in errors
    assert "release_set.services contains duplicate service names" in errors


def test_release_set_configuration_schema_version_matches_source() -> None:
    manifest = _manifest()
    manifest["release_set"]["schemas"]["configuration"]["version"] = "2"

    errors = validate_manifest(manifest)

    assert "release_set configuration version does not match CONFIG_SCHEMA_VERSION" in errors


def test_schema_version_is_compatible_only_at_v1() -> None:
    manifest = _manifest()
    manifest["schema_version"] = "2.0"

    errors = validate_manifest(manifest)

    assert any("schema_version" in error for error in errors)


def test_preview_package_cannot_be_promoted_without_matching_maturity() -> None:
    manifest = _manifest()
    override = json.loads((FIXTURE_DIR / "preview-as-supported.json").read_text(encoding="utf-8"))
    _package(manifest, override["name"]).update(override)

    errors = validate_manifest(manifest)

    assert any("current_maturity contradicts target_status" in error for error in errors)


def test_unknown_scope_classification_fails_schema_validation() -> None:
    manifest = _manifest()
    override = json.loads((FIXTURE_DIR / "unknown-scope.json").read_text(encoding="utf-8"))
    _package(manifest, override["name"]).update(override)

    errors = validate_manifest(manifest)

    assert any("unknown value 'not-a-real-scope'" in error for error in errors)


def test_unsupported_package_addition_fails_inventory_validation() -> None:
    manifest = _manifest()
    addition = json.loads((FIXTURE_DIR / "unsupported-addition.json").read_text(encoding="utf-8"))
    manifest["packages"] = [*manifest["packages"], addition]

    errors = validate_manifest(manifest)

    assert any("absent from the workspace" in error for error in errors)


def test_required_capability_cannot_be_deleted_with_references() -> None:
    manifest = _manifest()
    manifest["capabilities"] = [
        item for item in manifest["capabilities"] if item["name"] != "mandatory_authorization"
    ]
    for profile in manifest["profiles"].values():
        profile["components"] = [
            component
            for component in profile["components"]
            if component != "capability:mandatory_authorization"
        ]
    manifest["gates"]["components"] = [
        item
        for item in manifest["gates"]["components"]
        if item["name"] != "capability:mandatory_authorization"
    ]

    errors = validate_manifest(manifest)

    assert any("mandatory_authorization" in error and "absent" in error for error in errors)


def test_implemented_blessed_capability_cannot_be_deleted_with_references() -> None:
    manifest = _manifest()
    manifest["capabilities"] = [
        item for item in manifest["capabilities"] if item["name"] != "dlt_ingestion"
    ]
    for profile in manifest["profiles"].values():
        profile["components"] = [
            component
            for component in profile["components"]
            if component != "capability:dlt_ingestion"
        ]
    manifest["gates"]["components"] = [
        item
        for item in manifest["gates"]["components"]
        if item["name"] != "capability:dlt_ingestion"
    ]

    errors = validate_manifest(manifest)

    assert any("dlt_ingestion" in error and "absent" in error for error in errors)


def test_unapproved_capability_addition_is_rejected() -> None:
    manifest = _manifest()
    addition = dict(manifest["capabilities"][0])
    addition["name"] = "arbitrary_production_capability"
    manifest["capabilities"].append(addition)
    manifest["profiles"]["blessed_core"]["components"].append(
        "capability:arbitrary_production_capability"
    )

    errors = validate_manifest(manifest)

    assert any("not in the approved v1 capability set" in error for error in errors)


def test_required_blessed_core_profile_cannot_be_deleted() -> None:
    manifest = _manifest()
    del manifest["profiles"]["blessed_core"]

    errors = validate_manifest(manifest)

    assert any("approved profile 'blessed_core' is absent" in error for error in errors)


@pytest.mark.parametrize("profile_name", ["supported_optional", "preview", "development_only"])
def test_approved_profile_cannot_be_deleted(profile_name: str) -> None:
    manifest = _manifest()
    del manifest["profiles"][profile_name]

    errors = validate_manifest(manifest)

    assert any(f"approved profile {profile_name!r} is absent" in error for error in errors)


def test_unapproved_production_ready_profile_is_rejected() -> None:
    manifest = _manifest()
    manifest["profiles"]["production_ready"] = {
        "status": "supported",
        "components": ["package:phlo-rustfs"],
        "reason": "An invented production profile must not expand the frozen scope.",
    }

    errors = validate_manifest(manifest)

    assert any(
        "profile 'production_ready' is not in the approved profile set" in error for error in errors
    )


def test_profile_status_must_match_its_classification() -> None:
    manifest = _manifest()
    manifest["profiles"]["preview"]["status"] = "supported"

    errors = validate_manifest(manifest)

    assert any(
        "profile 'preview' status must be 'available_without_v1_guarantee'" in error
        for error in errors
    )


def test_preview_component_cannot_join_supported_optional_profile() -> None:
    manifest = _manifest()
    manifest["profiles"]["supported_optional"]["components"].append("package:phlo-rustfs")

    errors = validate_manifest(manifest)

    assert any(
        "profile 'supported_optional' membership does not match" in error for error in errors
    )


def _write_service_package(tmp_path: Path, entry_points: str = "") -> Path:
    package_root = tmp_path / "packages" / "phlo-example"
    package_root.mkdir(parents=True)
    (package_root / "pyproject.toml").write_text(
        f'[project]\nname = "phlo-example"\nversion = "0.1.0"\n{entry_points}',
        encoding="utf-8",
    )
    return package_root


def test_service_inventory_recursively_discovers_nested_companion(tmp_path: Path) -> None:
    package_root = _write_service_package(
        tmp_path,
        '[project.entry-points."phlo.plugins.services"]\nexample = "phlo_example.plugin:Plugin"\n',
    )
    primary_path = package_root / "src" / "phlo_example" / "service.yaml"
    primary_path.parent.mkdir(parents=True)
    primary_path.write_text("name: example\n", encoding="utf-8")
    service_path = package_root / "src" / "phlo_example" / "nested" / "worker-daemon.yaml"
    service_path.parent.mkdir(parents=True)
    service_path.write_text("name: nested-worker\n", encoding="utf-8")

    assert service_inventory_at(tmp_path) == {
        "example": primary_path,
        "nested-worker": service_path,
    }


def test_service_inventory_ignores_companion_without_plugin(tmp_path: Path) -> None:
    package_root = _write_service_package(tmp_path)
    service_path = package_root / "src" / "phlo_example" / "nested" / "worker-daemon.yaml"
    service_path.parent.mkdir(parents=True)
    service_path.write_text("name: nested-worker\n", encoding="utf-8")

    assert service_inventory_at(tmp_path) == {}


def test_service_inventory_ignores_nested_service_yaml(tmp_path: Path) -> None:
    package_root = _write_service_package(
        tmp_path,
        '[project.entry-points."phlo.plugins.services"]\nexample = "phlo_example.plugin:Plugin"\n',
    )
    primary_path = package_root / "src" / "phlo_example" / "service.yaml"
    primary_path.parent.mkdir(parents=True)
    primary_path.write_text("name: example\n", encoding="utf-8")
    nested_path = package_root / "src" / "phlo_example" / "nested" / "service.yaml"
    nested_path.parent.mkdir(parents=True)
    nested_path.write_text("name: nested-service\n", encoding="utf-8")

    assert service_inventory_at(tmp_path) == {"example": primary_path}


def test_service_inventory_ignores_unrelated_named_yaml(tmp_path: Path) -> None:
    package_root = _write_service_package(
        tmp_path,
        '[project.entry-points."phlo.plugins.services"]\nexample = "phlo_example.plugin:Plugin"\n',
    )
    primary_path = package_root / "src" / "phlo_example" / "service.yaml"
    primary_path.parent.mkdir(parents=True)
    primary_path.write_text("name: example\n", encoding="utf-8")
    yaml_path = package_root / "src" / "phlo_example" / "nested" / "values.yaml"
    yaml_path.parent.mkdir(parents=True)
    yaml_path.write_text("name: unrelated\n", encoding="utf-8")

    assert service_inventory_at(tmp_path) == {"example": primary_path}


def test_service_inventory_rejects_registered_custom_primary(tmp_path: Path) -> None:
    package_root = _write_service_package(
        tmp_path,
        '[project.entry-points."phlo.plugins.services"]\ncustom = "phlo_example.plugin:Plugin"\n',
    )
    service_path = package_root / "src" / "phlo_example" / "custom-definition.yaml"
    service_path.parent.mkdir(parents=True)
    service_path.write_text("name: custom\n", encoding="utf-8")

    with pytest.raises(VALIDATOR.ValidationError, match="no runtime-recognized YAML definition"):
        service_inventory_at(tmp_path)


def test_temp_repo_rejects_missing_evidence_path(tmp_path: Path) -> None:
    manifest = _manifest()
    _package(manifest, "phlo")["evidence"] = ["docs/does-not-exist.md"]

    errors = validate_manifest(manifest, repo_root=_linked_repo(tmp_path))

    assert any("evidence path does not exist" in error for error in errors)


def test_temp_repo_rejects_missing_evidence_anchor(tmp_path: Path) -> None:
    manifest = _manifest()
    _package(manifest, "phlo")["evidence"] = ["README.md#not-a-heading"]

    errors = validate_manifest(manifest, repo_root=_linked_repo(tmp_path))

    assert any("evidence anchor does not exist" in error for error in errors)


def test_markdown_anchor_requires_exact_heading_slug() -> None:
    manifest = _manifest()
    _package(manifest, "phlo")["evidence"] = ["README.md#not-an-existing-heading"]

    errors = validate_manifest(manifest)

    assert any("evidence anchor does not exist" in error for error in errors)


def test_evidence_path_cannot_be_empty() -> None:
    manifest = _manifest()
    _package(manifest, "phlo")["evidence"] = ["#made-up"]

    errors = validate_manifest(manifest)

    assert any("evidence path must not be empty" in error for error in errors)


def test_non_markdown_evidence_fragment_is_rejected() -> None:
    manifest = _manifest()
    _package(manifest, "phlo")["evidence"] = ["pyproject.toml#made-up"]

    errors = validate_manifest(manifest)

    assert any("evidence fragments are unsupported" in error for error in errors)


def test_temp_repo_rejects_evidence_traversal_to_existing_outside_file(tmp_path: Path) -> None:
    outside = tmp_path.parent / "support-manifest-outside.md"
    outside.write_text("# Outside\n", encoding="utf-8")
    manifest = _manifest()
    _package(manifest, "phlo")["evidence"] = ["../support-manifest-outside.md"]

    errors = validate_manifest(manifest, repo_root=_linked_repo(tmp_path))

    assert any("evidence path" in error and "must not contain '..'" in error for error in errors)


def test_temp_repo_rejects_absolute_evidence_path(tmp_path: Path) -> None:
    outside = tmp_path.parent / "support-manifest-absolute.md"
    outside.write_text("# Outside\n", encoding="utf-8")
    manifest = _manifest()
    _package(manifest, "phlo")["evidence"] = [str(outside)]

    errors = validate_manifest(manifest, repo_root=_linked_repo(tmp_path))

    assert any("evidence path" in error and "must be relative" in error for error in errors)


def test_temp_repo_rejects_evidence_symlink_escape(tmp_path: Path) -> None:
    outside = tmp_path.parent / "support-manifest-symlink.md"
    outside.write_text("# Outside\n", encoding="utf-8")
    (tmp_path / "escaped.md").symlink_to(outside)
    manifest = _manifest()
    _package(manifest, "phlo")["evidence"] = ["escaped.md"]

    errors = validate_manifest(manifest, repo_root=_linked_repo(tmp_path))

    assert any("evidence path" in error and "outside repo_root" in error for error in errors)


def test_temp_repo_rejects_service_source_traversal(tmp_path: Path) -> None:
    outside = tmp_path.parent / "support-manifest-service.yaml"
    outside.write_text("name: escaped-service\n", encoding="utf-8")
    manifest = _manifest()
    service = next(item for item in manifest["services"] if item["name"] == "dagster")
    service["source"] = "../support-manifest-service.yaml"

    errors = validate_manifest(manifest, repo_root=_linked_repo(tmp_path))

    assert any(
        "service 'dagster': source path" in error and "must not contain '..'" in error
        for error in errors
    )


def test_required_capability_cannot_be_marked_planned() -> None:
    manifest = _manifest()
    capability = next(item for item in manifest["capabilities"] if item["name"] == "golden_path_ci")
    capability["target_status"] = "planned"

    errors = validate_manifest(manifest)

    assert any(
        "required v1 capability must use target_status=required" in error for error in errors
    )


def test_gate_status_is_derived_from_applicability_and_checked_evidence() -> None:
    manifest = _manifest()
    component = manifest["gates"]["components"][0]
    component["applicable_gates"] = []
    component["blocked_by"] = []
    component["status"] = "passed"

    errors = validate_manifest(manifest)

    assert any("has no applicable gates" in error for error in errors)
    assert any("passed without checked evidence" in error for error in errors)


def test_passed_gate_rejects_missing_evidence_path(tmp_path: Path) -> None:
    manifest = _manifest()
    component = manifest["gates"]["components"][0]
    component["applicable_gates"] = []
    component["blocked_by"] = []
    component["status"] = "passed"
    component["evidence"] = ["docs/does-not-exist.md"]

    errors = validate_manifest(manifest, repo_root=_linked_repo(tmp_path))

    assert any(
        "gate component" in error and "evidence path does not exist" in error for error in errors
    )


def test_passed_gate_rejects_stale_evidence_anchor(tmp_path: Path) -> None:
    manifest = _manifest()
    component = manifest["gates"]["components"][0]
    component["applicable_gates"] = []
    component["blocked_by"] = []
    component["status"] = "passed"
    component["evidence"] = ["README.md#not-a-heading"]

    errors = validate_manifest(manifest, repo_root=_linked_repo(tmp_path))

    assert any(
        "gate component" in error and "evidence anchor does not exist" in error for error in errors
    )


def test_passed_gate_rejects_arbitrary_evidence_string() -> None:
    manifest = _manifest()
    component = manifest["gates"]["components"][0]
    component["evidence"] = "evidence claimed by release tooling"

    errors = validate_manifest(manifest)

    assert any("gates.components[0].evidence: expected array" in error for error in errors)


def test_unknown_gate_returns_validation_error_instead_of_raising() -> None:
    manifest = _manifest()
    component = manifest["gates"]["components"][0]
    component["applicable_gates"] = ["not-a-gate"]
    component["blocked_by"] = []

    errors = validate_manifest(manifest)

    assert any("unknown applicable gates" in error for error in errors)


def test_service_package_must_own_discovered_source() -> None:
    manifest = _manifest()
    service = next(item for item in manifest["services"] if item["name"] == "dagster")
    service["package"] = "phlo-api"

    errors = validate_manifest(manifest)

    assert any("dagster" in error and "does not own discovered source" in error for error in errors)


def test_runtime_claim_sets_must_be_disjoint() -> None:
    manifest = _manifest()
    manifest["runtime"]["python"]["supported"].append("3.13")

    errors = validate_manifest(manifest)

    assert any("must be disjoint" in error for error in errors)


def test_runtime_python_claims_must_match_advertised_classifiers() -> None:
    manifest = _manifest()
    manifest["runtime"]["python"]["advertised_unverified"] = ["3.13"]

    errors = validate_manifest(manifest)

    assert any("must exactly match pyproject classifiers" in error for error in errors)


def test_runtime_supported_python_requires_ci_evidence() -> None:
    manifest = _manifest()
    manifest["runtime"]["python"]["supported"] = ["3.13"]
    manifest["runtime"]["python"]["advertised_unverified"] = []

    errors = validate_manifest(manifest)

    assert any("3.13" in error and "no CI matrix evidence" in error for error in errors)


# ---------------------------------------------------------------------------
# Named-claim reconciliation (issue #628)
# ---------------------------------------------------------------------------

_STALE_PHRASES = [
    "does not expose an authoritative durable per-run report",
    "durable per-run reports remain a planned",
    "required CI does not invoke",
    "Upgrade and recovery are documentation procedures",
    "Automated upgrade and restore drills are deferred",
    "scripts/run_golden_path.py",
]


def _capability(manifest: dict[str, object], name: str) -> dict[str, object]:
    capabilities = manifest["capabilities"]
    assert isinstance(capabilities, list)
    entry = next(item for item in capabilities if item["name"] == name)
    assert isinstance(entry, dict)
    return entry


def test_observatory_run_report_evidence_binds_to_committed_implementation() -> None:
    """The run-report capability must cite the actual API, UI route, and test."""
    manifest = _manifest()
    capability = _capability(manifest, "observatory_run_report")
    evidence = set(capability["evidence"])

    assert "packages/phlo-api/src/phlo_api/observatory_api/run_report.py" in evidence
    assert (
        "packages/phlo-observatory/src/phlo_observatory/src/routes/"
        "runs.$projectId.$runId.attempts.$attempt.report.tsx"
    ) in evidence
    assert "packages/phlo-api/tests/test_observatory_api.py" in evidence
    for path in evidence:
        assert (ROOT / path.split("#", 1)[0]).exists(), f"missing evidence: {path}"
    assert "does not expose" not in capability["reason"]


def test_observatory_package_reason_acknowledges_run_report() -> None:
    """The phlo-observatory package entry must not claim run reports are absent."""
    manifest = _manifest()
    entry = _package(manifest, "phlo-observatory")
    assert "does not expose" not in entry["reason"]


def test_upgrade_restore_evidence_binds_to_recovery_drill_and_ci() -> None:
    """The upgrade_restore capability must cite the drill script, its tests, and CI."""
    manifest = _manifest()
    capability = _capability(manifest, "upgrade_restore")
    evidence = set(capability["evidence"])

    assert "scripts/recovery_drill.py" in evidence
    assert "tests/scripts/test_recovery_drill.py" in evidence
    assert ".github/workflows/ci.yml" in evidence
    for path in evidence:
        assert (ROOT / path.split("#", 1)[0]).exists(), f"missing evidence: {path}"
    ci_text = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    assert "scripts/recovery_drill.py" in ci_text
    assert "documentation procedures" not in capability["reason"]


def test_golden_path_ci_evidence_binds_to_release_golden_path_and_ci() -> None:
    """The golden_path_ci capability must cite release_golden_path.py and CI, not run_golden_path.py."""
    manifest = _manifest()
    capability = _capability(manifest, "golden_path_ci")
    evidence = set(capability["evidence"])

    assert "scripts/release_golden_path.py" in evidence
    assert "tests/scripts/test_release_golden_path.py" in evidence
    assert ".github/workflows/ci.yml" in evidence
    assert "scripts/run_golden_path.py" not in evidence
    for path in evidence:
        assert (ROOT / path.split("#", 1)[0]).exists(), f"missing evidence: {path}"
    ci_text = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    assert "test_release_golden_path" in ci_text
    assert "does not invoke" not in capability["reason"]


def test_named_claim_validator_catches_stale_run_report_reason() -> None:
    manifest = _manifest()
    _capability(manifest, "observatory_run_report")["reason"] = (
        "The Observatory surface does not expose an authoritative durable per-run report API."
    )

    errors = validate_manifest(manifest)

    assert any("stale phrase" in error and "observatory_run_report" in error for error in errors)


def test_named_claim_validator_catches_stale_upgrade_restore_reason() -> None:
    manifest = _manifest()
    _capability(manifest, "upgrade_restore")["reason"] = (
        "Upgrade and recovery are documentation procedures until tested."
    )

    errors = validate_manifest(manifest)

    assert any("stale phrase" in error and "upgrade_restore" in error for error in errors)


def test_named_claim_validator_catches_stale_golden_path_reason() -> None:
    manifest = _manifest()
    _capability(manifest, "golden_path_ci")["reason"] = (
        "The real golden-path script exists but required CI does not invoke it."
    )

    errors = validate_manifest(manifest)

    assert any("stale phrase" in error and "golden_path_ci" in error for error in errors)


def test_named_claim_validator_catches_missing_required_evidence() -> None:
    manifest = _manifest()
    _capability(manifest, "golden_path_ci")["evidence"] = [
        "scripts/run_golden_path.py",
        ".github/workflows/ci.yml",
    ]

    errors = validate_manifest(manifest)

    assert any(
        "evidence must include" in error and "release_golden_path.py" in error for error in errors
    )
    assert any(
        "evidence must not include" in error and "run_golden_path.py" in error for error in errors
    )


def test_named_claim_validator_catches_workflow_command_regression(tmp_path: Path) -> None:
    """If CI stops invoking the recovery drill, the validator must fail."""
    manifest = _manifest()
    capabilities = manifest["capabilities"]
    workflows_dir = tmp_path / ".github" / "workflows"
    workflows_dir.mkdir(parents=True)
    ci_text = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    (workflows_dir / "ci.yml").write_text(
        ci_text.replace("scripts/recovery_drill.py", "scripts/removed.py"),
        encoding="utf-8",
    )

    errors = VALIDATOR._validate_named_claim_bindings(capabilities, repo_root=tmp_path)

    assert any("does not invoke" in error and "recovery_drill.py" in error for error in errors)


def test_no_stale_support_phrases_in_manifest_or_docs() -> None:
    """The stale phrases identified in issue #628 must not regress."""
    check_paths = [
        ROOT / "registry" / "support" / "v1.json",
        ROOT / "packages" / "phlo-observatory" / "README.md",
    ]
    # Dated point-in-time records (plans, handoffs, proposals, ADRs) preserve
    # history; old paths and superseded claims there are records, not current
    # support claims. Living operator docs stay scanned.
    for doc_file in (ROOT / "docs").rglob("*.md"):
        relative = doc_file.relative_to(ROOT)
        if relative.parts[:2] in (
            ("docs", "plans"),
            ("docs", "handoffs"),
            ("docs", "architecture"),
        ):
            continue
        head = doc_file.read_text(encoding="utf-8", errors="replace")[:512]
        if any(line.startswith("Date:") for line in head.splitlines()):
            continue
        check_paths.append(doc_file)
    for path in check_paths:
        text = path.read_text(encoding="utf-8")
        for phrase in _STALE_PHRASES:
            assert phrase not in text, f"stale phrase {phrase!r} found in {path.relative_to(ROOT)}"


def test_production_ready_remains_false_and_no_gate_passed() -> None:
    """Alpha maturity and fail-closed gates must be preserved."""
    manifest = _manifest()
    assert manifest["current_release"]["production_ready"] is False
    assert manifest["current_release"]["maturity"] == "alpha"
    assert not any(v == "passed" for v in manifest["gates"]["status"].values())
