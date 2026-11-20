"""Run ``dagster asset materialize`` as a run Observatory can attribute.

``phlo materialize`` executes the Dagster CLI inside the code-location
container. That CLI accepts no run tags and records no code location, so its
runs carried neither ``phlo/ref`` nor a location identity, and phlo-api, which
attributes runs to environments by both, could not show them. This entry point
adds the tags and records the workspace's code location as the run's origin,
the same origin the webserver gives runs it launches.

Usage: ``python -m phlo_dagster.tagged_materialize [--location NAME] KEY=VALUE [...] -- <args>``
"""

from __future__ import annotations

import os
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import yaml

_USAGE = "usage: tagged_materialize [--location NAME] KEY=VALUE [...] -- <materialize args>"


def _split(argv: Sequence[str]) -> tuple[str | None, dict[str, str], list[str]]:
    items = list(argv)
    if "--" not in items:
        raise SystemExit(_USAGE)
    index = items.index("--")
    head, rest = items[:index], items[index + 1 :]
    location = None
    if head[:1] == ["--location"]:
        if len(head) < 2 or not head[1]:
            raise SystemExit(_USAGE)
        location, head = head[1], head[2:]
    tags: dict[str, str] = {}
    for item in head:
        key, sep, value = item.partition("=")
        if not sep or not key or not value:
            raise SystemExit(f"invalid run tag {item!r}; expected KEY=VALUE")
        tags[key] = value
    return location, tags, rest


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


def main(argv: Sequence[str] | None = None) -> int:
    """Start the Dagster materialize command as a tagged run of the workspace location."""
    location, tags, rest = _split(sys.argv[1:] if argv is None else argv)
    workspace = Path(os.environ.get("DAGSTER_HOME", "/opt/dagster")) / "workspace.yaml"
    server = _workspace_server(location, workspace)

    import dagster._cli.asset as asset_cli
    from dagster import DagsterInstance
    from dagster._cli import main as dagster_main

    execute_job = asset_cli.execute_job
    create_run_for_job = DagsterInstance.create_run_for_job
    extra = tags

    def tagged_execute_job(*args: Any, tags: dict[str, str] | None = None, **kwargs: Any) -> Any:
        # Tags the CLI computed itself, such as the partition, win.
        return execute_job(*args, tags={**extra, **(tags or {})}, **kwargs)

    def located_create_run(self: Any, job_def: Any, *args: Any, **kwargs: Any) -> Any:
        if kwargs.get("remote_job_origin") is None:
            kwargs["remote_job_origin"] = _remote_origin(server, job_def.name)
        return create_run_for_job(self, job_def, *args, **kwargs)

    # The Dagster CLI calls these by name, so replace them where they are looked up.
    setattr(asset_cli, "execute_job", tagged_execute_job)  # noqa: B010
    setattr(DagsterInstance, "create_run_for_job", located_create_run)  # noqa: B010
    sys.argv = ["dagster", "asset", "materialize", *rest]
    result = dagster_main()
    return int(result or 0)


if __name__ == "__main__":
    raise SystemExit(main())
