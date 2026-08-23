"""CLI commands for MinIO S3-compatible object storage operations.

This module provides Click-based CLI commands for interacting with MinIO,
including listing buckets/objects and retrieving admin information. All
commands execute inside the MinIO container backend container using the mc (MinIO Client).

Examples:
    List all buckets:
        $ phlo minio ls

    List objects recursively in a bucket:
        $ phlo minio ls local/my-bucket --recursive

    Get admin info in JSON:
        $ phlo minio admin info --json

Note:
    All commands require container backend to be running and the MinIO service
    to be available.

"""

from __future__ import annotations

import shlex
import subprocess
from subprocess import TimeoutExpired

import click

from phlo.cli.authorization_wrappers import enforce_surface_mutation_authorization
from phlo.cli.commands.services.utils import (
    ensure_compose_project as ensure_phlo_dir,
    require_container_backend as _require_selected_container_backend,
)
from phlo.cli.infrastructure.command import CommandError, run_command
from phlo.cli.infrastructure.compose import compose_base_cmd
from phlo.cli.infrastructure.utils import get_project_name
from phlo.cli.output import command_failed_error
from phlo.logging import get_logger
from phlo_minio.authorization import get_minio_cli_adapter

logger = get_logger(__name__)


def _require_container_backend() -> None:
    """Validate that the selected container backend is available."""
    _require_selected_container_backend()


def _mc_exec_base(*, tty: bool) -> list[str]:
    """Build the docker compose exec command base for MinIO client operations.
    Constructs a command list that will execute mc (MinIO Client) commands
    inside the running MinIO container via docker compose exec.

    Examples:
        Non-TTY for programmatic use:
            >>> cmd = _mc_exec_base(tty=False)
            >>> cmd.extend(["ls", "local/my-bucket"])
            # Result: ['docker', 'compose', ..., 'exec', '-T', 'minio', 'mc', 'ls', ...]

        TTY for interactive use:
            >>> cmd = _mc_exec_base(tty=True)
            >>> cmd.extend(["admin", "info"])
            # Result: ['docker', 'compose', ..., 'exec', 'minio', 'mc', 'admin', 'info']

    Implementation:
        Uses phlo CLI infrastructure to determine project configuration:
            - ensure_phlo_dir: Locate .phlo directory
            - get_project_name: Get compose project name
            - compose_base_cmd: Build base docker compose command
    """
    phlo_dir = ensure_phlo_dir()
    project_name = get_project_name()
    cmd = compose_base_cmd(phlo_dir=phlo_dir, project_name=project_name)
    cmd.append("exec")
    if not tty:
        cmd.append("-T")
    cmd.extend(["minio", "mc"])
    return cmd


def _mc_shell_exec_base(*, tty: bool) -> list[str]:
    """Build a compose exec shell command that configures the default mc alias."""
    phlo_dir = ensure_phlo_dir()
    project_name = get_project_name()
    cmd = compose_base_cmd(phlo_dir=phlo_dir, project_name=project_name)
    cmd.append("exec")
    if not tty:
        cmd.append("-T")
    cmd.extend(["minio", "/bin/sh", "-c"])
    return cmd


def _mc_with_local_alias(args: list[str]) -> list[str]:
    """Return a shell command that ensures `local` has generated stack credentials."""
    alias_cmd = (
        'mc alias set local http://localhost:9000 "$MINIO_ROOT_USER" '
        '"$MINIO_ROOT_PASSWORD" >/dev/null'
    )
    return [f"{alias_cmd} && mc {shlex.join(args)}"]


@click.command(
    name="minio",
    context_settings={"ignore_unknown_options": True, "allow_extra_args": True},
)
@click.argument("mc_args", nargs=-1, type=click.UNPROCESSED)
@click.pass_context
def minio_group(ctx: click.Context, mc_args: tuple[str, ...]) -> None:
    """Run MinIO client (mc) commands inside the project service container.
    This is the main entry point for MinIO CLI operations. It handles
    common subcommands like 'ls' and 'admin info' with dedicated handlers,
    while passing other commands directly to the mc binary.

    Raises: click.ClickException when if the mc command exits with non-zero status.
    Examples:
        List all buckets:
            $ phlo minio ls
            [2024-01-15 10:30:00 UTC]     0B my-bucket/

        List with recursion:
            $ phlo minio ls local/my-bucket --recursive

        Direct mc commands:
            $ phlo minio mb local/new-bucket  # Make bucket
            $ phlo minio cp file.txt local/my-bucket/  # Copy file
            $ phlo minio mirror local/data/ local/my-bucket/  # Mirror directory

        Admin operations:
            $ phlo minio admin info
            $ phlo minio admin info --json

        Alias configuration:
            $ phlo minio alias set myminio http://localhost:10001 minio minio123

    Note:
        The 'ls' and 'admin info' subcommands have dedicated handlers
        for better output formatting. All other commands are passed
        directly to mc inside the MinIO container.
    """
    if mc_args and mc_args[0] == "ls":
        minio_ls.main(
            args=list(mc_args[1:]),
            prog_name="phlo minio ls",
            standalone_mode=False,
        )
        return
    if len(mc_args) >= 2 and mc_args[0] == "admin" and mc_args[1] == "info":
        minio_admin_info.main(
            args=list(mc_args[2:]),
            prog_name="phlo minio admin info",
            standalone_mode=False,
        )
        return

    _require_container_backend()
    enforce_surface_mutation_authorization("minio", get_minio_cli_adapter)
    cmd = _mc_shell_exec_base(tty=True)
    cmd.extend(_mc_with_local_alias(list(mc_args)))
    result = subprocess.run(cmd, check=False)
    if result.returncode != 0:
        raise command_failed_error(
            "MinIO client command",
            exit_code=result.returncode,
            run="phlo services status",
        )


