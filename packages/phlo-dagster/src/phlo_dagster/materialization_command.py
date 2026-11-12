"""Build container commands for Dagster asset materialization.

Forward host identity and project pretty-drain settings. Contract refresh
is left unset unless the caller explicitly enables or disables it.
"""

import platform

from phlo.cli.infrastructure.container_backend import ContainerBackend, container_host_identity
from phlo.config.env import load_project_env


def build_materialization_command(
    backend: ContainerBackend,
    container_name: str,
    selection: str,
    partition: str | None,
    *,
    refresh_contracts: bool | None = None,
) -> list[str]:
    """Build an exec command for a selected container without discovering services."""
    host_platform = platform.system()
    exec_env = {
        "PHLO_HOST_PLATFORM": host_platform,
        "PHLO_PROJECT_PATH": "/app",
    }
    if refresh_contracts is not None:
        exec_env["PHLO_AUTO_REFRESH_CONTRACTS"] = "1" if refresh_contracts else "0"
        exec_env["PHLO_CONTRACT_REFRESH_SELECTION"] = selection
    project_env = load_project_env()
    for pretty_var in ("PHLO_OBSERVE_PRETTY", "PHLO_OBSERVE_PRETTY_VERBOSE", "OBSERVE_DRAINS"):
        if project_env.get(pretty_var):
            exec_env[pretty_var] = project_env[pretty_var]
    command = [
        "dagster",
        "asset",
        "materialize",
        "-m",
        "phlo_dagster.framework.definitions",
        "--select",
        selection,
    ]
    if partition:
        command.extend(["--partition", partition])
    return backend.container_exec_cmd(
        container_name=container_name,
        user=":".join(map(str, container_host_identity(backend.name)))
        if host_platform == "Linux"
        else None,
        env=exec_env,
        workdir="/app",
        command=command,
    )
