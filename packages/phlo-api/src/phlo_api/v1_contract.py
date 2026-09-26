"""Representative /api/v1 wire models; no v1 routes are mounted yet."""

from __future__ import annotations

from typing import Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator


Environment = Literal["prod", "staging"]
ServiceStatus = Literal["healthy", "degraded", "unhealthy", "unknown", "unavailable"]


class WireModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class EnvironmentSelection(WireModel):
    """Required query selection for an environment-sensitive resource."""

    env: Environment


class EnvironmentTarget(WireModel):
    """Operator-configured target, never supplied by the request caller."""

    dagster_location: str = Field(min_length=1)
    nessie_ref: str = Field(min_length=1)


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
