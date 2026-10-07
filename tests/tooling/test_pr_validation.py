"""Pre-merge gates must cover every lane and cannot accept skipped work."""

import importlib.util
import json
import os
import subprocess
import sys
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


@pytest.mark.parametrize(
    "inspect_status,scan_status,policy_status,correct_report,expected",
    [
        (0, 0, 0, True, 0),
        (13, 0, 0, True, 13),
        (0, 17, 0, True, 17),
        (0, 0, 23, True, 23),
        (0, 0, 0, False, 1),
    ],
)
def test_local_image_scan_uses_built_ids_and_propagates_failures(
    tmp_path, inspect_status, scan_status, policy_status, correct_report, expected
):
    step = next(
        step
        for step in workflow("ci.yml")["jobs"]["installed-provider-artifacts"]["steps"]
        if step.get("name") == "Scan locally built first-party images without publishing"
    )
    artifacts = tmp_path / "provider-artifacts"
    artifacts.mkdir()
    image = "ghcr.io/phlohouse/phlo-api:0.17.0"
    (artifacts / "installed-provider-artifacts.json").write_text(
        json.dumps(
            {
                "images": [
                    {"service": "api", "image": image, "build": {"context": "."}},
                    {"service": "api-alias", "image": image, "build": {"context": "."}},
                    {"service": "vendor", "image": "postgres:18", "build": {"context": "."}},
                    {
                        "service": "unbuilt",
                        "image": "ghcr.io/phlohouse/phlo-observatory:0.17.0",
                        "build": {"context": "."},
                    },
                ],
                "builds": [
                    {"service": "api", "status": "passed"},
                    {"service": "api-alias", "status": "passed"},
                    {"service": "vendor", "status": "passed"},
                    {"service": "unbuilt", "status": "failed"},
                ],
            }
        )
    )
    binary = tmp_path / "bin"
    binary.mkdir()
    log = tmp_path / "calls"
    digest = "sha256:" + "a" * 64
    scan_report = json.dumps(
        {
            "SchemaVersion": 2,
            "ArtifactType": "container_image",
            "Metadata": {"ImageID": digest if correct_report else "sha256:" + "b" * 64},
        }
    )
    docker = binary / "docker"
    docker.write_text(
        f'#!/bin/sh\nprintf "docker %s\\n" "$*" >> "{log}"\n'
        f'if [ "$1" = image ]; then echo "{digest}"; exit {inspect_status}; fi\n'
        f"printf '%s' '{scan_report}' > 'provider-artifacts/security-{'a' * 64}.json'\n"
        f"exit {scan_status}\n"
    )
    uv = binary / "uv"
    uv.write_text(f'#!/bin/sh\nprintf "uv %s\\n" "$*" >> "{log}"\nexit {policy_status}\n')
    docker.chmod(0o755)
    uv.chmod(0o755)
    result = subprocess.run(
        ["bash", "-c", step["run"]],
        cwd=tmp_path,
        env=dict(os.environ, PATH=f"{binary}:{os.environ['PATH']}"),
        capture_output=True,
    )
    assert result.returncode == expected
    calls = log.read_text()
    assert calls.count("docker image inspect") == 1
    assert "postgres" not in calls and "observatory" not in calls and "push" not in calls
    if inspect_status == 0:
        assert "--image-src docker" in calls and f"--scanners vuln {digest}" in calls
        assert ("apply-policy" in calls) == (scan_status == 0 and correct_report)


def test_one_pr_orchestrator_and_no_candidate_duplication() -> None:
    pr = workflow("pr.yml")
    assert set(pr.get("on") or pr[True]) == {"pull_request", "merge_group", "workflow_call"}
    assert pr["jobs"]["required"]["needs"] == [
        "changes",
        "ci",
        "integration",
        "containers",
        "security",
        "docs",
        "mutation",
    ]
    for name in (
        "ci.yml",
        "integration.yml",
        "security.yml",
        "container-security.yml",
        "release-candidate.yml",
        "mutation.yml",
    ):
        definition = workflow(name)
        assert not {"pull_request", "merge_group"} & set(definition.get("on") or definition[True])
    assert not (ROOT / ".github/workflows/dependency-validation.yml").exists()


