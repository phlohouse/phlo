"""Run ``dagster asset materialize`` as a run Observatory can attribute.

``phlo materialize`` executes the Dagster CLI inside the code-location
container. That CLI accepts no run tags and records no code location, so its
runs carried neither ``phlo/ref`` nor a location identity, and phlo-api, which
attributes runs to environments by both, could not show them. This entry point
adds the tags and records the workspace's code location as the run's origin,
the same origin the webserver gives runs it launches.

``--default-partition KEY`` is the partition ``phlo materialize`` picks when the
user names none. It applies only when the selected assets are partitioned, so an
unpartitioned asset still runs (Dagster refuses ``--partition`` for those).

Usage: ``python -m phlo_dagster.tagged_materialize [--location NAME]
[--default-partition KEY] KEY=VALUE [...] -- <args>``
"""

from __future__ import annotations

import os
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import yaml

_USAGE = (
    "usage: tagged_materialize [--location NAME] [--default-partition KEY] KEY=VALUE [...]"
    " -- <materialize args>"
)
_OPTIONS = ("--location", "--default-partition")


def _split(argv: Sequence[str]) -> tuple[dict[str, str], dict[str, str], list[str]]:
    items = list(argv)
    if "--" not in items:
        raise SystemExit(_USAGE)
    index = items.index("--")
    head, rest = items[:index], items[index + 1 :]
    options: dict[str, str] = {}
    while head[:1] and head[0] in _OPTIONS:
        if len(head) < 2 or not head[1] or head[1] in _OPTIONS or head[0] in options:
            raise SystemExit(_USAGE)
        options[head[0]] = head[1]
        head = head[2:]
    tags: dict[str, str] = {}
    for item in head:
        key, sep, value = item.partition("=")
        if not sep or not key or not value:
            raise SystemExit(f"invalid run tag {item!r}; expected KEY=VALUE")
        tags[key] = value
    return options, tags, rest


def _workspace_server(location: str | None, workspace: Path) -> dict[str, Any]:
    """Return the workspace's gRPC server entry for ``location``."""
    try:
        document = yaml.safe_load(workspace.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as exc:
        raise SystemExit(f"cannot read the Dagster workspace {workspace}: {exc}") from exc
    servers = [
        entry["grpc_server"]
        for entry in document.get("load_from") or []
        if isinstance(entry, dict) and isinstance(entry.get("grpc_server"), dict)
    ]
    matches = [
        server for server in servers if location is None or server.get("location_name") == location
    ]
    if len(matches) != 1 or not matches[0].get("location_name"):
        wanted = f"code location {location!r}" if location else "a single code location"
        raise SystemExit(f"the Dagster workspace {workspace} does not name {wanted}")
    return matches[0]


def _remote_origin(server: dict[str, Any], job_name: str) -> Any:
    from dagster._core.definitions.repository_definition.valid_definitions import (
        SINGLETON_REPOSITORY_NAME,
    )
    from dagster._core.remote_origin import (
        GrpcServerCodeLocationOrigin,
        RemoteJobOrigin,
        RemoteRepositoryOrigin,
    )

    code_location = GrpcServerCodeLocationOrigin(
        host=server.get("host", "localhost"),
        port=server.get("port"),
        socket=server.get("socket"),
        location_name=server["location_name"],
    )
    return RemoteJobOrigin(
        repository_origin=RemoteRepositoryOrigin(
            code_location_origin=code_location,
            repository_name=SINGLETON_REPOSITORY_NAME,
        ),
        job_name=job_name,
    )


def _selection_is_partitioned(asset_cli: Any, select: str, python_pointer_opts: Any) -> bool:
    """Return whether any asset ``select`` names is partitioned.

    The definitions module is already imported by then, so loading the
    repository again here is cheap.
    """
    from dagster import AssetSelection

    origin = asset_cli.get_repository_python_origin_from_cli_opts(python_pointer_opts)
    repo_def = asset_cli.recon_repository_from_origin(origin).get_definition()
    asset_keys = AssetSelection.from_coercible(select.split(",")).resolve(repo_def.asset_graph)
    job_def = repo_def.get_implicit_job_def_for_assets(asset_keys)
    if job_def is None:
        # Mixed partitioning: let Dagster report it with the partition applied.
        return True
    return any(job_def.asset_layer.get(key).partitions_def is not None for key in asset_keys)


def main(argv: Sequence[str] | None = None) -> int:
    """Start the Dagster materialize command as a tagged run of the workspace location."""
    options, tags, rest = _split(sys.argv[1:] if argv is None else argv)
    location = options.get("--location")
    default_partition = options.get("--default-partition")
    server = None
    if location is not None or tags:
        workspace = Path(os.environ.get("DAGSTER_HOME", "/opt/dagster")) / "workspace.yaml"
        server = _workspace_server(location, workspace)

    import dagster._cli.asset as asset_cli
    from dagster import DagsterInstance
    from dagster._cli import main as dagster_main

    execute_job = asset_cli.execute_job
    execute_materialize = asset_cli.execute_materialize_command
    create_run_for_job = DagsterInstance.create_run_for_job
    extra = tags

    def tagged_execute_job(*args: Any, tags: dict[str, str] | None = None, **kwargs: Any) -> Any:
        # Tags the CLI computed itself, such as the partition, win.
        return execute_job(*args, tags={**extra, **(tags or {})}, **kwargs)

    def located_create_run(self: Any, job_def: Any, *args: Any, **kwargs: Any) -> Any:
        if kwargs.get("remote_job_origin") is None:
            kwargs["remote_job_origin"] = _remote_origin(server, job_def.name)
        return create_run_for_job(self, job_def, *args, **kwargs)

    def defaulted_materialize(**kwargs: Any) -> Any:
        if (
            kwargs.get("partition") is None
            and kwargs.get("partition_range") is None
            and _selection_is_partitioned(
                asset_cli, kwargs["select"], kwargs["python_pointer_opts"]
            )
        ):
            kwargs["partition"] = default_partition
        return execute_materialize(**kwargs)

    # The Dagster CLI calls these by name, so replace them where they are looked up.
    replacements: list[tuple[Any, str, Any]] = []
    if tags:
        replacements.append((asset_cli, "execute_job", tagged_execute_job))
    if server is not None:
        replacements.append((DagsterInstance, "create_run_for_job", located_create_run))
    if default_partition is not None:
        replacements.append((asset_cli, "execute_materialize_command", defaulted_materialize))
    for owner, name, replacement in replacements:
        setattr(owner, name, replacement)
    sys.argv = ["dagster", "asset", "materialize", *rest]
    result = dagster_main()
    return int(result or 0)


if __name__ == "__main__":
    raise SystemExit(main())
