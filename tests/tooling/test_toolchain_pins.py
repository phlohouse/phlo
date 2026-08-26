"""Structural contracts for the repository's pinned validation toolchain.

Workflows and pre-commit configuration are parsed as YAML and checked
structurally: third-party actions must be SHA-pinned, toolchain steps must
carry exact version pins, and every project uv invocation must run in
locked mode.
"""

import re
import tomllib
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
WORKFLOW_ROOT = REPO_ROOT / ".github" / "workflows"
EXPECTED_UV_VERSION = "0.12.1"
EXPECTED_RELX_VERSION = "v1.5.0"
EXPECTED_RELX_REVISION = "5dc88dc73d728dd1560444baadf66d6115c1bec2"

_SHA_REF = re.compile(r"@[0-9a-f]{40}$")

# uv invocations that opt out of the project environment are allowed to skip
# the lockfile; everything else operating on the project must be locked.
_NO_PROJECT_MARKERS = ("--no-project",)


def _workflow_paths() -> list[Path]:
    return sorted(WORKFLOW_ROOT.glob("*.yml"))


def _parse_yaml(path: Path) -> Any:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _iter_steps(value: Any) -> Iterator[dict[str, Any]]:
    """Yield every job/composite-action step mapping in a parsed document."""
    if isinstance(value, dict):
        steps = value.get("steps")
        if isinstance(steps, list):
            for step in steps:
                if isinstance(step, dict):
                    yield step
        for child in value.values():
            yield from _iter_steps(child)
    elif isinstance(value, list):
        for item in value:
            yield from _iter_steps(item)


def _step_run_script(step: dict[str, Any]) -> str:
    run = step.get("run")
    return run if isinstance(run, str) else ""


def _project_uv_lines(script: str) -> list[str]:
    return [
        line
        for line in script.splitlines()
        if re.search(r"\buv (run|sync|audit)\b", line)
        and not any(marker in line for marker in _NO_PROJECT_MARKERS)
    ]


def test_workflow_actions_are_sha_pinned() -> None:
    """Third-party actions must be referenced by an immutable commit SHA."""
    for path in _workflow_paths():
        for step in _iter_steps(_parse_yaml(path)):
            uses = step.get("uses")
            if not isinstance(uses, str) or uses.startswith(("./", "docker://")):
                continue
            assert _SHA_REF.search(uses), f"{path.name}: {uses!r} is not SHA-pinned"


def test_setup_uv_steps_pin_the_repository_uv_version() -> None:
    """Every setup-uv step must request exactly the repository uv version."""
    for path in _workflow_paths():
        for step in _iter_steps(_parse_yaml(path)):
            uses = step.get("uses")
            if not (isinstance(uses, str) and uses.startswith("astral-sh/setup-uv@")):
                continue
            with_block = step.get("with")
            version = with_block.get("version") if isinstance(with_block, dict) else None
            assert version == EXPECTED_UV_VERSION, (
                f"{path.name}: setup-uv is not pinned to {EXPECTED_UV_VERSION}"
            )


def test_release_workflow_pins_releasex_immutably() -> None:
    """Release steps must use the pinned ReleaseX build at its fixed version."""
    doc = _parse_yaml(WORKFLOW_ROOT / "release.yml")
    releasex_steps = [
        step
        for step in _iter_steps(doc)
        if isinstance(step.get("uses"), str) and step["uses"].startswith("iamgp/ReleaseX@")
    ]
    assert releasex_steps, "release.yml must invoke ReleaseX"
    for step in releasex_steps:
        assert step["uses"] == f"iamgp/ReleaseX@{EXPECTED_RELX_REVISION}"
        assert (step.get("with") or {}).get("version") == EXPECTED_RELX_VERSION


def test_workflow_uv_commands_run_in_locked_mode() -> None:
    """Project uv invocations inside workflows must be locked."""
    for path in _workflow_paths():
        for step in _iter_steps(_parse_yaml(path)):
            script = _step_run_script(step)
            for line in _project_uv_lines(script):
                if 'uv run "${run_args[@]}"' in line:
                    assert "run_args=(--locked" in script, (
                        f"{path.name}: run_args indirection without a locked default: {line}"
                    )
                else:
                    assert "--locked" in line, f"{path.name}: unlocked uv command: {line}"


def test_pre_commit_hooks_use_the_locked_project_toolchain() -> None:
    """Pre-commit must drive tools through the locked project environment."""
    config = _parse_yaml(REPO_ROOT / ".pre-commit-config.yaml")
    repos = config.get("repos") or []
    remote_tool_repos = [
        repo for repo in repos if "astral-sh/ruff-pre-commit" in str(repo.get("repo", ""))
    ]
    assert not remote_tool_repos, "ruff must come from the project environment"

    for repo in repos:
        for hook in repo.get("hooks") or []:
            entry = str(hook.get("entry", ""))
            if entry.startswith("uv run"):
                assert "--locked" in entry, f"unlocked pre-commit entry: {entry}"


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
    assert len(replacements) == 7

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
