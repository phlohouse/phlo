"""Settings for the Phlo Transform provider package.

All values are optional so a project works with zero configuration once the
CLI binary is on ``PATH`` or installed into ``.phlo/bin``. Environment names
follow the ``PHLO_TRANSFORM_*`` prefix used by the CLI itself.
"""

from __future__ import annotations

from pathlib import Path

from pydantic import AliasChoices, Field

from phlo.config.base import BaseConfig
from phlo.config.env import load_project_env, resolve_project_root


class TransformSettings(BaseConfig):
    """Configuration for locating and running the Phlo Transform engine."""

    binary: str | None = Field(
        default=None,
        validation_alias=AliasChoices("PHLO_TRANSFORM_BIN"),
        description="Path to the phlo-transform executable; overrides project and PATH lookup.",
    )
    root: str | None = Field(
        default=None,
        validation_alias=AliasChoices("PHLO_TRANSFORM_ROOT"),
        description="Transform workspace containing phlo.toml; auto-discovered when unset.",
    )
    adapter: str | None = Field(
        default=None,
        validation_alias=AliasChoices("PHLO_TRANSFORM_ADAPTER"),
        description="Execution adapter (trino or duckdb); inferred from the query engine when unset.",
    )
    duckdb_path: str | None = Field(
        default=None,
        validation_alias=AliasChoices("PHLO_TRANSFORM_DUCKDB_PATH"),
        description="DuckDB database file used when the adapter is duckdb.",
    )
    trino_endpoint: str | None = Field(
        default=None,
        validation_alias=AliasChoices("PHLO_TRANSFORM_TRINO_ENDPOINT"),
        description="Trino HTTP endpoint; defaults to the resolved query engine.",
    )
    trino_user: str | None = Field(
        default=None,
        validation_alias=AliasChoices("PHLO_TRANSFORM_TRINO_USER"),
        description="Trino user name.",
    )
    trino_password: str | None = Field(
        default=None,
        validation_alias=AliasChoices("PHLO_TRANSFORM_TRINO_PASSWORD"),
        description="Trino password; prefer the environment over checked-in files.",
    )
    trino_catalog: str | None = Field(
        default=None,
        validation_alias=AliasChoices("PHLO_TRANSFORM_TRINO_CATALOG"),
        description="Trino catalog for model targets; defaults to the query engine catalog.",
    )
    trino_schema: str | None = Field(
        default=None,
        validation_alias=AliasChoices("PHLO_TRANSFORM_TRINO_SCHEMA"),
        description="Trino session schema.",
    )
    jobs: int = Field(
        default=4,
        validation_alias=AliasChoices("PHLO_TRANSFORM_JOBS"),
        description="Maximum concurrent model builds.",
    )
    asset_key: str = Field(
        default="leaf",
        validation_alias=AliasChoices("PHLO_TRANSFORM_ASSET_KEY"),
        description=(
            "Asset key shape for workspace models: leaf uses the last dotted "
            "name segment (dbt-compatible); logical keeps the namespace prefix."
        ),
    )
    release_version: str = Field(
        default="0.1.0",
        validation_alias=AliasChoices("PHLO_TRANSFORM_RELEASE_VERSION"),
        description="Release whose portable binary phlo transform install downloads.",
    )
    timeout_seconds: float = Field(
        default=900.0,
        validation_alias=AliasChoices("PHLO_TRANSFORM_TIMEOUT_SECONDS"),
        description="Upper bound for one phlo-transform command.",
    )

    @property
    def asset_key_mode(self) -> str:
        """Return the validated asset-key mode."""

        mode = self.asset_key.strip().lower()
        if mode not in {"leaf", "logical"}:
            raise ValueError(
                f"PHLO_TRANSFORM_ASSET_KEY must be 'leaf' or 'logical', got '{self.asset_key}'"
            )
        return mode


_ENV_KEYS = (
    "PHLO_TRANSFORM_BIN",
    "PHLO_TRANSFORM_ROOT",
    "PHLO_TRANSFORM_ADAPTER",
    "PHLO_TRANSFORM_DUCKDB_PATH",
    "PHLO_TRANSFORM_TRINO_ENDPOINT",
    "PHLO_TRANSFORM_TRINO_USER",
    "PHLO_TRANSFORM_TRINO_PASSWORD",
    "PHLO_TRANSFORM_TRINO_CATALOG",
    "PHLO_TRANSFORM_TRINO_SCHEMA",
    "PHLO_TRANSFORM_JOBS",
    "PHLO_TRANSFORM_ASSET_KEY",
    "PHLO_TRANSFORM_RELEASE_VERSION",
    "PHLO_TRANSFORM_TIMEOUT_SECONDS",
)


def get_settings(project_root: Path | str | None = None) -> TransformSettings:
    """Return provider settings for a project root.

    ``phlo.yaml`` ``env:`` values are part of the project environment layered
    over ``.phlo/.env`` files, so they are read here too; a project can point
    at its workspace without exporting a shell variable.
    """

    root = resolve_project_root(project_root)
    env = load_project_env(root)
    values = {key: env[key] for key in _ENV_KEYS if env.get(key)}
    return TransformSettings.model_validate(values)


__all__ = ["TransformSettings", "get_settings"]
