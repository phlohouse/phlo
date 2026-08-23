"""CLI commands for RBAC authorization management.

Exposes the validate/plan/sync/verify/revert workflow over RBACConfigLoader
and SyncController. Mutating commands (real sync, any revert) are gated by
require_mutation_authorization; read-only commands run unauthenticated.

Imported by phlo.cli.main to expose the `phlo authz` command group; covered by tests/cli.
Drives phlo.rbac sync over phlo.capabilities.discovery with mutation authorization wrappers.
"""

from __future__ import annotations

import sys
from contextlib import suppress
from pathlib import Path

import click

from phlo.capabilities.discovery import discover_capabilities
from phlo.cli.authorization_wrappers import require_mutation_authorization
from phlo.logging import get_logger
from phlo.rbac.config import RBACConfigLoader
from phlo.rbac.sync import SyncController

logger = get_logger(__name__)


def _ensure_capabilities_discovered() -> None:
    """Ensure capabilities are discovered before executing authz commands."""
    with suppress(Exception):
        discover_capabilities()


@click.group(name="authz")
def authz_group():
    """Manage RBAC authorization policies and backend synchronization.

    This command group provides tools for:
    - Validating RBAC configuration
    - Planning and applying policy changes to backends
    - Verifying backend state matches desired state
    - Reverting applied changes

    For more information, see Spec 0017: RBAC Core Services Enforcement.
    """
    _ensure_capabilities_discovered()


@authz_group.command()
@click.option(
    "--path",
    type=click.Path(exists=True, file_okay=False, dir_okay=True, path_type=Path),
    default=None,
    help="Path to .phlo directory containing RBAC config.",
)
def validate(path):
    """Validate RBAC configuration files.

    Checks that roles.yaml and policies.yaml are valid and consistent.
    This includes:
    - Valid YAML syntax
    - Role hierarchy has no cycles
    - All referenced roles exist
    - Policy rules are well-formed

    Exit code 0 means validation passed. Non-zero means errors were found.
    """

    loader = RBACConfigLoader(base_path=path)
    is_valid, errors = loader.validate()

    if is_valid:
        click.echo("Validation passed.")
        sys.exit(0)
    else:
        click.echo("Validation failed:", err=True)
        for error in errors:
            click.echo(f"  - {error}", err=True)
        sys.exit(1)


@authz_group.command()
@click.option(
    "--path",
    type=click.Path(exists=True, file_okay=False, dir_okay=True, path_type=Path),
    default=None,
    help="Path to .phlo directory containing RBAC config.",
)
@click.option(
    "--backend",
    multiple=True,
    help="Specific backends to plan for (default: all).",
)
@click.option(
    "--environment",
    default="development",
    help="Environment name (development, staging, production).",
)
def plan(path, backend, environment):
    """Create a sync plan without applying changes.

    This shows what changes would be made to each backend without actually
    making them. Use this to review changes before applying.

    Exit code 0 means planning succeeded. Non-zero means errors occurred.
    """

    loader = RBACConfigLoader(base_path=path)
    controller = SyncController(loader=loader)

    backends = list(backend) if backend else None

    try:
        plans = controller.plan(backends=backends, environment=environment)
    except Exception as e:
        click.echo(f"Planning failed: {e}", err=True)
        sys.exit(1)

    if not plans:
        click.echo("No plans generated.")
        sys.exit(0)

    for backend_name, plan in plans.items():
        click.echo(f"\n=== {backend_name} ===")
        click.echo(f"Version hash: {plan.version_hash}")
        click.echo(f"Changes: {len(plan.changes)}")

        if plan.changes:
            click.echo("\nPlanned changes:")
            for change in plan.changes:
                click.echo(f"  [{change.change_type}] {change.artifact.name}")
                click.echo(f"    Statement: {change.artifact.statement[:80]}...")

        if plan.errors:
            click.echo("\nErrors:")
            for error in plan.errors:
                click.echo(f"  - {error}")

    sys.exit(0)


