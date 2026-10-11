"""Polaris settings resolved from the project environment."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from phlo.config.base import BaseConfig
from phlo.config.cache import project_root_cached
from phlo.config.env import resolve_project_root
from phlo.config.network import resolve_host
from pydantic import Field, PrivateAttr


class PolarisSettings(BaseConfig):
    """Settings for the Phlo Polaris catalog service."""

    _credential_path: Path = PrivateAttr(
        default_factory=lambda: resolve_project_root() / ".phlo" / "polaris-principals.json"
    )
    polaris_host: str = Field(default="polaris", description="Polaris API host")
    polaris_port: int = Field(default=8181, description="Polaris API port")
    polaris_catalog: str = Field(
        default="phlo", description="Polaris catalog name backing the Phlo warehouse"
    )
    polaris_root_credentials: str = Field(
        default="",
        description="Bootstrap principal credentials as client_id:client_secret",
    )
    polaris_writer_client_id: str = Field(
        default="phlo_writer", description="Writer principal client id"
    )
    polaris_writer_client_secret: str = Field(
        default="", description="Writer principal client secret"
    )
    polaris_reader_client_id: str = Field(
        default="phlo_reader", description="Reader principal client id"
    )
    polaris_reader_client_secret: str = Field(
        default="", description="Reader principal client secret"
    )
    polaris_query_engine: str | None = Field(
        default=None, description="Optional query-engine capability used by catalog scanners"
    )

    def model_post_init(self, __context: Any) -> None:
        host, port = resolve_host(self.polaris_host, self.polaris_port, port_env_var="POLARIS_PORT")
        object.__setattr__(self, "polaris_host", host)
        object.__setattr__(self, "polaris_port", port)

    def polaris_api_uri(self) -> str:
        """Return the Polaris management API base URI."""
        return f"http://{self.polaris_host}:{self.polaris_port}"

    def polaris_rest_catalog_uri(self) -> str:
        """Return the Iceberg REST catalog endpoint exposed by Polaris."""
        return f"{self.polaris_api_uri()}/api/catalog"

    def oauth_token_uri(self) -> str:
        """Return the OAuth2 token endpoint used by REST catalog clients."""
        return f"{self.polaris_rest_catalog_uri()}/v1/oauth/tokens"

    def writer_credential(self) -> str:
        """Return the writer principal credential as client_id:client_secret."""
        return self._principal_credential(
            self.polaris_writer_client_id, self.polaris_writer_client_secret
        )

    def reader_credential(self) -> str:
        """Return the reader principal credential as client_id:client_secret."""
        return self._principal_credential(
            self.polaris_reader_client_id, self.polaris_reader_client_secret
        )

    def _principal_credential(self, name: str, secret: str) -> str:
        path = self._credential_path
        if path.exists():
            stored = json.loads(path.read_text(encoding="utf-8"))
            credential = stored.get(name)
            if credential:
                client_id, separator, client_secret = str(credential).partition(":")
                if client_id and separator and client_secret:
                    return str(credential)
                raise ValueError(f"Invalid saved Polaris credential for {name}")
        if not name or not secret:
            raise ValueError(
                "Polaris credentials are missing; run phlo services start to bootstrap"
            )
        return f"{name}:{secret}"


@project_root_cached
def get_settings(project_root: Path) -> PolarisSettings:
    """Return cached Polaris settings for the selected project root."""
    return PolarisSettings()
