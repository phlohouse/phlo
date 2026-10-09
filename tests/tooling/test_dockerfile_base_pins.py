"""Every Dockerfile base image is immutable: ``FROM image:tag@sha256:<digest>``."""

import re
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
_FROM = re.compile(r"^\s*FROM\s+(?:--platform=\S+\s+)?(?P<image>\S+)", re.IGNORECASE)
_PINNED = re.compile(r"^[^@\s]+:[^@\s]+@sha256:[0-9a-f]{64}$")


def _dockerfiles() -> list[Path]:
    tracked = subprocess.run(
        ["git", "ls-files", "*Dockerfile", "*Dockerfile.*", "*.Dockerfile"],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.split()
    return [REPO_ROOT / path for path in tracked]


def unpinned_bases(text: str) -> list[str]:
    """Return external base images that are not ``tag@sha256`` pinned."""
    stages: set[str] = set()
    unpinned = []
    for line in text.splitlines():
        match = _FROM.match(line)
        if not match:
            continue
        image = match["image"]
        words = line.split()
        alias = words[-1].lower() if len(words) >= 2 and words[-2].upper() == "AS" else None
        if image.lower() not in stages and image.lower() != "scratch" and not _PINNED.match(image):
            unpinned.append(image)
        if alias:
            stages.add(alias)
    return unpinned


def test_repository_has_dockerfiles() -> None:
    assert _dockerfiles()


@pytest.mark.parametrize(
    "dockerfile", _dockerfiles(), ids=lambda path: str(path.relative_to(REPO_ROOT))
)
def test_dockerfile_bases_are_digest_pinned(dockerfile: Path) -> None:
    assert unpinned_bases(dockerfile.read_text(encoding="utf-8")) == []


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("FROM python:3.12-slim AS build\n", ["python:3.12-slim"]),
        ("FROM python@sha256:" + "a" * 64 + "\n", ["python@sha256:" + "a" * 64]),
        ("FROM python:3.12-slim@sha256:" + "a" * 64 + " AS build\nFROM build\n", []),
        ("FROM --platform=$BUILDPLATFORM node:24-alpine\n", ["node:24-alpine"]),
        ("FROM scratch\n", []),
    ],
)
def test_unpinned_base_detection(text: str, expected: list[str]) -> None:
    assert unpinned_bases(text) == expected