@authz_group.command()
@click.option(
    "--path",
    type=click.Path(exists=True, file_okay=False, dir_okay=True, path_type=Path),
    default=None,
    help="Path to .phlo directory containing RBAC config.",
)
@click.option(
    "--backend",
    multiple=True,
    help="Specific backends to sync (default: all).",
)
@click.option(
    "--environment",
    default="development",
    help="Environment name (development, staging, production).",
)
@click.option(
    "--dry-run",
    is_flag=True,
    help="Plan changes without applying them.",
)
# Dry-run applies nothing, so it is exempt from mutation authorization;
# only a real sync requires it.
@require_mutation_authorization("authz.sync", when=lambda params: not params.get("dry_run"))
def sync(path, backend, environment, dry_run):
    """Synchronize RBAC policies to backend-native enforcement.

    This compiles canonical RBAC policies into backend-specific artifacts
    and applies them to the target backends (Trino, PostgreSQL, Hasura, etc.).

    Exit code 0 means sync succeeded. Non-zero means failures occurred.
    """

    loader = RBACConfigLoader(base_path=path)
    controller = SyncController(loader=loader)

    backends = list(backend) if backend else None

    try:
        results = controller.sync(
            backends=backends,
            environment=environment,
            dry_run=dry_run,
        )
    except Exception as e:
        click.echo(f"Sync failed: {e}", err=True)
        sys.exit(1)

    if not results:
        click.echo("No sync results.")
        sys.exit(0)

    has_failures = False

    for backend_name, result in results.items():
        click.echo(f"\n=== {backend_name} ===")
        click.echo(f"Version hash: {result.version_hash}")
        click.echo(f"Success: {result.success}")
        click.echo(f"Applied: {result.applied_count}")
        click.echo(f"Failed: {result.failed_count}")

        if result.errors:
            click.echo("\nErrors:")
            for error in result.errors:
                click.echo(f"  - {error}")

        if not result.success:
            has_failures = True

    if has_failures:
        sys.exit(1)
    sys.exit(0)


@authz_group.command()
@click.option(
    "--path",
    type=click.Path(exists=True, file_okay=False, dir_okay=True, path_type=Path),
    default=None,
    help="Path to .phlo directory containing RBAC config.",
)
@click.option(
    "--backend",
    multiple=True,
    help="Specific backends to verify (default: all).",
)
@click.option(
    "--environment",
    default="development",
    help="Environment name (development, staging, production).",
)
def verify(path, backend, environment):
    """Verify backend state matches desired RBAC state.

    Compares the current state of each backend against the desired state
    defined in canonical RBAC configuration. Reports any drift.

    Exit code 0 means backends are in sync. Non-zero means drift detected.
    """

    loader = RBACConfigLoader(base_path=path)
    controller = SyncController(loader=loader)

    backends = list(backend) if backend else None

    try:
        results = controller.verify(backends=backends, environment=environment)
    except Exception as e:
        click.echo(f"Verification failed: {e}", err=True)
        sys.exit(1)

    if not results:
        click.echo("No verification results.")
        sys.exit(0)

    has_drift = False

    for backend_name, result in results.items():
        click.echo(f"\n=== {backend_name} ===")
        click.echo(f"In sync: {result.in_sync}")

        if result.missing:
            click.echo(f"\nMissing artifacts: {len(result.missing)}")
            for artifact in result.missing:
                click.echo(f"  - {artifact.name}")
            has_drift = True

        if result.extra:
            click.echo(f"\nExtra artifacts: {len(result.extra)}")
            for artifact in result.extra:
                click.echo(f"  - {artifact.name}")
            has_drift = True

        if result.mismatched:
            click.echo(f"\nMismatched artifacts: {len(result.mismatched)}")
            for artifact in result.mismatched:
                click.echo(f"  - {artifact.name}")
            has_drift = True

    if has_drift:
        sys.exit(1)
    sys.exit(0)


@authz_group.command()
@click.argument("revert_ids", nargs=-1, required=True)
@click.option(
    "--path",
    type=click.Path(exists=True, file_okay=False, dir_okay=True, path_type=Path),
    default=None,
    help="Path to .phlo directory containing RBAC config.",
)
@click.option(
    "--backend",
    multiple=True,
    help="Specific backends to revert (default: all).",
)
@click.option(
    "--environment",
    default="development",
    help="Environment name (development, staging, production).",
)
@require_mutation_authorization("authz.revert")
def revert(path, revert_ids, backend, environment):
    """Revert previously applied policy changes.

    Takes one or more revert IDs from a previous sync operation and
    attempts to undo those changes.

    Exit code 0 means revert succeeded. Non-zero means failures occurred.
    """

    loader = RBACConfigLoader(base_path=path)
    controller = SyncController(loader=loader)

    backends = list(backend) if backend else None

    try:
        results = controller.revert(
            revert_ids=list(revert_ids),
            backends=backends,
            environment=environment,
        )
    except Exception as e:
        click.echo(f"Revert failed: {e}", err=True)
        sys.exit(1)

    has_failures = False

    for backend_name, (success_ids, errors) in results.items():
        click.echo(f"\n=== {backend_name} ===")
        click.echo(f"Reverted: {len(success_ids)}")
        click.echo(f"Errors: {len(errors)}")

        if errors:
            click.echo("\nErrors:")
            for error in errors:
                click.echo(f"  - {error}")
            has_failures = True

    if has_failures:
        sys.exit(1)
    sys.exit(0)
