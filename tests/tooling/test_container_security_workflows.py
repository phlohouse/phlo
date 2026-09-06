"""Parsed structural contracts for the container-security workflow lanes.

Each workflow is loaded with ``yaml.safe_load`` and checked semantically:
trigger events, referenced scripts/subcommands resolving on disk, and each
lane's registry/reporting posture. Action SHA pinning is enforced globally by
tests/tooling/test_toolchain_pins.py.
"""

from __future__ import annotations

import ast
import re
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
WORKFLOW_ROOT = REPO_ROOT / ".github" / "workflows"
SECURITY_SCRIPT = REPO_ROOT / "scripts" / "container_security.py"

_CONTAINER_SECURITY_INVOCATION = re.compile(
    r"container_security\.py\s*(?:\\\n\s*)?([a-z][a-z0-9-]*)"
)


def _load_workflow(name: str) -> dict[str, Any]:
    return yaml.safe_load((WORKFLOW_ROOT / name).read_text(encoding="utf-8"))


def _triggers(workflow: dict[str, Any]) -> dict[str, Any]:
    return workflow.get("on") or workflow.get(True) or {}


def _steps(workflow: dict[str, Any]) -> Iterator[dict[str, Any]]:
    for job in (workflow.get("jobs") or {}).values():
        if not isinstance(job, dict):
            continue
        for step in job.get("steps") or []:
            if isinstance(step, dict):
                yield step


def _run_scripts(workflow: dict[str, Any]) -> list[str]:
    return [step["run"] for step in _steps(workflow) if isinstance(step.get("run"), str)]


def _script_subcommands() -> set[str]:
    """Collect every argparse subcommand registered by scripts/container_security.py."""
    tree = ast.parse(SECURITY_SCRIPT.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "add_parser"
            and node.args
            and isinstance(node.args[0], ast.Constant)
            and isinstance(node.args[0].value, str)
        ):
            names.add(node.args[0].value)
    return names


def _referenced_subcommands(scripts: list[str]) -> set[str]:
    referenced: set[str] = set()
    for script in scripts:
        referenced.update(_CONTAINER_SECURITY_INVOCATION.findall(script))
    return referenced


def test_container_security_and_rescan_lanes_reference_real_inputs_and_registry() -> None:
    security = _load_workflow("container-security.yml")
    rescan = _load_workflow("container-rescan.yml")

    security_triggers = _triggers(security)
    assert {"workflow_call", "workflow_dispatch", "schedule"} <= set(security_triggers)
    assert "pull_request" not in security_triggers
    assert (REPO_ROOT / "packages").is_dir()
    assert (REPO_ROOT / "security").is_dir()
    for name in (
        "scripts/container_security.py",
        "scripts/generated_image_matrix.py",
        "pyproject.toml",
        "uv.lock",
    ):
        assert (REPO_ROOT / name).is_file()

    rescan_triggers = _triggers(rescan)
    assert {"schedule", "workflow_dispatch"} <= set(rescan_triggers)

    subcommands = _script_subcommands()
    security_commands = _referenced_subcommands(_run_scripts(security))
    rescan_commands = _referenced_subcommands(_run_scripts(rescan))
    assert security_commands <= subcommands
    assert {"validate-waivers", "render-waivers", "affected-images"} <= security_commands
    assert rescan_commands <= subcommands
    assert {
        "validate-waivers",
        "published-fleet",
        "assemble-rescan-manifest",
        "apply-policy",
    } <= rescan_commands

    registries = {
        step["with"]["registry"]
        for step in _steps(rescan)
        if str(step.get("uses", "")).startswith("docker/login-action")
        and "registry" in (step.get("with") or {})
    }
    assert registries == {"ghcr.io"}
    rescan_scripts = "\n".join(_run_scripts(rescan))
    assert "imagetools inspect" in rescan_scripts
    assert '"$image@$digest"' in rescan_scripts


def test_upstream_visibility_is_report_only_with_resolvable_references() -> None:
    workflow = _load_workflow("upstream-image-visibility.yml")
    triggers = _triggers(workflow)

    assert {"pull_request", "workflow_dispatch", "schedule"} <= set(triggers)
    assert {"opened", "reopened", "synchronize", "labeled"} <= set(
        triggers["pull_request"]["types"]
    )

    assert workflow["permissions"] == {"contents": "read"}
    publisher = workflow["jobs"]["publish-candidate-comparison"]
    assert publisher["permissions"] == {"actions": "read", "pull-requests": "write"}

    used = _referenced_subcommands(_run_scripts(workflow))
    assert used <= _script_subcommands()
    assert {
        "write-upstream-candidates",
        "write-upstream-inventory",
        "compare-upstream-candidates",
        "summarize-upstream-reports",
    } <= used
    assert not used & {"apply-policy", "validate-waivers"}

    uploads = [
        (step.get("with") or {}).get("if-no-files-found")
        for step in _steps(workflow)
        if str(step.get("uses", "")).startswith("actions/upload-artifact")
    ]
    assert uploads
    assert set(uploads) == {"error"}
    assert any("GITHUB_STEP_SUMMARY" in script for script in _run_scripts(workflow))
    assert all("continue-on-error" not in step for step in _steps(workflow))


def test_renovate_config_workflow_validates_the_repository_config() -> None:
    workflow = _load_workflow("renovate-config.yml")
    triggers = _triggers(workflow)

    assert {"pull_request", "workflow_dispatch"} <= set(triggers)
    assert "renovate.json" in triggers["pull_request"]["paths"]
    assert workflow["permissions"] == {"contents": "read"}

    config_path = REPO_ROOT / "renovate.json"
    assert config_path.is_file()

    validator_runs = [s for s in _run_scripts(workflow) if "renovate-config-validator" in s]
    assert len(validator_runs) == 1
    assert config_path.name in validator_runs[0]


def test_zizmor_audit_runs_in_the_scheduled_security_lane_not_ci() -> None:
    security = _load_workflow("security.yml")
    ci = _load_workflow("ci.yml")

    assert "schedule" in _triggers(security)
    hardening_steps = [step for step in _steps(security) if "zizmor" in (step.get("name") or "")]
    assert len(hardening_steps) == 1
    assert hardening_steps[0]["run"] == "make zizmor"

    makefile = (REPO_ROOT / "Makefile").read_text(encoding="utf-8")
    assert re.search(r"(?m)^zizmor\s*:", makefile)

    assert all("zizmor" not in script for script in _run_scripts(ci))
