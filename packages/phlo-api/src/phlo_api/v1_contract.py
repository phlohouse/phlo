"""Typed /api/v1 phase-1 wire contracts."""

from __future__ import annotations

from typing import Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from phlo.config_schema import EnvironmentId as Environment
from phlo.config_schema import EnvironmentTarget as EnvironmentTarget

ServiceStatus = Literal["healthy", "degraded", "unhealthy", "unknown", "unavailable", "inactive"]
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
    model_config = ConfigDict(
        extra="forbid", strict=True, json_schema_serialization_defaults_required=True
    )


class EnvironmentSelection(WireModel):
    """Required query selection for an environment-sensitive resource."""

    env: Environment


class ProviderActionResult(WireModel):
    """Required acceptance decision with optional provider-specific outcome evidence."""

    model_config = ConfigDict(extra="allow")
    accepted: bool
    run_id: str | None = None
    message: str | None = None


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
    default_environment: Environment | None = None
    promotion_source_environment: Environment | None = None


class ServiceSnapshot(WireModel):
    id: str = Field(min_length=1)
    status: ServiceStatus
    observed_at: AwareDatetime | None
    response_time_seconds: float | None = Field(ge=0)
    runtime_state: Literal["running", "starting", "stopped", "unknown"] = "unknown"
    definition_state: Literal["configured", "available", "disabled", "unknown"] = "unknown"
    reason: str | None = None
    health_origin: str | None = None

    @model_validator(mode="after")
    def require_observation_for_measured_status(self) -> ServiceSnapshot:
        if self.status in {"healthy", "degraded", "unhealthy"} and self.observed_at is None:
            raise ValueError("Measured service status requires observed_at")
        return self


class ServicesResponse(WireModel):
    env: Environment
    dagster_location: str | None = None
    nessie_ref: str | None = None
    items: list[ServiceSnapshot]
    next_cursor: str | None


class RunStatusEvent(WireModel):
    env: Environment
    run_id: str = Field(min_length=1)
    status: RunStatus
    observed_at: AwareDatetime
