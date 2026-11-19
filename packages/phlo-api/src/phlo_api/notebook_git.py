"""Commit notebooks (and models promoted from them) to the project's git repository.

Opt-in with ``PHLO_NOTEBOOK_GIT_COMMIT=1``. Only notebook files and dbt models
promoted from notebooks can be committed, only those paths go into the commit
(anything else already staged is left alone), and never on a protected branch
or a detached HEAD. The commit's author is the signed-in person; the committer
and any signing follow the repository's own git configuration.
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from phlo_api import notebook_files as files

PROMOTED_DIR = "workflows/transforms/dbt/models/notebooks"
_PROMOTED = re.compile(
    r"^workflows/transforms/dbt/models/notebooks/[a-z_][a-z0-9_]{0,63}\.(sql|yml)$"
)


class GitUnavailable(RuntimeError):
    """The project is not a git checkout, or git is missing."""


class GitRefused(ValueError):
    """The commit is not allowed: protected branch, detached HEAD or a disallowed path."""


@dataclass(frozen=True)
class GitStatus:
    branch: str | None
    protected: bool
    changed: list[str]


async def _git(project: Path, *args: str, timeout: float = 30) -> str:
    try:
        process = await asyncio.create_subprocess_exec(
            "git",
            "-C",
            str(project),
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except FileNotFoundError as exc:
        raise GitUnavailable("git is not installed on the API host.") from exc
    try:
        out, err = await asyncio.wait_for(process.communicate(), timeout)
    except TimeoutError as exc:
        process.kill()
        raise GitUnavailable("git did not answer in time.") from exc
    if process.returncode != 0:
        message = err.decode(errors="replace").strip().splitlines()
        raise GitUnavailable(message[-1] if message else "git failed.")
    return out.decode(errors="replace")


def allowed_path(path: str) -> str:
    """A project-relative path that may be committed from Observatory."""
    clean = PurePosixPath(path).as_posix()
    if clean.startswith(f"{files.NOTEBOOK_DIR}/"):
        files.resolve_notebook_path(Path("/"), clean[len(files.NOTEBOOK_DIR) + 1 :])
        return clean
    if _PROMOTED.fullmatch(clean):
        return clean
    raise GitRefused(f"{path} cannot be committed from Observatory.")


async def status(project: Path, protected: set[str]) -> GitStatus:
    await _git(project, "rev-parse", "--is-inside-work-tree")
    branch = (await _git(project, "branch", "--show-current")).strip() or None
    porcelain = await _git(
        project,
        "status",
        "--porcelain=v1",
        "-z",
        "--untracked-files=all",
        "--",
        files.NOTEBOOK_DIR,
        PROMOTED_DIR,
    )
    prefix = (await _git(project, "rev-parse", "--show-prefix")).strip()
    changed = []
    for entry in porcelain.split("\0"):
        if len(entry) > 3:
            path = entry[3:]
            if prefix and path.startswith(prefix):
                path = path[len(prefix) :]
            changed.append(path)
    return GitStatus(
        branch=branch, protected=branch is None or branch in protected, changed=sorted(changed)
    )


async def commit(
    project: Path,
    paths: list[str],
    message: str,
    *,
    author: str,
    protected: set[str],
) -> tuple[str, str]:
    """Commit exactly ``paths`` and return ``(branch, commit sha)``."""
    if not paths:
        raise GitRefused("Choose at least one file to commit.")
    targets = sorted({allowed_path(path) for path in paths})
    current = await status(project, protected)
    if current.branch is None:
        raise GitRefused("The project is on a detached HEAD. Check out a branch first.")
    if current.protected:
        raise GitRefused(
            f"Notebooks are not committed to {current.branch} from Observatory. "
            "Switch the project to a working branch."
        )
    unchanged = [path for path in targets if path not in current.changed]
    if unchanged:
        raise GitRefused(f"No changes to commit in {', '.join(unchanged)}.")
    text = message.strip()
    if not text or len(text) > 2000:
        raise GitRefused("Write a commit message.")
    await _git(project, "add", "-A", "--", *targets)
    await _git(project, "commit", "--only", f"--author={author}", "-m", text, "--", *targets)
    sha = (await _git(project, "rev-parse", "HEAD")).strip()
    return current.branch, sha