@pytest.mark.parametrize("bad_result", ["failure", "cancelled", "skipped", "", "success"])
def test_required_gate_executes_fail_closed(bad_result) -> None:
    step = workflow("pr.yml")["jobs"]["required"]["steps"][0]
    for lane in (
        "CHANGES",
        "CI",
        "CONTAINERS",
        "SECURITY",
        "INTEGRATION",
        "DOCS",
        "MUTATION",
    ):
        env = dict(os.environ, **dict.fromkeys(step["env"], "success"))
        env["INTEGRATION_SELECTED"] = "true"
        env["PYTHON_SELECTED"] = "false"
        env["DOCS_SELECTED"] = "true"
        env["MUTATION_SELECTED"] = "true"
        env[lane] = bad_result
        result = subprocess.run(["bash", "-c", step["run"]], env=env, capture_output=True)
        assert (result.returncode == 0) == (bad_result == "success")
    env["MUTATION"] = "success"
    for lane in ("INTEGRATION", "DOCS", "MUTATION"):
        env[f"{lane}_SELECTED"] = "false"
        env[lane] = "skipped"
        assert (
            subprocess.run(["bash", "-c", step["run"]], env=env, capture_output=True).returncode
            == 0
        )
        env[lane] = "success"
        assert (
            subprocess.run(["bash", "-c", step["run"]], env=env, capture_output=True).returncode
            != 0
        )
        env[f"{lane}_SELECTED"] = "true"


@pytest.mark.parametrize(
    "body",
    [
        "",
        "import pytest\npytest.skip('local', allow_module_level=True)",
        "def test_skip():\n import pytest\n pytest.skip('unavailable')",
        "def test_fail():\n assert False",
        "import pytest\n@pytest.mark.xfail\ndef test_xfail():\n assert False",
        "def test_pass():\n assert True\ndef test_skip():\n import pytest\n pytest.skip('local')",
        "def test_ok():\n assert True",
    ],
)
def test_required_suite_executes_and_rejects_skips(tmp_path, body) -> None:
    path = tmp_path / "test_contract.py"
    path.write_text(body)

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-p",
            "scripts.ci_required",
            str(path),
            f"--junitxml={tmp_path / 'result.xml'}",
            "-o",
            "addopts=",
        ],
        cwd=ROOT,
        capture_output=True,
    )
    assert (result.returncode == 0) == ("test_ok" in body), result.stdout
    assert (tmp_path / "result.xml").exists()


def test_merge_queue_executes_full_selection(tmp_path) -> None:
    step = workflow("pr.yml")["jobs"]["changes"]["steps"][-1]
    output = tmp_path / "output"
    summary = tmp_path / "summary"
    env = dict(
        os.environ,
        EVENT_NAME="merge_group",
        GITHUB_OUTPUT=str(output),
        GITHUB_STEP_SUMMARY=str(summary),
    )
    subprocess.run(["bash", "-c", step["run"]], cwd=ROOT, env=env, check=True)
    values = dict(line.split("=", 1) for line in output.read_text().splitlines())
    selection = json.loads(values["selection"])
    assert len(selection["groups"]) == 4
    assert all(
        selection[lane]
        for lane in ("python", "frontend", "writer", "integration", "docs", "plugin", "mutation")
    )
    assert summary.read_text().strip()


def test_local_skip_remains_successful_without_required_plugin(tmp_path) -> None:
    path = tmp_path / "test_local.py"
    path.write_text("def test_local():\n import pytest\n pytest.skip('intentional local skip')")
    result = subprocess.run(
        [sys.executable, "-m", "pytest", str(path), "-o", "addopts="], cwd=ROOT, capture_output=True
    )
    assert result.returncode == 0, result.stdout


def test_docs_reusable_without_duplicate_pr_trigger_or_queue_deployment() -> None:
    docs = workflow("docs.yml")
    events = docs.get("on") or docs[True]
    assert "pull_request" not in events
    assert docs["jobs"]["deploy"]["if"] == "github.event_name == 'push'"
    build = workflow("docs-build.yml")
    assert set(build.get("on") or build[True]) == {"workflow_call"}
    assert docs["jobs"]["build"]["uses"] == workflow("pr.yml")["jobs"]["docs"]["uses"]
    assert build["permissions"] == {"contents": "read"}


@pytest.mark.parametrize("selected", ["frontend", "writer", "plugin", "none"])
@pytest.mark.parametrize("result", ["success", "failure", "skipped", "cancelled"])
def test_combined_node_gate_requires_any_selected_consumer(tmp_path, selected, result):
    step = workflow("ci.yml")["jobs"]["ci-status"]["steps"][0]
    env = dict(os.environ, **dict.fromkeys(step["env"], "skipped"))
    env.update(
        CI_CONFIG="success",
        PYTHON_QUALITY="success",
        SELECT_PYTHON="false",
        SELECT_GROUPS="[]",
        SELECT_FRONTEND=str(selected == "frontend").lower(),
        SELECT_WRITER=str(selected == "writer").lower(),
        SELECT_PLUGIN=str(selected == "plugin").lower(),
        FRONTEND=result,
        GITHUB_STEP_SUMMARY=str(tmp_path / "summary"),
    )
    actual = subprocess.run(["bash", "-c", step["run"]], env=env, capture_output=True)
    assert (actual.returncode == 0) == (result == ("skipped" if selected == "none" else "success"))


