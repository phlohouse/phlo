"""Resolve the Dagster endpoint for project-policy WAP launches.

An explicit ``dagster_url`` is treated as an intentionally remote GraphQL
endpoint; otherwise the local Dagster service is resolved through the same
host and port resolution as every other Phlo service, so WAP never bypasses
shared network configuration.
"""

from __future__ import annotations

from phlo.config.network import resolve_host
from phlo.config_schema import WapConfig

from phlo_dagster.settings import get_settings


def resolve_wap_dagster_url(config: WapConfig) -> str:
    """Return an explicit remote endpoint or resolve the local Dagster service.

    Local WAP must follow the same host and port resolution as other Phlo
    services.  ``dagster_url`` is therefore reserved for an intentionally
    remote GraphQL endpoint.
    """
    if config.dagster_url:
        return config.dagster_url
    host, port = resolve_host(
        "dagster",
        get_settings().dagster_port,
        port_env_var="DAGSTER_PORT",
    )
    return f"http://{host}:{port}/graphql"
