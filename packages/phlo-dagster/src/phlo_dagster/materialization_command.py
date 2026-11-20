"""Build container commands for Dagster asset materialization.

Forward host identity and project pretty-drain settings. Contract refresh
is left unset unless the caller explicitly enables or disables it.
"""

import platform

from phlo.cli.infrastructure.container_backend import ContainerBackend, container_host_identity
from phlo.config.env import load_project_env


def _default_run_target() -> tuple[str, str | None] | None:
    """Return the default environment's Nessie ref and code location, if configured."""
    from phlo.config.project import get_default_environment, get_environment_targets

    try:
        target = get_environment_targets()[get_default_environment()]
    except (OSError, ValueError, TypeError, KeyError):
        return None
    if not target.nessie_ref:
        return None
    return target.nessie_ref, target.dagster_location or None


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
    # Record the ref the run writes and its code location, so phlo-api shows
    # the run in its environment like runs launched from Observatory.
    target = _default_run_target()
    if target is None:
        launcher = ["dagster", "asset", "materialize"]
    else:
        ref, location = target
        launcher = ["python", "-m", "phlo_dagster.tagged_materialize"]
        if location:
            launcher.extend(["--location", location])
        launcher.extend([f"phlo/ref={ref}", "--"])
    command = [
        *launcher,
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
