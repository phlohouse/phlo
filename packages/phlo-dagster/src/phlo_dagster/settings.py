"""Configuration settings for Dagster orchestration.

This module defines the DagsterSettings class for configuring the
Dagster adapter behavior. Settings control executor selection,
workflow discovery paths, and service port configuration.

Configuration Sources:
    - Environment variables (PHLO_* prefix)
    - .phlo/.env and .phlo/.env.local files
    - Default values defined in DagsterSettings

Key Settings:
    - dagster_port: Webserver port (default: 10006)
    - workflows_path: User workflow discovery path (default: workflows)
    - phlo_force_in_process_executor: Force single-process execution
    - phlo_force_multiprocess_executor: Force multiprocess execution
    - phlo_host_platform: Override platform detection

Executor Selection:
    The module implements platform-aware executor selection to handle
    Docker Desktop/Colima on macOS where multiprocessing can cause
    DuckDB crashes. Priority:
    1. PHLO_FORCE_IN_PROCESS_EXECUTOR
    2. PHLO_FORCE_MULTIPROCESS_EXECUTOR
    3. PHLO_HOST_PLATFORM detection
    4. platform.system() fallback

Example:
    Accessing settings::

        from phlo_dagster.settings import get_settings

        settings = get_settings()
        port = settings.dagster_port

    Environment configuration::

        PHLO_DAGSTER_PORT=3000
        PHLO_WORKFLOWS_PATH=./custom_workflows


        Settings for the phlo_dagster workflows package, built on the shared phlo.config base/cache helpers.
        Loaded within phlo_dagster by framework and CLI-log code through get_settings().
"""

from __future__ import annotations

from pathlib import Path

from pydantic import AliasChoices, Field, model_validator

from phlo.config.base import BaseConfig
from phlo.config.cache import project_root_cached


class DagsterSettings(BaseConfig):
    """Dagster orchestration configuration."""

    dagster_port: int = Field(default=10006, description="Dagster webserver port")
    workflows_path: str = Field(
        default="workflows",
        validation_alias=AliasChoices("PHLO_WORKFLOWS_PATH", "WORKFLOWS_PATH", "workflows_path"),
        description="Path to user workflows directory (for external projects)",
    )
    phlo_force_in_process_executor: bool = Field(
        default=False, description="Force use of in-process executor"
    )
    phlo_force_multiprocess_executor: bool = Field(
        default=False, description="Force use of multiprocess executor"
    )
    phlo_host_platform: str | None = Field(
        default=None,
        description="Host platform for executor selection (Darwin/Linux/Windows). "
        "Auto-detected in CLI; set explicitly for daemon/webserver on macOS.",
    )

    @model_validator(mode="after")
    def validate_executor_flags(self) -> "DagsterSettings":
        """Reject settings where both executor force flags are set."""
        if self.phlo_force_in_process_executor and self.phlo_force_multiprocess_executor:
            raise ValueError(
                "phlo_force_in_process_executor and phlo_force_multiprocess_executor "
                "cannot both be True"
            )
        return self


@project_root_cached
def get_settings(project_root: Path) -> DagsterSettings:
    """Return cached Dagster settings."""
    return DagsterSettings()
