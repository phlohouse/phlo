"""Process-only Pandera schema discovery settings."""

from typing import Annotated

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class PanderaSettings(BaseSettings):
    """Read schema paths while retaining project-local discovery when absent or blank."""

    model_config = SettingsConfigDict(case_sensitive=True, extra="ignore", env_file=None)

    schema_search_paths: Annotated[list[str] | None, NoDecode] = Field(
        None,
        validation_alias="PHLO_SCHEMA_SEARCH_PATHS",
        description="Comma-separated paths, stripped with blanks removed. Missing/empty falls back to PHLO_PROJECT_PATH/examples and workflows, or local examples and workflows. A nonempty all-blank list selects no paths.",
    )

    @field_validator("schema_search_paths", mode="before")
    @classmethod
    def _paths(cls, value: object) -> object:
        if isinstance(value, str):
            return [part.strip() for part in value.split(",") if part.strip()] if value else None
        return value


def get_settings() -> PanderaSettings:
    """Read the current process paths, without dotenv loading or caching."""
    return PanderaSettings()
