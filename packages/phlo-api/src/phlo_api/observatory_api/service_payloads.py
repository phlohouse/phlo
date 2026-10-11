"""Validated Docker transport payloads for service read models."""

from collections.abc import Mapping
from typing import Any, Literal

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, ValidationError, field_validator


class DockerPayloadError(ValueError):
    """Docker returned a malformed container payload."""


class DockerPsContainer(BaseModel):
    """CLI ps and normalized Engine list-container contract."""

    model_config = ConfigDict(strict=True, frozen=True, populate_by_name=True)
    id: str = Field(default="", validation_alias=AliasChoices("ID", "Id", "id"))
    name: str = Field(default="", alias="Names")
    state: Literal[
        "running", "paused", "restarting", "removing", "exited", "dead", "created", "unknown"
    ] = Field(alias="State")
    status: str = Field(default="", alias="Status")
    labels: dict[str, str] = Field(default_factory=dict, alias="Labels")

    @field_validator("name", mode="before")
    @classmethod
    def engine_names(cls, value: object) -> object:
        if isinstance(value, list):
            if not all(isinstance(name, str) for name in value):
                raise ValueError("Docker names must be strings")
            name = value[0] if value else ""
            if isinstance(name, str):
                return name.lstrip("/")
        return value

    @field_validator("labels", mode="before")
    @classmethod
    def cli_labels(cls, value: Any) -> Any:
        if not isinstance(value, str):
            return value
        result: dict[str, str] = {}
        for item in value.split(","):
            if not item:
                continue
            key, separator, label = item.partition("=")
            if not separator or not key:
                raise ValueError("Docker label must be key=value")
            result[key] = label
        return result


class DockerHealthLog(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True)
    exit_code: int = Field(alias="ExitCode")


class DockerHealth(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True)
    status: Literal["healthy", "unhealthy", "starting"] | None = Field(default=None, alias="Status")
    log: list[DockerHealthLog] = Field(default_factory=list, alias="Log")


class DockerContainerState(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True)
    started_at: str | None = Field(default=None, alias="StartedAt")
    finished_at: str | None = Field(default=None, alias="FinishedAt")
    exit_code: int | None = Field(default=None, alias="ExitCode")
    oom_killed: bool | None = Field(default=None, alias="OOMKilled")
    health: DockerHealth = Field(default_factory=DockerHealth, alias="Health")


class DockerContainerConfig(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True)
    labels: dict[str, str] = Field(default_factory=dict, alias="Labels")


class DockerInspectContainer(BaseModel):
    """Only fields needed for runtime evidence and compose-project attribution."""

    model_config = ConfigDict(strict=True, frozen=True)
    restart_count: int | None = Field(default=None, alias="RestartCount", ge=0)
    state: DockerContainerState = Field(alias="State")
    config: DockerContainerConfig = Field(default_factory=DockerContainerConfig, alias="Config")


def parse_docker_payload[T: BaseModel](model: type[T], payload: object) -> T:
    """Report malformed transport data separately from Docker unavailability."""
    try:
        return model.model_validate(payload)
    except ValidationError as exc:
        raise DockerPayloadError(f"Docker returned invalid {model.__name__} data") from exc


def ps_container(value: DockerPsContainer | Mapping[str, Any]) -> DockerPsContainer:
    """Legacy mapping adapter; typed callers never reparse container fields."""
    return (
        value
        if isinstance(value, DockerPsContainer)
        else parse_docker_payload(DockerPsContainer, value)
    )