@click.command(name="ls")
@click.argument("target", default="local/")
@click.option("--recursive", is_flag=True, help="List recursively.")
@click.option("--json", "as_json", is_flag=True, help="Emit JSON lines from mc.")
@click.option("--timeout", "timeout_seconds", default=30, show_default=True, type=int)
def minio_ls(target: str, recursive: bool, as_json: bool, timeout_seconds: int) -> None:
    """List objects or buckets using the MinIO client.
    Lists S3 buckets or objects within a bucket using the mc ls command.
    Supports recursive listing and JSON output for programmatic use.

    Raises: click.ClickException when if command fails or times out.
    Examples:
        List all buckets:
            $ phlo minio ls
            [2024-01-15 10:30:00 UTC]     0B my-bucket/
            [2024-01-15 10:30:00 UTC]     0B staging-bucket/

        List bucket contents:
            $ phlo minio ls local/my-bucket
            [2024-01-15 10:30:00 UTC]  1.5MiB data/
            [2024-01-15 10:30:00 UTC]  256KiB config.yaml

        Recursive listing:
            $ phlo minio ls local/my-bucket --recursive
            [2024-01-15 10:30:00 UTC]  1.5MiB data/partition1/
            [2024-01-15 10:30:00 UTC]  256KiB data/partition1/file.parquet
            ...

        JSON output for scripts:
            $ phlo minio ls local/my-bucket --json | jq '.key'
            "data/partition1/file.parquet"
            "data/partition2/file.parquet"

        List with custom timeout:
            $ phlo minio ls local/large-bucket --recursive --timeout 120

    Use Case:
        Verify data lake contents:
            $ phlo minio ls local/raw-data/invoices/ --recursive --json | \
                jq 'select(.size > 1000000) | .key'
            # List all files larger than 1MB
    """
    _require_container_backend()
    mc_args = ["ls"]
    if recursive:
        mc_args.append("--recursive")
    if as_json:
        mc_args.append("--json")
    mc_args.append(target)
    cmd = _mc_shell_exec_base(tty=False)
    cmd.extend(_mc_with_local_alias(mc_args))

    try:
        result = run_command(
            cmd,
            timeout_seconds=timeout_seconds,
            capture_output=True,
            check=True,
        )
    except CommandError as exc:
        logger.error(
            "minio_ls_failed",
            target=target,
            recursive=recursive,
            as_json=as_json,
            stderr=exc.stderr.strip(),
            error=str(exc),
            exc_info=True,
        )
        raise command_failed_error(
            "MinIO list",
            details={"Target": target},
            run="phlo services status",
        ) from exc
    except TimeoutExpired as exc:
        raise click.ClickException(f"List timed out after {timeout_seconds} seconds.") from exc

    if result.stdout:
        click.echo(result.stdout, nl=False)


@click.command(name="info")
@click.argument("target", default="local/")
@click.option("--json", "as_json", is_flag=True, help="Emit JSON output from mc.")
@click.option("--timeout", "timeout_seconds", default=30, show_default=True, type=int)
def minio_admin_info(target: str, as_json: bool, timeout_seconds: int) -> None:
    """Show MinIO server admin information.
    Retrieves administrative information about the MinIO server
    using the mc admin info command. Useful for monitoring server
    health, storage usage, and cluster status.

    Raises: click.ClickException when if command fails or times out.
    Examples:
        Basic server info:
            $ phlo minio admin info
            ●  minio:10001
               Uptime: 3 hours 45 minutes
               Version: 2024-01-15T20:30:00Z
               Network: 1/1 OK
               Drives: 1/1 OK

        JSON output for monitoring:
            $ phlo minio admin info --json | jq '.info.servers[0]'
            {
              "state": "online",
              "endpoint": "minio:10001",
              "uptime": 13500000000000,
              ...
            }

        Check specific alias:
            $ phlo minio admin info mycustom/

    Use Case:
        Health check in CI/CD:
            $ phlo minio admin info --json | jq -e '.status == "success"' > /dev/null
            # Exit code indicates server health status

    Note:
        Requires admin privileges on the MinIO server.
    """
    _require_container_backend()
    mc_args = ["admin", "info"]
    if as_json:
        mc_args.append("--json")
    mc_args.append(target)
    cmd = _mc_shell_exec_base(tty=False)
    cmd.extend(_mc_with_local_alias(mc_args))

    try:
        result = run_command(
            cmd,
            timeout_seconds=timeout_seconds,
            capture_output=True,
            check=True,
        )
    except CommandError as exc:
        logger.error(
            "minio_admin_info_failed",
            target=target,
            as_json=as_json,
            stderr=exc.stderr.strip(),
            error=str(exc),
            exc_info=True,
        )
        raise command_failed_error(
            "MinIO admin info",
            details={"Target": target},
            run="phlo services status",
        ) from exc
    except TimeoutExpired as exc:
        raise click.ClickException(
            f"Admin info timed out after {timeout_seconds} seconds."
        ) from exc

    if result.stdout:
        click.echo(result.stdout, nl=False)
