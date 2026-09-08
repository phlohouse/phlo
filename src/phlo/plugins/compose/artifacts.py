"""Path policy shared by runtime artifact export and regeneration."""

from pathlib import Path, PureWindowsPath

_PRIVATE_NAMES = {
    "volumes",
    "logs",
    "audit",
    "contracts",
    "wheelhouse",
    "__pycache__",
    "docker-compose.yml",
    "compose.local.yaml",
    "pyproject.toml",
    "uv.lock",
}


def shared_artifact_files(root: Path, relative: str) -> list[Path]:
    """Return explicit regular artifact files without following links or state paths."""
    path = Path(relative)
    windows_path = PureWindowsPath(relative)
    if path.is_absolute() or windows_path.drive or ".." in path.parts or ".." in windows_path.parts:
        raise ValueError(f"Artifact path must be relative: {relative}")
    source = root / path
    if not source.exists():
        raise ValueError(f"Missing artifact: {relative}")
    candidates = [source, *source.rglob("*")] if source.is_dir() else [source]
    files = []
    for candidate in candidates:
        parts = candidate.relative_to(root).parts
        if any(part.startswith(".") or part in _PRIVATE_NAMES for part in parts):
            raise ValueError(f"Private/runtime path cannot be shared: {relative}")
        if any((root / Path(*parts[:index])).is_symlink() for index in range(len(parts) + 1)):
            raise ValueError(f"Symlink cannot be shared: {relative}")
        if candidate.is_file():
            files.append(candidate)
    return files
