"""Process telemetry switches, including supplied run-environment snapshots."""

from collections.abc import Mapping

from pydantic import Field, PrivateAttr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from phlo.config.process import process_override


class TelemetryProcessSettings(BaseSettings):
    """Read fresh telemetry controls while retaining SDK auto-selection when unset."""

    model_config = SettingsConfigDict(case_sensitive=True, extra="ignore", env_file=None)
    enabled: bool | None = Field(
        None,
        validation_alias="PHLO_OBSERVE_ENABLED",
        description="Telemetry opt-in/out. Unset keeps auto-selection from OBSERVE drains/endpoint; stripped lowercase 1/true/yes/on enable; other strings disable.",
    )
    pretty: bool = Field(
        False,
        validation_alias="PHLO_OBSERVE_PRETTY",
        description="Pretty drain request; stripped lowercase 1/true/yes/on enable, otherwise disabled.",
    )
    pretty_verbose: bool = Field(
        False,
        validation_alias="PHLO_OBSERVE_PRETTY_VERBOSE",
        description="Verbose pretty drain request; stripped lowercase 1/true/yes/on enable, otherwise disabled.",
    )
    _log_level: str | None = PrivateAttr(None)

    @property
    def log_level(self) -> str:
        """Read the single core logging declaration without resolved defaults."""
        from phlo.config.settings import Settings

        return (
            self._log_level
            if self._log_level is not None
            else process_override(Settings, "phlo_log_level") or ""
        )

    @field_validator("enabled", "pretty", "pretty_verbose", mode="before")
    @classmethod
    def _flag(cls, value: object) -> object:
        return (
            value.strip().lower() in {"1", "true", "yes", "on"} if isinstance(value, str) else value
        )

    @classmethod
    def from_environment(cls, env: Mapping[str, str] | None = None) -> "TelemetryProcessSettings":
        """Use a supplied run snapshot exclusively, or read the process afresh."""
        if env is None:
            return cls()
        values = {
            "PHLO_OBSERVE_ENABLED": env.get("PHLO_OBSERVE_ENABLED"),
            "PHLO_OBSERVE_PRETTY": env.get("PHLO_OBSERVE_PRETTY", False),
            "PHLO_OBSERVE_PRETTY_VERBOSE": env.get("PHLO_OBSERVE_PRETTY_VERBOSE", False),
        }
        settings = cls.model_validate(values)
        settings._log_level = env.get("PHLO_LOG_LEVEL", "")
        return settings
