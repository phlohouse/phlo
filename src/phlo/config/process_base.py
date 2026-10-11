"""Process-only settings primitives used below project/dotenv resolution."""

from __future__ import annotations

from copy import copy
from typing import cast, overload

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, EnvSettingsSource, SettingsConfigDict


def process_override(
    model: type[BaseSettings], name: str, *, alias: str | None = None
) -> str | None:
    """Read an exact process key from its owning declaration, without defaults or dotenv.

    Resolved logging models also accept secondary aliases and have defaults.
    A raw projection must not apply either, or blank/missing security overrides
    would acquire logging's precedence. No second setting is declared here.
    """
    field = copy(model.model_fields[name])
    declared_alias = field.validation_alias
    primary = (
        declared_alias.choices[0] if isinstance(declared_alias, AliasChoices) else declared_alias
    )
    field.validation_alias = alias or (primary if isinstance(primary, str) else name.upper())
    return cast(
        str | None, EnvSettingsSource(model, case_sensitive=True).get_field_value(field, name)[0]
    )


class ProcessOverrides(BaseSettings):
    """Read case-sensitive process overrides while preserving missing and empty values."""

    model_config = SettingsConfigDict(case_sensitive=True, extra="ignore", env_file=None)

    def __getitem__(self, name: str) -> str:
        field = type(self).model_fields.get(name.lower())
        if field is not None and field.validation_alias != name:
            raise KeyError(name)
        value: str | None = getattr(self, name.lower(), None)
        if value is None:
            raise KeyError(name)
        if not isinstance(value, str):
            raise TypeError(f"Use the typed {name.lower()} field directly")
        return value

    def __contains__(self, name: str) -> bool:
        try:
            self[name]
        except KeyError:
            return False
        return True

    @overload
    def get(self, name: str) -> str | None: ...

    @overload
    def get[T](self, name: str, default: T) -> str | T: ...

    def get[T](self, name: str, default: T | None = None) -> str | T | None:
        """Resolve an override without replacing an explicitly empty string."""
        try:
            return self[name]
        except KeyError:
            return default


class ProjectRootProcessSettings(ProcessOverrides):
    """The project-root override required before loading project environment files."""

    phlo_project_path: str | None = Field(
        None,
        validation_alias="PHLO_PROJECT_PATH",
        description="Process project-root override. Otherwise use the caller's root or working directory; API deployment defaults may use /app/project.",
    )
