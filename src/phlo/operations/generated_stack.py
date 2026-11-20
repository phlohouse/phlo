"""Reviewable generated-file upgrades, separate from provider data migrations.

Only files declared by the installed generators are candidates. A recorded
generation baseline proves ownership; unknown or locally edited files conflict
rather than being adopted implicitly. No environment file or data is generated.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from importlib.metadata import distributions
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Literal

from pydantic import BaseModel, ConfigDict

from phlo.operations.api_runtime import ApiRuntimeDirectory

BASELINE = "upgrade-baseline.json"
# Provider commands that rewrite a generated file record the rewrite here, so a
# later upgrade plan still recognises that file as unmodified generated output.
REWRITES = "upgrade-rewrites.json"


def installed_distributions() -> dict[str, str]:
    return dict(
        sorted(
            (dist.metadata["Name"], dist.version)
            for dist in distributions()
            if dist.metadata["Name"].lower() == "phlo"
            or dist.metadata["Name"].lower().startswith("phlo-")
        )
    )


def digest(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def checked_path(root: Path, relative: str) -> Path:
    """Reject ambiguous, escaping and symlinked paths before reading or writing."""
    path = PurePosixPath(relative)
    if (
        not relative
        or path.is_absolute()
        or PureWindowsPath(relative).drive
        or ".." in path.parts
        or "\\" in relative
        or path.as_posix() != relative
        or relative == "."
    ):
        raise ValueError("Upgrade paths must be normalized relative file paths")
    target = root / relative
    if root.is_symlink() or any(p.is_symlink() for p in (target, *target.parents)):
        raise ValueError(f"Upgrade path cannot use symlinks: {relative}")
    if not target.resolve().is_relative_to(root.resolve()):
        raise ValueError(f"Upgrade path escapes project: {relative}")
    if target.exists() and not target.is_file():
        raise ValueError(f"Upgrade destination is not a regular file: {relative}")
    return target


class GeneratedFileChange(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    path: str
    before: str | None
    after: str
    mode: int
    state: Literal["unchanged", "create", "replace", "conflict"]


class GeneratedStackPlan(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: Literal[1] = 1
    project: str
    source_distributions: dict[str, str]
    distributions: dict[str, str]
    images: dict[str, tuple[str | None, str | None]]
    inputs: dict[str, str | None]
    candidate_files: dict[str, str]
    files: tuple[GeneratedFileChange, ...]
    new_companions: tuple[str, ...]
    restart_order: tuple[str, ...]
    migrations: tuple[str, ...]
    api_runtime_ownership: tuple[ApiRuntimeDirectory, ...] = ()

    def token(self) -> str:
        return digest(self.model_dump_json().encode())


def file_digest(root: Path, relative: str) -> str | None:
    path = checked_path(root, relative)
    return digest(path.read_bytes()) if path.exists() else None


def generation_baseline(root: Path, files: list[str]) -> str:
    """Record hashes, not credentials or copies of generated content."""
    return (
        json.dumps(
            {
                "files": {name: file_digest(root, name) for name in sorted(files)},
                "distributions": installed_distributions(),
            },
            indent=2,
        )
        + "\n"
    )


def rewrite_record(phlo_dir: Path, name: str, before: bytes, after: bytes, by: str) -> str:
    """Return the rewrite record with ``by``'s rewrite of ``name`` appended.

    Only digests are stored. The rewrite counts during planning only when the
    baseline digest equals ``before``, so a file the operator had already
    edited still conflicts. The caller writes the result to ``REWRITES``.
    """
    path = phlo_dir / REWRITES
    recorded = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
    rewrites = recorded.setdefault("rewrites", [])
    rewrites.append({"path": name, "from": digest(before), "to": digest(after), "by": by})
    return json.dumps(recorded, indent=2) + "\n"


def apply_rewrites(root: Path, baseline: Mapping[str, str | None]) -> dict[str, str | None]:
    """Follow recorded provider rewrites from each baseline digest, in recorded order."""
    path = checked_path(root, f".phlo/{REWRITES}")
    if not path.exists():
        return dict(baseline)
    rewrites = json.loads(path.read_text(encoding="utf-8")).get("rewrites", [])
    if not isinstance(rewrites, list):
        raise ValueError(f".phlo/{REWRITES} is not a list of rewrites")
    followed = dict(baseline)
    for rewrite in rewrites:
        if not isinstance(rewrite, dict) or not {"path", "from", "to"} <= set(rewrite):
            raise ValueError(f".phlo/{REWRITES} has a malformed rewrite")
        name = rewrite["path"]
        if name in followed and followed[name] == rewrite["from"]:
            followed[name] = rewrite["to"]
    return followed


def plan_generated_stack(
    *,
    root: Path,
    candidate: dict[str, tuple[bytes, int]],
    baseline: dict[str, str | None],
    source_distributions: dict[str, str],
    distributions: dict[str, str],
    images: dict[str, tuple[str | None, str | None]],
    inputs: list[str],
    new_companions: tuple[str, ...],
    restart_order: tuple[str, ...],
    migrations: tuple[str, ...],
) -> GeneratedStackPlan:
    changes = []
    for name, (content, mode) in sorted(candidate.items()):
        before, after = file_digest(root, name), digest(content)
        state = (
            "unchanged"
            if before == after
            else "create"
            if before is None
            else "replace"
            if before == baseline.get(name)
            else "conflict"
        )
        changes.append(
            GeneratedFileChange(path=name, before=before, after=after, mode=mode, state=state)
        )
    return GeneratedStackPlan(
        project=str(root.resolve()),
        source_distributions=source_distributions,
        distributions=distributions,
        images=images,
        inputs={name: file_digest(root, name) for name in sorted(set(inputs))},
        candidate_files={
            name: content.decode("utf-8") for name, (content, _) in sorted(candidate.items())
        },
        files=tuple(changes),
        new_companions=new_companions,
        restart_order=restart_order,
        migrations=migrations,
    )
