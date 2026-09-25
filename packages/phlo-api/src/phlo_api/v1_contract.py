"""Typed /api/v1 phase-1 wire contracts."""

from __future__ import annotations

from typing import Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator


Environment = Literal["prod", "staging"]
ServiceStatus = Literal["healthy", "degraded", "unhealthy", "unknown", "unavailable"]
RunStatus = Literal[
    "NOT_STARTED",
    "MANAGED",
    "QUEUED",
    "STARTING",
    "STARTED",
    "SUCCESS",
    "FAILURE",
    "CANCELING",
    "CANCELED",
]
ReadPermission = Literal["service.read", "run.read"]


class WireModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class EnvironmentSelection(WireModel):
    """Required query selection for an environment-sensitive resource."""

    env: Environment


class EnvironmentTarget(WireModel):
    """Operator-configured target, never supplied by the request caller."""

    dagster_location: str = Field(min_length=1)
    nessie_ref: str = Field(min_length=1)


class MeResponse(WireModel):
    subject: str = Field(min_length=1)
    principal_type: Literal["user", "service", "platform"]
    email: str | None
    roles: list[str]
    permissions: dict[Environment, list[ReadPermission]]


class EnvironmentAvailability(WireModel):
    env: Environment
    status: Literal["available", "unavailable"]


class EnvironmentsResponse(WireModel):
    items: list[EnvironmentAvailability]


class ServiceSnapshot(WireModel):
    id: str = Field(min_length=1)
    status: ServiceStatus
    observed_at: AwareDatetime | None
    response_time_seconds: float | None = Field(ge=0)

    @model_validator(mode="after")
    def require_observation_for_measured_status(self) -> ServiceSnapshot:
        if self.status in {"healthy", "degraded", "unhealthy"} and self.observed_at is None:
            raise ValueError("Measured service status requires observed_at")
        return self


class ServicesResponse(WireModel):
    env: Environment
    items: list[ServiceSnapshot]
    next_cursor: str | None


class RunStatusEvent(WireModel):
    env: Environment
    run_id: str = Field(min_length=1)
    status: RunStatus
    observed_at: AwareDatetime
