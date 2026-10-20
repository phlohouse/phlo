"""Check deadline and newly introduced suppression gates."""

import importlib.util
import subprocess
import sys
from pathlib import Path


def load_script(name: str):
    spec = importlib.util.spec_from_file_location(
        name, Path(__file__).resolve().parents[2] / f"scripts/{name}.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


added_suppressions = load_script("check_new_suppressions").added_suppressions
missing_timeouts = load_script("check_workflow_timeouts").missing_timeouts
headers = load_script("check_file_headers")


def test_deadline_check_ignores_reusable_calls_but_rejects_jobs(tmp_path) -> None:
    assert missing_timeouts(
        "jobs:\n  call:\n    uses: ./.github/workflows/ci.yml\n"
        "  worker:\n    runs-on: ubuntu-latest\n    steps: []\n"
    ) == ["worker"]
    assert (
        missing_timeouts("jobs:\n  worker:\n    timeout-minutes: 5\n    runs-on: ubuntu-latest\n")
        == []
    )


def test_new_noqa_requires_adjacent_reason(tmp_path) -> None:
    file = tmp_path / "module.py"
    file.write_text("# reason: upstream API is untyped\nvalue = call()  # noqa: C901\n")
    diff = (
        "+++ b/module.py\n@@ -0,0 +1,2 @@\n"
        "+# reason: upstream API is untyped\n+value = call()  # noqa: C901\n"
    )
    assert added_suppressions(diff, tmp_path) == []
    file.write_text("value = call()  # noqa: C901\n")
    assert (
        len(
            added_suppressions(
                "+++ b/module.py\n@@ -0,0 +1 @@\n+value = call()  # noqa: C901\n", tmp_path
            )
        )
        == 1
    )


def test_header_baseline_only_allows_unchanged_bytes(tmp_path, monkeypatch) -> None:
    def git(*args: str) -> str:
        return subprocess.run(
            [
                "git",
                "-c",
                "user.name=Test",
                "-c",
                "user.email=test@example.com",
                "-c",
                "commit.gpgsign=false",
                "-c",
                "core.hooksPath=/dev/null",
                *args,
            ],
            cwd=tmp_path,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()

    monkeypatch.chdir(tmp_path)
    old = Path("legacy.py")
    old.write_text("value = 1\n")
    git("init", "-q")
    git("add", str(old))
    git("commit", "-qm", "header baseline")
    baseline = git("rev-parse", "HEAD")
    monkeypatch.setattr(headers, "BASELINE", baseline)

    new = Path("new.py")
    new.write_text("value = 1\n")
    git("add", str(new))
    git("commit", "-qm", "new headerless file")
    assert git("rev-parse", "HEAD") != baseline

    assert not headers.has_header(old)
    assert headers.unchanged_baseline(old)
    assert headers.main([str(old)]) == 0
    assert not headers.unchanged_baseline(new)
    assert headers.main([str(new)]) == 1

    old.write_text("value = 2\n")
    assert not headers.unchanged_baseline(old)
    assert headers.main([str(old)]) == 1
    old.write_text('"""Describe the updated module."""\nvalue = 2\n')
    assert headers.main([str(old)]) == 0


def test_skip_guard_ignores_fixture_strings_but_checks_real_calls_and_decorators(tmp_path):
    source = (
        "fixture = \"pytest.skip('local')\"\n"
        "@pytest.mark.skip\ndef test_contract(): pass\n"
        'pytest.skip("unavailable")\n'
    )
    (tmp_path / "module.py").write_text(source)
    diff = "+++ b/module.py\n@@ -0,0 +1,4 @@\n" + "".join(
        "+" + line + "\n" for line in source.splitlines()
    )
    violations = added_suppressions(diff, tmp_path)
    assert len(violations) == 2
    assert violations[0].startswith("module.py:2:")
    assert violations[1].startswith("module.py:4:")


def test_first_party_warning_fails_pytest(tmp_path) -> None:
    warning_test = tmp_path / "test_warning.py"
    warning_test.write_text(
        'import warnings\n\ndef test_warning():\n    warnings.warn("first-party warning sentinel")\n'
    )
    root = Path(__file__).resolve().parents[2]
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "-c",
            str(root / "pyproject.toml"),
            str(warning_test),
        ],
        cwd=root,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 1
    assert "UserWarning: first-party warning sentinel" in result.stdout
