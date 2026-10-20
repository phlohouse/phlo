"""Defend complete branch coverage and source floors across parallel producers.

Real pytest fixture runs compare the original serial measurement with the shard
union; malformed or unsuccessful producer artifacts must block aggregation.
"""

from __future__ import annotations

import json
import os
import runpy
import shutil
import subprocess
import sys
import venv
import xml.etree.ElementTree as ET
from pathlib import Path

import coverage
import pytest
from coverage import Coverage, CoverageData
from coverage.exceptions import CoverageException

ROOT = Path(__file__).resolve().parents[2]
combine = runpy.run_path(str(ROOT / "scripts/ci_coverage.py"))["combine"]
shard_for = runpy.run_path(str(ROOT / "scripts/ci_shard.py"))["shard_for"]


def _write_suite(directory: Path, name: str, outcomes: tuple[str, ...] = ("passed",)) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    root = ET.Element("testsuites")
    suite = ET.SubElement(root, "testsuite", name=name, tests=str(len(outcomes)))
    for index, outcome in enumerate(outcomes):
        case = ET.SubElement(suite, "testcase", classname="fixture", name=f"test_{index}")
        if outcome != "passed":
            ET.SubElement(case, outcome)
    ET.ElementTree(root).write(directory / "results.xml")


@pytest.fixture
def producer_inputs(tmp_path: Path) -> Path:
    source = tmp_path / "fixture.py"
    source.write_text("answer = 42\n")
    inputs = tmp_path / "inputs"
    for producer in ("workspace-0", "workspace-1", "postgres"):
        directory = inputs / producer
        suite_name = producer if producer == "postgres" else f"{producer}-of-2"
        _write_suite(directory, suite_name)
        data = CoverageData(basename=str(directory / "coverage.data"))
        data.add_arcs({str(source): [(-1, 1), (1, -1)]})
        data.write()
    return inputs


@pytest.mark.parametrize("producer", ["workspace-0", "workspace-1", "postgres"])
def test_missing_producer_blocks_incomplete_coverage(producer_inputs: Path, producer: str) -> None:
    shutil.rmtree(producer_inputs / producer)
    with pytest.raises(ValueError, match="Coverage producers differ"):
        combine(producer_inputs, producer_inputs.parent / "output", 2)
    assert not (producer_inputs.parent / "output").exists()


def test_extra_producer_and_mismatched_shard_count_are_rejected(producer_inputs: Path) -> None:
    extra = producer_inputs / "workspace-2"
    extra.mkdir()
    with pytest.raises(ValueError, match="unexpected=.*workspace-2"):
        combine(producer_inputs, producer_inputs.parent / "output", 2)
    extra.rmdir()
    _write_suite(producer_inputs / "workspace-0", "workspace-0-of-3")
    with pytest.raises(ValueError, match="expected test suite workspace-0-of-2"):
        combine(producer_inputs, producer_inputs.parent / "output", 2)


@pytest.mark.parametrize(
    ("outcomes", "message"),
    [
        (("passed", "failure"), "test failures or collection errors"),
        (("error",), "test failures or collection errors"),
        ((), "no executed tests"),
        (("skipped",), "no executed tests"),
    ],
)
def test_unsuccessful_workspace_results_block(
    producer_inputs: Path, outcomes: tuple[str, ...], message: str
) -> None:
    _write_suite(producer_inputs / "workspace-0", "workspace-0-of-2", outcomes)
    with pytest.raises(ValueError, match=message):
        combine(producer_inputs, producer_inputs.parent / "output", 2)


def test_collection_failure_counts_cannot_hide_behind_passing_cases(producer_inputs: Path) -> None:
    path = producer_inputs / "workspace-0/results.xml"
    results = ET.parse(path)
    results.getroot()[0].set("errors", "1")
    results.write(path)
    with pytest.raises(ValueError, match="collection errors"):
        combine(producer_inputs, producer_inputs.parent / "output", 2)


def test_required_postgres_skips_block_even_with_other_passing_tests(producer_inputs: Path) -> None:
    _write_suite(producer_inputs / "postgres", "postgres", ("passed", "skipped"))
    with pytest.raises(ValueError, match="required PostgreSQL tests were skipped or xfailed"):
        combine(producer_inputs, producer_inputs.parent / "output", 2)


def test_workspace_skips_preserve_existing_optional_test_policy(producer_inputs: Path) -> None:
    _write_suite(producer_inputs / "workspace-0", "workspace-0-of-2", ("passed", "skipped"))
    output = producer_inputs.parent / "output"
    report = combine(producer_inputs, output, 2)
    assert json.loads(report.read_text())["totals"]["covered_lines"] == 1
    assert len(ET.parse(output / "workspace-0.xml").findall(".//testcase")) == 2
    assert all((directory / "coverage.data").exists() for directory in producer_inputs.iterdir())


