"""An early tools-only job must not occupy the immutable workspace cache key."""

import importlib.util
import re
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("select_ci", ROOT / "scripts/select_ci.py")
assert SPEC and SPEC.loader
SELECTOR = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(SELECTOR)


def _render(value, context):
    """Resolve the context references and boolean choice used by cache inputs."""

    def expression(match):
        body = match[1].strip()
        if " && " in body:
            condition, choices = body.split(" && ")
            variable, expected = condition.split(" == ")
            yes, no = choices.split(" || ")
            return (yes if context[variable] == expected.strip("'") else no).strip("'")
        return context[body]

    return re.sub(r"\$\{\{\s*(.*?)\s*\}\}", expression, str(value))


def _setup_steps(steps, context):
    for step in steps:
        uses = step.get("uses", "")
        if uses.startswith("astral-sh/setup-uv@"):
            yield step.get("with", {}), context
        elif uses.startswith("./.github/actions/"):
            action = yaml.safe_load((ROOT / uses / "action.yml").read_text())
            inputs = {
                name: spec.get("default", "") for name, spec in action.get("inputs", {}).items()
            } | step.get("with", {})
            nested = context | {
                f"inputs.{name}": _render(value, context) for name, value in inputs.items()
            }
            yield from _setup_steps(action["runs"].get("steps", []), nested)


def _pr_consumers(python_selected="true", matrix=None):
    """Follow reusable workflows and composite actions, including nested callers."""
    pending = [".github/workflows/pr.yml"]
    seen = set()
    consumers = {}
    matrix = matrix or SELECTOR.select(set())["groups"][0]
    while pending:
        path = pending.pop()
        if path in seen:
            continue
        seen.add(path)
        workflow = yaml.safe_load((ROOT / path).read_text())
        for job_id, job in workflow["jobs"].items():
            if job.get("uses", "").startswith("./.github/workflows/"):
                pending.append(job["uses"].removeprefix("./"))
            context = {
                "needs.ci-config.outputs.python": python_selected,
                **{f"matrix.{name}": value for name, value in matrix.items()},
                **{
                    f"env.{name}": value
                    for name, value in (workflow.get("env", {}) | job.get("env", {})).items()
                },
            }
            for settings, resolved in _setup_steps(job.get("steps", []), context):
                key = (Path(path).name, job_id)
                assert key not in consumers, f"Review the multiple uv cache lifecycles in {key}"
                if str(settings.get("enable-cache", "auto")).lower() == "false":
                    consumers[key] = None
                else:
                    assert settings.get("cache-suffix"), f"Unscoped uv cache in {key}"
                    consumers[key] = _render(settings["cache-suffix"], resolved)
    return consumers


@pytest.mark.parametrize("python_selected", ["true", "false"])
def test_partial_installs_cannot_win_the_full_workspace_cache(python_selected):
    consumers = _pr_consumers(python_selected)
    workspace_jobs = {
        ("ci.yml", "python-core-tests"),
        ("ci.yml", "python-coverage-workspace"),
        ("ci.yml", "python-coverage-postgres"),
        ("ci.yml", "frontend"),
        ("integration.yml", "contracts"),
        ("integration.yml", "api"),
        ("integration.yml", "operations"),
        ("mutation.yml", "mutation"),
        ("windows-compose-portability.yml", "generate-linux-fixture"),
    }
    if python_selected == "true":
        workspace_jobs.add(("ci.yml", "python-quality"))

    # With matching platform, Python and dependency hashes, setup-uv restores
    # only the exact key and never replaces an existing entry. Model the real
    # failure order: smaller jobs save first, then full workspace jobs complete.
    # https://github.com/astral-sh/setup-uv/blob/5a095e7a2014a4212f075830d4f7277575a9d098/docs/caching.md
    cache = {}
    for job, key in consumers.items():
        if job not in workspace_jobs and key is not None:
            cache.setdefault(key, "partial")
    for job in workspace_jobs:
        key = consumers[job]
        assert key is not None, f"Full workspace cache disabled in {job}"
        assert cache.get(key) != "partial", f"{job} restored an incomplete dependency cache"
        cache.setdefault(key, "workspace")

    # Compatible full installs reuse one cache instead of each rebuilding it.
    assert len({consumers[job] for job in workspace_jobs}) == 1


def test_package_cache_tracks_the_installed_group_and_python():
    job = ("ci.yml", "python-package-tests")
    groups = SELECTOR.select(set())["groups"]
    keys = {_pr_consumers(matrix=group)[job] for group in groups}
    assert len(keys) == len(groups)
    group = groups[0]
    original = _pr_consumers(matrix=group)[job]
    # Group membership can change without changing a pyproject or uv.lock.
    changed_packages = group | {"packages": group["packages"] + " phlo-new-provider"}
    assert _pr_consumers(matrix=changed_packages)[job] != original
    assert _pr_consumers(matrix=group | {"python-version": "3.13"})[job] != original
