"""phlo_transform CLI authorization table.

Declares which transform CLI commands mutate versus read so regulated
deployments gate engine runs, workspace translation, provider switching, and
binary installation through the same surface contract as other providers.
"""

from __future__ import annotations

from phlo.cli.authorization import CliSurfaceAdapter, cli_surface_adapter_class

SURFACE_NAME = "phlo-transform"
FRAMEWORK_TYPE = "cli"
MUTATION_COMMANDS: frozenset[str] = frozenset(
    {
        "transform.install",
        "transform.run",
        "transform.translate",
        "transform.use",
    }
)
READ_COMMANDS: frozenset[str] = frozenset(
    {
        "transform.check",
        "transform.doctor",
        "transform.explain",
        "transform.lineage",
        "transform.list",
        "transform.plan",
        "transform.status",
        "transform.test",
    }
)
COMMAND_RESOURCE_MAP: dict[str, str] = {
    "transform.install": "project",
    "transform.run": "dataset",
    "transform.translate": "project",
    "transform.use": "project",
}
COMMAND_ACTION_MAP: dict[str, str] = {
    "transform.install": "project.create",
    "transform.run": "dataset.write",
    "transform.translate": "project.create",
    "transform.use": "project.update",
}

TransformSurfaceAdapter = cli_surface_adapter_class(
    "TransformSurfaceAdapter",
    surface_name=SURFACE_NAME,
    mutation_commands=MUTATION_COMMANDS,
    read_commands=READ_COMMANDS,
    command_resource_map=COMMAND_RESOURCE_MAP,
    command_action_map=COMMAND_ACTION_MAP,
    default_action_prefix="transform",
    default_resource_prefix="transform",
)


def get_transform_adapter() -> CliSurfaceAdapter:
    """Return the shared TransformSurfaceAdapter instance for this surface."""

    return TransformSurfaceAdapter.get_instance()