@pytest.mark.parametrize("filename", ["results.xml", "coverage.data"])
def test_missing_producer_artifact_blocks(producer_inputs: Path, filename: str) -> None:
    (producer_inputs / "workspace-0" / filename).unlink()
    with pytest.raises((OSError, ValueError)):
        combine(producer_inputs, producer_inputs.parent / "output", 2)
    assert not (producer_inputs.parent / "output").exists()


@pytest.mark.parametrize("content", [b"", b"corrupt sqlite data"])
def test_unreadable_coverage_data_blocks(producer_inputs: Path, content: bytes) -> None:
    (producer_inputs / "workspace-0/coverage.data").write_bytes(content)
    with pytest.raises((ValueError, CoverageException)):
        combine(producer_inputs, producer_inputs.parent / "output", 2)
    assert not (producer_inputs.parent / "output").exists()


@pytest.mark.parametrize("branch", [False, True])
def test_line_only_or_empty_branch_data_cannot_satisfy_a_producer(
    producer_inputs: Path, branch: bool
) -> None:
    path = producer_inputs / "workspace-0/coverage.data"
    path.unlink()
    data = CoverageData(basename=str(path))
    if branch:
        data.add_arcs({})
    else:
        data.add_lines({str(producer_inputs.parent / "fixture.py"): [1]})
    data.write()
    with pytest.raises(ValueError, match="expected nonempty branch coverage data"):
        combine(producer_inputs, producer_inputs.parent / "output", 2)


def test_nonpositive_shard_count_is_invalid(producer_inputs: Path) -> None:
    with pytest.raises(ValueError, match="shard count must be positive"):
        combine(producer_inputs, producer_inputs.parent / "output", 0)


def test_cli_reports_incomplete_artifacts_without_creating_a_report(tmp_path: Path) -> None:
    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts/ci_coverage.py"),
            "--inputs",
            str(tmp_path),
            "--output",
            str(tmp_path / "output"),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 2
    assert "Coverage producers differ" in result.stderr
    assert not (tmp_path / "output").exists()


def _write_workspace(root: Path, shards: int) -> None:
    sources = {
        "src/phlo/fixture_core.py": (
            "def classify(value):\n"
            "    if value is None:\n        return 'missing'\n"
            "    if value > 0:\n        return 'positive'\n"
            "    return 'nonpositive'\n"
        ),
        "src/phlo/unused.py": (
            "def unused(value):\n    if value:\n        return True\n    return False\n"
        ),
        "scripts/fixture_script.py": (
            "def even(value):\n    if value % 2 == 0:\n        return True\n    return False\n"
        ),
        "packages/example/src/fixture_package.py": (
            "def direction(value):\n    if value > 0:\n        return 1\n    return -1\n"
        ),
    }
    for relative, content in sources.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    (root / "pytest.ini").write_text("[pytest]\nmarkers = integration: fixture service subset\n")
    (root / "tests").mkdir()
    for shard in range(shards):
        suffix = 0
        while shard_for(f"tests/test_workspace_{shard}_{suffix}.py", shards) != shard:
            suffix += 1
        (root / f"tests/test_workspace_{shard}_{suffix}.py").write_text(
            "import pytest\n"
            "from fixture_core import classify\n"
            "from fixture_script import even\n"
            "from fixture_package import direction\n"
            f"@pytest.mark.parametrize('value', [{shard + 1}, {-shard - 1}])\n"
            "def test_branch_outcomes(value):\n"
            "    assert classify(value) == ('positive' if value > 0 else 'nonpositive')\n"
            "    assert even(value) == (value % 2 == 0)\n"
            "    assert direction(value) == (1 if value > 0 else -1)\n"
        )
    (root / "tests/test_postgres.py").write_text(
        "import pytest\nfrom fixture_core import classify\n"
        "@pytest.mark.integration\n"
        "def test_postgres_only_branch():\n    assert classify(None) == 'missing'\n"
    )


def _run_fixture_tests(
    root: Path, data: Path, results: Path, suite: str, selection: list[str], *, append: bool = False
) -> None:
    sources = [root / path for path in ("src/phlo", "scripts", "packages/example/src")]
    env = {
        **os.environ,
        "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
        "PYTEST_ADDOPTS": "",
        "PYTHONPATH": os.pathsep.join(str(path) for path in [ROOT, *sources]),
        "COVERAGE_FILE": str(data),
    }
    results.parent.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-p",
            "pytest_cov.plugin",
            "-o",
            "addopts=",
            "-o",
            f"junit_suite_name={suite}",
            "--junitxml",
            str(results),
            "--cov=src/phlo",
            "--cov=scripts",
            "--cov=packages/example/src",
            "--cov-branch",
            "--cov-report=",
            *(["--cov-append"] if append else []),
            "-q",
            *selection,
        ],
        cwd=root,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def _node_ids(path: Path) -> set[tuple[str, str]]:
    return {
        (case.attrib["classname"], case.attrib["name"]) for case in ET.parse(path).iter("testcase")
    }


