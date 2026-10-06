"""Check relative Markdown links in tracked Markdown files."""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parents[1]
LINK = re.compile(r"(?<!!)\[[^\]]*\]\((<[^>]+>|[^)]+)\)")
REFERENCE = re.compile(r"^\s{0,3}\[[^\]]+\]:\s*(\S+)", re.MULTILINE)
HEADING = re.compile(r"^#{1,6}\s+(.+?)\s*#*\s*$", re.MULTILINE)
FENCE = re.compile(r"^[ \t]*(```|~~~)[^\n]*\n.*?^[ \t]*\1[ \t]*$", re.MULTILINE | re.DOTALL)


def slug(heading: str) -> str:
    heading = re.sub(r"`([^`]*)`", r"\1", heading)
    heading = re.sub(r"\[([^]]+)\]\([^)]*\)", r"\1", heading)
    heading = re.sub(r"<[^>]+>", "", heading).lower()
    heading = re.sub(r"[^\w\- ]", "", heading)
    return re.sub(r"\s+", "-", heading.strip())


def markdown_files() -> list[Path]:
    result = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z", "--", "*.md"],
        cwd=ROOT,
        check=True,
        capture_output=True,
    )
    return [ROOT / path.decode() for path in result.stdout.split(b"\0") if path]


def targets(text: str) -> list[str]:
    text = FENCE.sub("", text)
    return [match.group(1).strip("<>") for match in LINK.finditer(text)] + REFERENCE.findall(text)


def check_link(source: Path, target: str) -> str | None:
    parts = urlsplit(target)
    if parts.scheme or parts.netloc or not parts.path and not parts.fragment:
        if parts.scheme == "file":
            return "workstation file URL is not a repository link"
        return None

    path = unquote(parts.path)
    resolved = source if not path else source.parent / path
    if path.startswith("/"):
        resolved = ROOT / path.lstrip("/")
    resolved = resolved.resolve()
    if not resolved.is_relative_to(ROOT):
        return "link resolves outside the repository"
    if resolved.is_dir():
        resolved = next(
            (
                candidate
                for candidate in (resolved / "index.md", resolved / "README.md")
                if candidate.is_file()
            ),
            resolved,
        )
    if not resolved.exists():
        return "target does not exist"
    if resolved.is_dir():
        return None
    if parts.fragment and resolved.suffix.lower() == ".md":
        content = resolved.read_text()
        anchors = [slug(heading) for heading in HEADING.findall(content)]
        if unquote(parts.fragment).lower() not in anchors:
            return f"heading #{parts.fragment} does not exist"
    return None


def main() -> int:
    failures: list[str] = []
    for source in markdown_files():
        content = source.read_text()
        for target in targets(content):
            error = check_link(source, target)
            if error:
                failures.append(f"{source.relative_to(ROOT)}: {target}: {error}")
    if failures:
        print("Broken Markdown links:", file=sys.stderr)
        print("\n".join(failures), file=sys.stderr)
        return 1
    print("All tracked Markdown links resolve.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
