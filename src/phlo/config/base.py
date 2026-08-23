"""Base configuration classes for Phlo.

This module provides the foundational configuration infrastructure used across
all Phlo components. It defines the base configuration class with common
settings for environment variable loading and validation.
"""

from pydantic_settings import BaseSettings, SettingsConfigDict

from phlo.config.env import project_env_files, resolve_project_root, use_project_root


class BaseConfig(BaseSettings):
    """Base configuration class with common settings for all config domains.

    Standardized foundation for all Phlo configuration classes: loads
    environment variables from `.phlo/.env` and `.phlo/.env.local` with
    case-insensitive matching, ignoring extra fields. Project env files
    are selected per instance via ``_project_root``.

    Example:
        ```python
        from phlo.config.base import BaseConfig
        from pydantic import Field

        class DatabaseConfig(BaseConfig):
            postgres_host: str = Field(default="localhost")
            postgres_port: int = Field(default=5432)
        ```

    """

    model_config = SettingsConfigDict(
        env_file=None,
        case_sensitive=False,
        extra="ignore",
    )

    def __init__(self, _project_root=None, **values):
        """Load settings from the selected project's generated environment files.

        ``_project_root`` is intentionally an initialization-only argument so
        callers and tests can select a project without changing process-wide
        working-directory state. When omitted, ``PHLO_PROJECT_PATH`` is used,
        followed by the current working directory for CLI compatibility.
        """
        project_root = resolve_project_root(_project_root)
        with use_project_root(project_root):
            super().__init__(_env_file=project_env_files(project_root), **values)