@pytest.fixture
def reporting_python(tmp_path: Path) -> Path:
    """Report real data with coverage available but no application or test packages."""
    directory = tmp_path / ".reporting-venv"
    venv.EnvBuilder(with_pip=False).create(directory)
    executable = directory / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    site = subprocess.run(
        [str(executable), "-c", "import sysconfig; print(sysconfig.get_path('purelib'))"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    shutil.copytree(Path(coverage.__file__).parent, Path(site) / "coverage")
    result = subprocess.run(
        [
            str(executable),
            "-I",
            "-c",
            "import coverage, importlib.util; "
            "assert all(importlib.util.find_spec(name) is None "
            "for name in ('phlo', 'pytest', 'yaml', 'dagster'))",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return executable


def test_parallel_measurement_matches_serial_lines_branches_selection_and_floors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, reporting_python: Path
) -> None:
    shards = 4
    _write_workspace(tmp_path, shards)
    serial = tmp_path / "serial"
    _run_fixture_tests(
        tmp_path,
        serial / "coverage.data",
        serial / "results.xml",
        "serial",
        ["-m", "not integration"],
    )
    postgres_selection = [
        "-p",
        "scripts.ci_required",
        "-m",
        "integration",
        "tests/test_postgres.py",
    ]
    _run_fixture_tests(
        tmp_path,
        serial / "coverage.data",
        serial / "postgres.xml",
        "postgres",
        postgres_selection,
        append=True,
    )
    inputs = tmp_path / "inputs"
    seen: set[tuple[str, str]] = set()
    for shard in range(shards):
        directory = inputs / f"workspace-{shard}"
        _run_fixture_tests(
            tmp_path,
            directory / "coverage.data",
            directory / "results.xml",
            f"workspace-{shard}-of-{shards}",
            [
                "-p",
                "scripts.ci_shard",
                "-m",
                "not integration",
                f"--ci-shard-index={shard}",
                f"--ci-shard-count={shards}",
            ],
        )
        selected = _node_ids(directory / "results.xml")
        assert selected
        assert not seen & selected
        seen.update(selected)
    assert seen == _node_ids(serial / "results.xml")
    postgres = inputs / "postgres"
    _run_fixture_tests(
        tmp_path,
        postgres / "coverage.data",
        postgres / "results.xml",
        "postgres",
        postgres_selection,
    )
    assert _node_ids(postgres / "results.xml") == _node_ids(serial / "postgres.xml")
    monkeypatch.chdir(tmp_path)
    serial_data = Coverage(data_file=str(serial / "coverage.data"))
    serial_data.load()
    serial_data.json_report(outfile=str(serial / "coverage.json"))
    expected = json.loads((serial / "coverage.json").read_text())
    combined = tmp_path / "combined"
    result = subprocess.run(
        [
            str(reporting_python),
            "-I",
            str(ROOT / "scripts/ci_coverage.py"),
            "--inputs",
            str(inputs),
            "--output",
            str(combined),
            "--shards",
            str(shards),
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    actual = json.loads((combined / "coverage.json").read_text())
    assert actual["files"] == expected["files"]
    assert actual["totals"] == expected["totals"]
    assert actual["totals"]["num_branches"] > 0
    assert actual["files"]["src/phlo/unused.py"]["summary"]["covered_lines"] == 0
    for producer in inputs.iterdir():
        assert (combined / f"{producer.name}.xml").read_bytes() == (
            producer / "results.xml"
        ).read_bytes()

    floors = tmp_path / "floors.json"
    floors.write_text(
        json.dumps({name: {"line": 0, "branch": 0} for name in ("core", "scripts", "example")})
    )
    command = [
        str(reporting_python),
        "-I",
        str(ROOT / "scripts/coverage_report.py"),
        str(tmp_path / "combined/coverage.json"),
        "--floors",
        str(floors),
        "--output",
        str(tmp_path / "totals.json"),
    ]
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stdout + result.stderr
    measured = json.loads((tmp_path / "totals.json").read_text())
    assert set(measured) == {"core", "scripts", "example"}
    for metric in ("line", "branch"):
        regressed = {name: {"line": 0, "branch": 0} for name in measured}
        assert measured["core"][metric] < 100
        regressed["core"][metric] = measured["core"][metric] + 0.01
        floors.write_text(json.dumps(regressed))
        result = subprocess.run(command, capture_output=True, text=True, check=False)
        assert result.returncode == 1
        assert f"core {metric}:" in result.stdout