def test_consolidated_jobs_preserve_named_checks_and_single_setup():
    jobs = workflow("ci.yml")["jobs"]
    quality = jobs["python-quality"]
    node = jobs["frontend"]
    for job in (quality, node):
        assert sum("actions/checkout@" in step.get("uses", "") for step in job["steps"]) == 1
        assert not any(step.get("continue-on-error") for step in job["steps"])
    assert {"frontend", "writer", "plugin"} <= {
        key for key in ("frontend", "writer", "plugin") if f"outputs.{key}" in node["if"]
    }
    names = {step.get("name") for step in node["steps"]}
    assert {"Run tests", "Run writer tests", "Run plugin behavior tests", "Build writer"} <= names
    quality_names = {step.get("name") for step in quality["steps"]}
    assert {
        "Check private-key hook with a non-allow-listed fixture",
        "Run remaining file hooks",
        "Audit workflow hardening",
        "Lint with ruff",
        "Type check with ty",
    } <= quality_names
    assert "pre-commit" not in jobs and "phlo-github-writer" not in jobs
    assert "plugin" not in workflow("pr.yml")["jobs"]


def test_required_result_collects_coverage_after_service_and_python_contracts():
    required = workflow("pr.yml")["jobs"]["required"]
    assert {"ci", "integration"} <= set(required["needs"])
    steps = required["steps"]
    downloads = [step["with"] for step in steps if "download-artifact@" in step.get("uses", "")]
    assert {item.get("pattern") for item in downloads} >= {"core-tests-*", "package-results-*"}
    assert any(item.get("name") == "quickstart-smoke" for item in downloads)
    combine = next(step for step in steps if "coverage combine" in step.get("run", ""))
    assert combine["if"] == "needs.changes.outputs.python == 'true'"
    emit = next(step for step in steps if "ci_evidence.py emit" in step.get("run", ""))
    assert steps.index(combine) < steps.index(emit)
    assert "coverage" not in workflow("ci.yml")["jobs"]


@pytest.mark.parametrize("first_fails", [False, True])
def test_container_lint_attempts_all_selected_files_and_preserves_failure(tmp_path, first_fails):
    job = workflow("container-security.yml")["jobs"]["checks"]
    step = next(
        step for step in job["steps"] if step.get("name") == "Lint generated-service Dockerfiles"
    )
    binary = tmp_path / "bin"
    binary.mkdir()
    executable = binary / "docker"
    executable.write_text(
        '#!/bin/sh\nbody="$(cat)"\nprintf "%s\\n" "$body" >> "$RUNNER_TEMP/calls"\n'
        'if [ "$FIRST_FAILS" = true ] && [ "$body" = first ]; then exit 1; fi\n'
    )
    executable.chmod(0o755)
    for name in ("first", "second"):
        (tmp_path / name).mkdir()
        (tmp_path / name / "Dockerfile").write_text(name)
    env = dict(
        os.environ,
        PATH=f"{binary}:{os.environ['PATH']}",
        RUNNER_TEMP=str(tmp_path),
        FIRST_FAILS=str(first_fails).lower(),
        TARGETS=json.dumps(
            {
                "include": [
                    {"package": name, "dockerfile": "Dockerfile"} for name in ("first", "second")
                ]
            }
        ),
    )
    actual = subprocess.run(["bash", "-c", step["run"]], cwd=tmp_path, env=env, capture_output=True)
    assert actual.returncode == int(first_fails)
    assert (tmp_path / "calls").read_text().splitlines() == ["first", "second"]


def test_shards_are_disjoint_complete_and_keep_module_fixtures_together() -> None:
    nodes = {f"tests/test_{i}.py::test_case[{j}]" for i in range(100) for j in range(3)}
    shards = [{node for node in nodes if shard_for(node, 3) == index} for index in range(3)]
    assert all(shards)
    assert set.union(*shards) == nodes
    assert sum(map(len, shards)) == len(nodes)
    for i in range(100):
        assert len({shard_for(f"tests/test_{i}.py::test_case[{j}]", 3) for j in range(3)}) == 1


def test_supported_python_version_runs_before_merge() -> None:
    ci = workflow("ci.yml")["jobs"]
    assert ci["python-core-tests"]["strategy"]["matrix"]["python-version"] == ["3.12"]
    assert ci["python-package-tests"]["strategy"]["matrix"] == {
        "include": "${{ fromJSON(needs.ci-config.outputs.groups) }}"
    }
    selection_spec = importlib.util.spec_from_file_location(
        "select_ci", ROOT / "scripts/select_ci.py"
    )
    assert selection_spec and selection_spec.loader
    selector = importlib.util.module_from_spec(selection_spec)
    selection_spec.loader.exec_module(selector)
    assert {entry["python-version"] for entry in selector.select(set())["groups"]} == {"3.12"}
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
