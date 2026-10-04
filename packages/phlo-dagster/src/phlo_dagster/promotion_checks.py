"""Opt-in Dagster jobs for running project-owned, read-only promotion suites."""

import os
import selectors
import subprocess
import sys
import time
from pathlib import Path
from typing import Literal

import dagster as dg

PromotionCheckKind = Literal["tests", "contracts", "audits"]
_CHECK_KINDS: tuple[PromotionCheckKind, ...] = ("tests", "contracts", "audits")
_OUTPUT_LIMIT = 8000


def build_promotion_check_jobs(
    *,
    project_root: Path | str,
    check_paths: dict[PromotionCheckKind, tuple[str, ...]],
    timeout_seconds: int = 900,
) -> tuple[dg.JobDefinition, ...]:
    """Build three jobs that execute the project's selected pytest suites.

    The caller explicitly chooses real test files/directories for each check
    kind. Paths must exist beneath ``project_root``; no default suite is
    inferred because projects differ and some contract tests mutate shared
    providers. Select only suites that are safe to execute against staging.
    """
    root = Path(project_root).resolve(strict=True)
    if not root.is_dir():
        raise ValueError("project_root must be a directory")
    if set(check_paths) != set(_CHECK_KINDS):
        raise ValueError("check_paths must define exactly tests, contracts, and audits")
    if timeout_seconds < 1:
        raise ValueError("timeout_seconds must be positive")

    resolved_paths: dict[PromotionCheckKind, tuple[str, ...]] = {}
    for kind in _CHECK_KINDS:
        paths = check_paths[kind]
        if not paths:
            raise ValueError(f"Configure at least one pytest path for {kind}")
        resolved = []
        for value in paths:
            path = (root / value).resolve(strict=True)
            if not path.is_relative_to(root) or not (path.is_file() or path.is_dir()):
                raise ValueError(
                    f"{kind} path must identify a file or directory under project_root"
                )
            resolved.append(str(path.relative_to(root)))
        resolved_paths[kind] = tuple(resolved)

    return tuple(
        _pytest_job(kind, resolved_paths[kind], root, timeout_seconds) for kind in _CHECK_KINDS
    )


def _pytest_job(
    kind: PromotionCheckKind, paths: tuple[str, ...], root: Path, timeout_seconds: int
) -> dg.JobDefinition:
    job_name = f"phlo_promotion_{kind}"

    @dg.op(name=f"run_{job_name}")
    def run_suite(context: dg.OpExecutionContext) -> None:
        code_version = context.run.tags.get("phlo/code_version")
        initial_snapshot = None
        if code_version is not None:
            initial_snapshot = _git_snapshot(root)
            if initial_snapshot[0] != code_version:
                raise dg.Failure(
                    "promotion check code version does not match project_root HEAD; "
                    "refusing to run tests from a stale code location"
                )
            if not initial_snapshot[1]:
                raise dg.Failure(
                    "promotion check project_root worktree is dirty; "
                    "refusing to attest candidate code"
                )

        command = [
            sys.executable,
            "-m",
            "pytest",
            "-p",
            "no:cacheprovider",
            "--disable-warnings",
            "--maxfail=1",
            *paths,
        ]
        returncode, output = _run_bounded(command, root, timeout_seconds)
        if output:
            context.log.info(output[-8000:])
        if initial_snapshot is not None:
            final_snapshot = _git_snapshot(root)
            if final_snapshot != initial_snapshot:
                raise dg.Failure(
                    "promotion check changed project_root HEAD or worktree; "
                    "refusing candidate evidence"
                )
        if returncode:
            raise dg.Failure(f"{kind} suite failed with exit status {returncode}")

    @dg.job(name=job_name)
    def check_job() -> None:
        run_suite()

    return check_job


def _git_snapshot(root: Path) -> tuple[str, bool]:
    try:
        head = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        )
        status = subprocess.run(
            ["git", "-C", str(root), "status", "--porcelain", "--untracked-files=normal"],
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise dg.Failure(f"cannot verify promotion check source in {root}") from exc
    return head.stdout.strip(), not status.stdout


def _run_bounded(command: list[str], root: Path, timeout_seconds: int) -> tuple[int, str]:
    output = bytearray()
    deadline = time.monotonic() + timeout_seconds
    with subprocess.Popen(
        command,
        cwd=root,
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    ) as process:
        assert process.stdout is not None
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ)
            while selector.get_map() or process.poll() is None:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    process.kill()
                    process.wait()
                    raise dg.Failure(f"promotion suite exceeded {timeout_seconds} seconds")
                for key, _ in selector.select(min(remaining, 0.1)):
                    chunk = os.read(key.fd, 4096)
                    if not chunk:
                        selector.unregister(key.fileobj)
                        continue
                    output.extend(chunk)
                    if len(output) > _OUTPUT_LIMIT:
                        del output[:-_OUTPUT_LIMIT]
        return process.wait(), output.decode(errors="replace")
