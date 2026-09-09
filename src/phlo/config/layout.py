"""Resolve environment files for shared and legacy project layouts."""

from pathlib import Path

SHARED_LAYOUT_MARKER = "# Phlo shared layout v1"


def project_env_paths(phlo_dir: Path) -> tuple[Path, ...]:
    """Return environment layers in precedence order, including absent files."""
    return (
        phlo_dir / ".env",
        phlo_dir / ".env.local",
        phlo_dir / "overrides" / ".env",
        phlo_dir / "secrets" / ".env",
    )


def _env_path(phlo_dir: Path, directory: str, legacy: str) -> Path:
    path = phlo_dir / directory / ".env"
    marker = phlo_dir / ".gitignore"
    if path.exists() or (marker.is_file() and SHARED_LAYOUT_MARKER in marker.read_text()):
        return path
    return phlo_dir / legacy


def env_defaults_path(phlo_dir: Path) -> Path:
    """Return the defaults destination for the project's current layout."""
    return _env_path(phlo_dir, "overrides", ".env")


def env_secrets_path(phlo_dir: Path) -> Path:
    """Return the secrets destination for the project's current layout."""
    return _env_path(phlo_dir, "secrets", ".env.local")
