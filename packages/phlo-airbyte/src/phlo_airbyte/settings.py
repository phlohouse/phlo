"""Airbyte settings resolved from the project environment."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from phlo.config.base import BaseConfig
from phlo.config.cache import project_root_cached
from phlo.config.network import resolve_host
from pydantic import Field


class AirbyteSettings(BaseConfig):
    """Settings for the Airbyte control-plane integration."""

    airbyte_host: str = Field(default="airbyte-server", description="Airbyte API host")
    airbyte_port: int = Field(default=8001, description="Airbyte server API port")
    airbyte_workspace_id: str | None = Field(
        default=None, description="Airbyte workspace id for connection lookups"
    )
    airbyte_client_id: str | None = Field(
        default=None, description="Airbyte API client id for token auth"
    )
    airbyte_client_secret: str | None = Field(
        default=None,
        description="Airbyte API client secret for token auth",
    )
    airbyte_poll_interval_seconds: int = Field(
        default=10, description="Seconds between sync job status polls"
    )
    airbyte_sync_timeout_seconds: int = Field(
        default=3600, description="Maximum seconds to wait for one sync to reach a terminal state"
    )

    def model_post_init(self, __context: Any) -> None:
        host, port = resolve_host(self.airbyte_host, self.airbyte_port, port_env_var="AIRBYTE_PORT")
        object.__setattr__(self, "airbyte_host", host)
        object.__setattr__(self, "airbyte_port", port)

    def airbyte_api_uri(self) -> str:
        """Return the Airbyte server API base URI."""
        return f"http://{self.airbyte_host}:{self.airbyte_port}"


@project_root_cached
def get_settings(project_root: Path) -> AirbyteSettings:
    """Return cached Airbyte settings for the selected project root."""
    return AirbyteSettings()
