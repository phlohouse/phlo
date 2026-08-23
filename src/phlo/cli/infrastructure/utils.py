"""Utility functions for CLI services that can be safely imported by plugins.

Deliberately dependency-light (yaml + logging only). Missing or malformed
env/config files never raise: they log a warning and fall back to defaults
derived from the current directory name.
"""

from collections.abc import Mapping
from pathlib import Path

import yaml

from phlo.logging import get_logger

logger = get_logger(__name__)


def parse_env_file(path: Path, *, strip_quotes: bool = False) -> dict[str, str]:
    """Parse a .env file into a dict of key=value pairs."""
    if not path.exists():
        return {}
    values: dict[str, str] = {}
    try:
        for line in path.read_text().splitlines():
            trimmed = line.strip()
            if not trimmed or trimmed.startswith("#") or "=" not in trimmed:
                continue
            key, value = trimmed.split("=", 1)
            if strip_quotes:
                value = value.strip()
                if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
                    value = value[1:-1]
            values[key] = value
    except OSError:
        logger.warning("env_file_read_failed", path=str(path), exc_info=True)
        return {}
    return values


def get_project_config() -> dict:
    """Load phlo.yaml configuration."""
    config_path = Path.cwd() / "phlo.yaml"
    if config_path.exists():
        try:
            with config_path.open() as f:
                config = yaml.safe_load(f) or {}
                if isinstance(config, Mapping):
                    return dict(config)
                logger.warning("project_config_invalid_type", path=str(config_path))
        except (OSError, yaml.YAMLError):
            logger.warning("project_config_load_failed", path=str(config_path), exc_info=True)

    fallback_name = Path.cwd().name.lower().replace(" ", "-").replace("_", "-")
    logger.debug("project_config_fallback_used", project_name=fallback_name)
    return {
        "name": fallback_name,
        "description": "Phlo data lakehouse",
    }


def get_project_name() -> str:
    """Get the project name for Docker Compose."""
    config = get_project_config()
    return config.get("name", Path.cwd().name.lower().replace(" ", "-").replace("_", "-"))
