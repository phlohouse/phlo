"""Exercise local selection and native command execution, not workflow YAML."""

import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import check_changed
import select_ci


def test_git_paths_include_committed_staged_unstaged_and_untracked(tmp_path: Path) -> None:
    def git(*args: str) -> None:
        subprocess.run(["git", *args], cwd=tmp_path, check=True, capture_output=True)

    git("init")
    git("config", "user.email", "test@example.com")
    git("config", "user.name", "Test")
    for name in ("committed", "staged", "unstaged", "deleted", "old name"):
        (tmp_path / name).write_text("initial")
    git("add", ".")
    git("commit", "-m", "base")
    git("tag", "base")
    assert select_ci.git_paths("base", root=tmp_path) == set()
    assert select_ci.select(select_ci.git_paths("base", root=tmp_path))["frontend"]
    (tmp_path / "committed").write_text("new")
    git("add", "committed")
    git("commit", "-m", "change")
    (tmp_path / "staged").write_text("new")
    git("add", "staged")
    (tmp_path / "unstaged").write_text("new")
    (tmp_path / "deleted").unlink()
    git("mv", "old name", "new name")
    (tmp_path / "untracked\nname").write_text("new")
    (tmp_path / ".gitignore").write_text("ignored\n")
    (tmp_path / "ignored").write_text("ignored")
    assert select_ci.git_paths("base", root=tmp_path) == {"committed"}
    assert select_ci.git_paths("base", working_tree=True, root=tmp_path) == {
        "committed",
        "staged",
        "unstaged",
        "deleted",
        "old name",
        "new name",
        "untracked\nname",
        ".gitignore",
    }


@pytest.mark.parametrize(
    ("paths", "expected", "surface"),
    [
        (
            {"README.md"},
            [
                "check_markdown_links",
                "check_adr_index",
                "docs-build",
            ],
            "docs",
        ),
        (
            {"apps/phlo-github-writer/src/index.ts"},
            [f"apps/phlo-github-writer:{task}" for task in ("typecheck", "test", "build")],
            "writer",
        ),
        (
            {".amp/plugins/phlo-github/lib.ts"},
            [".amp/plugins/phlo-github/lib.test.ts"],
            "plugin",
        ),
    ],
)
def test_main_executes_only_selected_commands(
    monkeypatch, capsys, tmp_path, paths, expected, surface
) -> None:
    monkeypatch.setattr(sys, "argv", ["check_changed.py", "--base", "test-base"])
    monkeypatch.setattr(select_ci, "git_paths", lambda base, **kw: paths)
    completed = tmp_path / "completed"

    def run(command, *, cwd, check):
        assert cwd == select_ci.ROOT
        assert check is True
        if command[0] == "python3":
            checks = [Path(command[1]).stem]
        elif command[0] == "make":
            checks = command[1:]
        elif command[0] == "npm":
            assert command[1] == "--prefix" and command[3] == "run"
            checks = [f"{command[2]}:{command[4]}"]
        elif command[0] == "node":
            assert command[1:3] == ["--experimental-strip-types", "--test"]
            checks = command[3:]
        else:
            raise AssertionError(f"Unexpected native check: {command}")
        with completed.open("a") as output:
            output.writelines(f"{check_name}\n" for check_name in checks)
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(check_changed.subprocess, "run", run)
    check_changed.main()
    assert completed.read_text().splitlines() == expected
    output = capsys.readouterr().out
    assert output.splitlines()[0] == f"Selected: {surface}"
    assert "Remaining required CI contracts" in output


def test_package_commands_follow_selected_groups_and_full_diff() -> None:
    selection = select_ci.select({"packages/phlo-traefik/src/phlo_traefik/plugin.py"})
    commands = check_changed.commands(selection)
    expected_packages = selection["groups"][0]["packages"].split()
    tests = [command for command in commands if "--package" in command]
    assert [command[4] for command in tests] == expected_packages
    assert all(command[-1] == f"packages/{command[4]}/tests" for command in tests)
    assert all("not integration" in command for command in tests)
    assert not any(command[0] in {"npm", "node", "docker"} for command in commands)
    full = check_changed.commands(select_ci.select(set()))
    assert ["uv", "run", "--locked", "pytest", "-m", "not integration"] in full
    assert ["make", "lint-ts", "format-ts", "typecheck-ts"] in full


def test_dry_run_does_not_execute_and_failures_stop(monkeypatch) -> None:
    monkeypatch.setattr(select_ci, "git_paths", lambda *args, **kw: {"README.md"})
    calls = []

    def fail(command, **kwargs):
        calls.append(command)
        raise subprocess.CalledProcessError(7, command)

    monkeypatch.setattr(check_changed.subprocess, "run", fail)
    monkeypatch.setattr(sys, "argv", ["check_changed.py", "--dry-run"])
    check_changed.main()
    assert calls == []
    monkeypatch.setattr(sys, "argv", ["check_changed.py"])
    with pytest.raises(subprocess.CalledProcessError):
        check_changed.main()
    assert len(calls) == 1
