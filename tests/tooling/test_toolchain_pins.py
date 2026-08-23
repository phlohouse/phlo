"""Contracts for the repository's pinned validation toolchain.

Every setup-uv step must match the repo pin, release.yml must reference the
immutable ReleaseX revision exactly twice, and relx.toml must derive workspace
versions transactionally.
"""

import re
import tomllib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
WORKFLOW_ROOT = REPO_ROOT / ".github" / "workflows"
EXPECTED_UV_VERSION = "0.12.1"
EXPECTED_RELX_VERSION = "v1.5.0"
EXPECTED_RELX_REVISION = "5dc88dc73d728dd1560444baadf66d6115c1bec2"


def _workflow_texts() -> list[str]:
    return [path.read_text(encoding="utf-8") for path in sorted(WORKFLOW_ROOT.glob("*.yml"))]


def test_every_setup_uv_step_uses_the_repository_pin() -> None:
    for workflow in _workflow_texts():
        lines = workflow.splitlines()
        for index, line in enumerate(lines):
            if "uses: astral-sh/setup-uv@" not in line:
                continue
            setup_block = lines[index : index + 6]
            assert any(
                re.fullmatch(rf'\s+version: "{EXPECTED_UV_VERSION}"', block_line)
                for block_line in setup_block
            ), f"setup-uv step is not pinned to {EXPECTED_UV_VERSION}: {line}"
            assert not any('version: "latest"' in block_line for block_line in setup_block)


def test_release_workflow_uses_the_immutable_releasex_pin() -> None:
    release = (WORKFLOW_ROOT / "release.yml").read_text(encoding="utf-8")

    assert release.count(f"uses: iamgp/ReleaseX@{EXPECTED_RELX_REVISION}") == 2
    assert release.count(f"version: {EXPECTED_RELX_VERSION}") == 2
    assert "iamgp/ReleaseX@v" not in release


def test_releasex_prepares_derived_workspace_versions_transactionally() -> None:
    with (REPO_ROOT / "relx.toml").open("rb") as handle:
        config = tomllib.load(handle)

    dependencies = config["workspace"]["dependencies"]
    assert dependencies["enabled"] is True
    assert dependencies["rules"] == [
        {
            "dependency": "phlo",
            "dependents": ["packages/*"],
            "when": "dependency_selected",
            "range": ">={version},<{next_minor}",
        }
    ]

    replacements = config["release"]["replacements"]
    assert len(replacements) == 11

    package_versions = {}
    manifests = [REPO_ROOT / "pyproject.toml", *REPO_ROOT.glob("packages/*/pyproject.toml")]
    for manifest in manifests:
        with manifest.open("rb") as handle:
            project = tomllib.load(handle)["project"]
        package_versions[project["name"]] = project["version"]

    for replacement in replacements:
        for package in replacement["packages"]:
            search = replacement["search"].format(
                name=package,
                current_version=package_versions[package],
            )
            for relative_path in replacement["files"]:
                content = (REPO_ROOT / relative_path).read_text(encoding="utf-8")
                assert content.count(search) == replacement["expected_matches"], (
                    package,
                    relative_path,
                    search,
                )


def test_makefile_project_commands_require_the_lockfile() -> None:
    makefile = (REPO_ROOT / "Makefile").read_text(encoding="utf-8")
    uv_run_lines = [line for line in makefile.splitlines() if "uv run" in line]

    assert uv_run_lines
    assert all("uv run --locked" in line for line in uv_run_lines), uv_run_lines
    assert "uv sync --locked" in makefile


def test_pre_commit_uses_the_locked_project_ruff_and_tools() -> None:
    pre_commit = (REPO_ROOT / ".pre-commit-config.yaml").read_text(encoding="utf-8")
    lockfile = (REPO_ROOT / "uv.lock").read_text(encoding="utf-8")
    ruff_version = re.search(
        r'\[\[package\]\]\nname = "ruff"\nversion = "([^"]+)"',
        lockfile,
    )

    assert ruff_version is not None
    assert ruff_version.group(1) == "0.15.9"
    assert "https://github.com/astral-sh/ruff-pre-commit" not in pre_commit
    assert "entry: uv run --locked ruff check --fix" in pre_commit
    assert "entry: uv run --locked ruff format" in pre_commit

    for line in pre_commit.splitlines():
        if "entry: uv run" in line:
            assert "--locked" in line, line


def test_workflow_project_validation_commands_require_the_lockfile() -> None:
    for workflow_path in sorted(WORKFLOW_ROOT.glob("*.yml")):
        lines = workflow_path.read_text(encoding="utf-8").splitlines()
        for index, line in enumerate(lines):
            if "uv sync" in line and "--no-project" not in line:
                assert "--locked" in line, f"{workflow_path}: {line}"
            if "uv --project" in line and " run" in line and "--no-project" not in line:
                assert "--locked" in line, f"{workflow_path}: {line}"
            if "uv run" not in line:
                continue
            if "--no-project" in line:
                continue
            if 'uv run "${run_args[@]}"' in line:
                assert any(
                    "run_args=(--locked" in prior for prior in lines[max(0, index - 20) : index]
                )
            else:
                assert "--locked" in line, f"{workflow_path}: {line}"


def test_security_audit_uses_the_stable_pinned_uv_command() -> None:
    security = (WORKFLOW_ROOT / "security.yml").read_text(encoding="utf-8")

    assert "run: uv audit --locked" in security
    assert "--preview-features audit-command" not in security
