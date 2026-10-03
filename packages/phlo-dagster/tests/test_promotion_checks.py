"""Executable tests for opt-in, project-backed promotion check jobs."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from phlo_dagster import promotion_checks
from phlo_dagster.promotion_checks import PromotionCheckKind, build_promotion_check_jobs


def _project(
    tmp_path: Path, *, failing: str | None = None
) -> dict[PromotionCheckKind, tuple[str, ...]]:
    paths: dict[PromotionCheckKind, tuple[str, ...]] = {}
    for kind in ("tests", "contracts", "audits"):
        directory = tmp_path / kind
        directory.mkdir()
        assertion = "assert False" if kind == failing else "assert True"
        (directory / "test_check.py").write_text(f"def test_real_check():\n    {assertion}\n")
        paths[kind] = (kind,)
    return paths


def test_builds_and_executes_one_real_pytest_job_per_configured_category(tmp_path: Path) -> None:
    paths = _project(tmp_path)
    jobs = build_promotion_check_jobs(project_root=tmp_path, check_paths=paths)

    assert [job.name for job in jobs] == [
        "phlo_promotion_tests",
        "phlo_promotion_contracts",
        "phlo_promotion_audits",
    ]
    assert all(job.execute_in_process().success for job in jobs)


def test_failed_project_suite_fails_its_dagster_job(tmp_path: Path) -> None:
    jobs = build_promotion_check_jobs(
        project_root=tmp_path,
        check_paths=_project(tmp_path, failing="contracts"),
    )

    result = jobs[1].execute_in_process(raise_on_error=False)

    assert not result.success


_INVALID_CHECK_PATHS: list[tuple[dict[PromotionCheckKind, tuple[str, ...]], str]] = [
    (
        {"tests": ("tests",), "contracts": ("contracts",)},
        "exactly tests, contracts, and audits",
    ),
    (
        {"tests": (), "contracts": ("contracts",), "audits": ("audits",)},
        "at least one pytest path for tests",
    ),
]


@pytest.mark.parametrize(
    ("paths", "error"),
    _INVALID_CHECK_PATHS,
)
def test_factory_requires_explicit_project_suites(
    tmp_path: Path, paths: dict[PromotionCheckKind, tuple[str, ...]], error: str
) -> None:
    with pytest.raises(ValueError, match=error):
        build_promotion_check_jobs(project_root=tmp_path, check_paths=paths)


def test_factory_rejects_paths_outside_project_root(tmp_path: Path) -> None:
    outside = tmp_path.parent / f"{tmp_path.name}-outside"
    outside.mkdir()
    paths: dict[PromotionCheckKind, tuple[str, ...]] = {
        kind: (str(outside),) for kind in ("tests", "contracts", "audits")
    }

    with pytest.raises(ValueError, match="under project_root"):
        build_promotion_check_jobs(project_root=tmp_path, check_paths=paths)


def _commit_project(root: Path) -> str:
    for command in (
        ["git", "init", "--quiet", str(root)],
        ["git", "-C", str(root), "config", "user.name", "Promotion Test"],
        ["git", "-C", str(root), "config", "user.email", "promotion-test@example.invalid"],
        ["git", "-C", str(root), "add", "."],
        ["git", "-C", str(root), "commit", "--quiet", "-m", "suite"],
    ):
        subprocess.run(command, check=True, capture_output=True, text=True)
    return subprocess.run(
        ["git", "-C", str(root), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def test_stale_promotion_tag_fails_before_pytest(tmp_path: Path) -> None:
    marker = tmp_path / "pytest-ran"
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests/test_check.py").write_text(
        f"from pathlib import Path\nPath({str(marker)!r}).touch()\ndef test_check(): pass\n"
    )
    _commit_project(tmp_path)
    jobs = build_promotion_check_jobs(
        project_root=tmp_path,
        check_paths={kind: ("tests",) for kind in ("tests", "contracts", "audits")},
    )

    result = jobs[0].execute_in_process(
        tags={"phlo/code_version": "not-the-current-revision"},
        raise_on_error=False,
    )

    assert not result.success
    assert not marker.exists()


def test_matching_promotion_tag_runs_pytest_and_preserves_clean_source(
    tmp_path: Path,
) -> None:
    paths = _project(tmp_path)
    code_version = _commit_project(tmp_path)
    jobs = build_promotion_check_jobs(project_root=tmp_path, check_paths=paths)

    result = jobs[0].execute_in_process(tags={"phlo/code_version": code_version})

    assert result.success
    assert promotion_checks._git_snapshot(tmp_path) == (code_version, True)


def test_promotion_run_requires_clean_worktree_before_pytest(tmp_path: Path) -> None:
    marker = tmp_path / "pytest-ran"
    paths = _project(tmp_path)
    test_file = tmp_path / "tests/test_check.py"
    test_file.write_text(
        f"from pathlib import Path\nPath({str(marker)!r}).touch()\ndef test_check(): pass\n"
    )
    code_version = _commit_project(tmp_path)
    test_file.write_text(test_file.read_text() + "# dirty\n")
    jobs = build_promotion_check_jobs(project_root=tmp_path, check_paths=paths)

    result = jobs[0].execute_in_process(
        tags={"phlo/code_version": code_version}, raise_on_error=False
    )

    assert not result.success
    assert not marker.exists()
    assert not promotion_checks._git_snapshot(tmp_path)[1]


def test_promotion_run_rejects_worktree_changes_after_pytest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = _project(tmp_path)
    code_version = _commit_project(tmp_path)
    changed_file = tmp_path / "tests/test_check.py"
    jobs = build_promotion_check_jobs(project_root=tmp_path, check_paths=paths)

    def modify_source(command: list[str], root: Path, timeout_seconds: int) -> tuple[int, str]:
        changed_file.write_text(changed_file.read_text() + "# changed\n")
        return 0, "suite passed"

    monkeypatch.setattr(promotion_checks, "_run_bounded", modify_source)
    result = jobs[0].execute_in_process(
        tags={"phlo/code_version": code_version}, raise_on_error=False
    )

    assert not result.success
    assert not promotion_checks._git_snapshot(tmp_path)[1]


def test_promotion_run_rejects_head_changes_after_pytest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = _project(tmp_path)
    code_version = _commit_project(tmp_path)
    (tmp_path / "alternate.txt").write_text("alternate\n")
    subprocess.run(["git", "-C", str(tmp_path), "add", "alternate.txt"], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "commit", "--quiet", "-m", "alternate"], check=True)
    alternate_revision = subprocess.run(
        ["git", "-C", str(tmp_path), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    subprocess.run(["git", "-C", str(tmp_path), "checkout", "--quiet", code_version], check=True)
    jobs = build_promotion_check_jobs(project_root=tmp_path, check_paths=paths)

    def change_head(command: list[str], root: Path, timeout_seconds: int) -> tuple[int, str]:
        subprocess.run(
            ["git", "-C", str(root), "switch", "--quiet", "--detach", alternate_revision],
            check=True,
        )
        return 0, "suite passed"

    monkeypatch.setattr(promotion_checks, "_run_bounded", change_head)
    result = jobs[0].execute_in_process(
        tags={"phlo/code_version": code_version}, raise_on_error=False
    )

    assert not result.success
    assert promotion_checks._git_snapshot(tmp_path) == (alternate_revision, True)


def test_bounded_runner_retains_only_output_tail(tmp_path: Path) -> None:
    returncode, output = promotion_checks._run_bounded(
        [sys.executable, "-c", "print('x' * 20000)"], tmp_path, 5
    )

    assert returncode == 0
    assert len(output.encode()) <= 8000
    assert output.endswith("x" * 7999 + "\n")
