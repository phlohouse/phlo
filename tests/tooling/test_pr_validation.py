"""Pre-merge gates must cover every lane and cannot accept skipped work."""

import importlib.util
import json
import os
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("ci_shard", ROOT / "scripts/ci_shard.py")
assert SPEC and SPEC.loader
SHARD = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(SHARD)
shard_for = SHARD.shard_for


def workflow(name):
    return yaml.safe_load((ROOT / ".github/workflows" / name).read_text())


def test_one_pr_orchestrator_and_no_candidate_duplication() -> None:
    pr = workflow("pr.yml")
    assert set(pr.get("on") or pr[True]) == {"pull_request", "merge_group"}
    assert pr["jobs"]["required"]["needs"] == ["ci", "integration", "containers", "security"]
    for name in (
        "ci.yml",
        "integration.yml",
        "security.yml",
        "container-security.yml",
        "release-candidate.yml",
    ):
        definition = workflow(name)
        assert not {"pull_request", "merge_group"} & set(definition.get("on") or definition[True])
    assert not (ROOT / ".github/workflows/dependency-validation.yml").exists()


@pytest.mark.parametrize("bad_result", ["failure", "cancelled", "skipped", "", "success"])
def test_required_gate_executes_fail_closed(bad_result) -> None:
    step = workflow("pr.yml")["jobs"]["required"]["steps"][0]
    for lane in step["env"]:
        env = dict(os.environ, **dict.fromkeys(step["env"], "success"))
        env[lane] = bad_result
        result = subprocess.run(["bash", "-c", step["run"]], env=env, capture_output=True)
        assert (result.returncode == 0) == (bad_result == "success")


def test_shards_are_disjoint_complete_and_keep_module_fixtures_together() -> None:
    nodes = {f"tests/test_{i}.py::test_case[{j}]" for i in range(100) for j in range(3)}
    shards = [{node for node in nodes if shard_for(node, 3) == index} for index in range(3)]
    assert all(shards)
    assert set.union(*shards) == nodes
    assert sum(map(len, shards)) == len(nodes)
    for i in range(100):
        assert len({shard_for(f"tests/test_{i}.py::test_case[{j}]", 3) for j in range(3)}) == 1


def test_both_python_versions_run_before_merge() -> None:
    ci = workflow("ci.yml")["jobs"]
    for job in ("python-core-tests", "python-package-tests"):
        assert ci[job]["strategy"]["matrix"]["python-version"] == ["3.11", "3.12"]
    assert ci["python-core-tests"]["env"]["UV_PYTHON"] == "${{ matrix.python-version }}"


def test_ruleset_requires_exact_pr_gate_and_all_green_squash_queue() -> None:
    ruleset = json.loads((ROOT / "security/release-candidate-ruleset.json").read_text())
    rules = {r["type"]: r.get("parameters") for r in ruleset["rules"]}
    assert rules["required_status_checks"]["required_status_checks"] == [
        {"context": "pr / required", "integration_id": 15368}
    ]
    assert rules["merge_queue"]["grouping_strategy"] == "ALLGREEN"
    assert rules["merge_queue"]["merge_method"] == "SQUASH"


@pytest.mark.parametrize("mode", ["scanner", "policy"])
def test_rescan_attempts_every_image_before_reporting_failure(tmp_path, mode) -> None:
    step = next(
        step
        for step in workflow("container-rescan.yml")["jobs"]["rescan"]["steps"]
        if step.get("name") == "Rescan immutable images with fresh Trivy data"
    )
    (tmp_path / "published").mkdir()
    (tmp_path / "published/generated-service-images.json").write_text(
        json.dumps(
            [
                {"image": "example/first", "digest": "sha256:1"},
                {"image": "example/second", "digest": "sha256:2"},
            ]
        )
    )
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name in ("docker", "uv"):
        executable = bin_dir / name
        executable.write_text(
            "#!/bin/sh\n"
            f'echo "{name} $*" >> "$RUNNER_TEMP/calls"\n'
            f'if [ "$MODE" = "{"scanner" if name == "docker" else "policy"}" ]; then\n'
            '  case "$*" in *example/first*) exit 1 ;; esac\n'
            "fi\nexit 0\n"
        )
        executable.chmod(0o755)
    env = dict(
        os.environ,
        RUNNER_TEMP=str(tmp_path),
        MODE=mode,
        PATH=f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
    )
    result = subprocess.run(["bash", "-c", step["run"]], cwd=tmp_path, env=env, capture_output=True)
    assert result.returncode == 1, result.stderr
    calls = (tmp_path / "calls").read_text()
    assert "example/first@sha256:1" in calls
    assert "example/second@sha256:2" in calls
