"""Every Dockerfile ``FROM`` must pin the base image to an immutable digest.

``FROM image:tag`` is mutable: the tag moves as upstream republishes it, so the
bytes under a released image reference change over time and a retagged upstream
substitutes different bytes into every consumer build. Pinning
``image:tag@sha256:<digest>`` makes the base immutable and reproducible.
Renovate's dockerfile manager keeps the digest fresh (see renovate.json), so
the pin costs no drift.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
DOCKERFILES = sorted(
    path
    for path in REPO_ROOT.rglob("Dockerfile")
    if not any(
        part.startswith(".") or part == "node_modules" for part in path.relative_to(REPO_ROOT).parts
    )
)
_FROM = re.compile(r"^FROM(?:\s+--platform=\S+)?\s+(?P<ref>\S+)(?:\s+[Aa][Ss]\s+\S+)?$")
_PINNED_REF = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9._/-]*(?::[A-Za-z0-9_][A-Za-z0-9._-]{0,127})@sha256:[0-9a-f]{64}$"
)


def test_dockerfiles_are_discovered() -> None:
    """A broken glob must fail rather than silently assert nothing."""
    assert DOCKERFILES, "no Dockerfiles discovered"


@pytest.mark.parametrize(
    "dockerfile",
    DOCKERFILES,
    ids=[path.relative_to(REPO_ROOT).as_posix() for path in DOCKERFILES],
)
def test_dockerfile_base_images_are_digest_pinned(dockerfile: Path) -> None:
    unpinned = [
        match.group("ref")
        for line in dockerfile.read_text(encoding="utf-8").splitlines()
        if (match := _FROM.match(line.strip())) and not _PINNED_REF.fullmatch(match.group("ref"))
    ]
    assert unpinned == [], (
        f"{dockerfile.relative_to(REPO_ROOT)} has unpinned FROM base images: "
        f"{', '.join(unpinned)}. Pin each base as image:tag@sha256:<digest> so a "
        "retagged upstream cannot substitute different bytes; Renovate keeps "
        "the digest current."
    )
