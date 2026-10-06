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
    ("paths", "expected"),
    [
        (
            {"README.md"},
            [
                ["python3", "scripts/check_markdown_links.py"],
                ["python3", "scripts/check_adr_index.py"],
                ["make", "docs-build"],
            ],
        ),
        (
            {"apps/phlo-github-writer/src/index.ts"},
            [
                ["npm", "--prefix", "apps/phlo-github-writer", "run", task]
                for task in ("typecheck", "test", "build")
            ],
        ),
        (
            {".amp/plugins/phlo-github/lib.ts"},
            [
                [
                    "node",
                    "--experimental-strip-types",
                    "--test",
                    ".amp/plugins/phlo-github/lib.test.ts",
                ]
            ],
        ),
    ],
)
def test_main_executes_only_selected_commands(monkeypatch, capsys, paths, expected) -> None:
    monkeypatch.setattr(sys, "argv", ["check_changed.py", "--base", "test-base"])
    monkeypatch.setattr(select_ci, "git_paths", lambda base, **kw: paths)
    calls = []
    monkeypatch.setattr(
        check_changed.subprocess, "run", lambda command, **kw: calls.append(command)
    )
    check_changed.main()
    assert calls == expected
    assert "Remaining required CI contracts" in capsys.readouterr().out


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
