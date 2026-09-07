"""Guarded plan-first operations (ADR 0049, Plans 010-012).

`phlo operations maintenance inventory|plan|apply` — inventory is read-only,
plan is mutation-free and returns a JSON envelope, apply is authorized and
bound to the exact plan token. Orphan deletion is always rejected.

`phlo operations backup create|verify` — create is authorized and finalizes
one immutable set manifest only after every provider artifact succeeds;
verify is read-only and mutation-free.

`phlo operations restore plan|apply` — plan is mutation-free and binds the
set digest to an explicit target; apply is authorized, reverifies the set,
and restores providers in reverse order with post-restore reconciliation.
An implicit/in-place target is always refused.

`phlo operations upgrade plan|apply` — exactly one supported version pair is
executable; apply requires a verified backup and runs provider steps in order,
issuing a restore action before the rollback boundary or forward-repair
instructions after it. No false rollback after an irreversible step.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import click

from phlo.cli.authorization_wrappers import require_mutation_authorization
from phlo.cli.contract import PhloCommand, PhloGroup
from phlo.cli.output import json_envelope
from phlo.logging import get_logger

logger = get_logger(__name__)


def _durable_journal():
    """Resolve the configured durable journal; fail closed when none is present.

    An authorized mutation must never silently fall back to an ephemeral
    in-memory journal, or the exactly-once contract disappears with the
    process. Callers that cannot resolve a durable store are refused.
    """
    import os

    from phlo.operations.journal_store import FileOperationJournalStore

    directory = os.environ.get("PHLO_OPERATIONS_JOURNAL_DIR")
    if not directory:
        raise click.ClickException(
            "no durable operation journal configured: set PHLO_OPERATIONS_JOURNAL_DIR "
            "before running an authorized mutation"
        )
    return FileOperationJournalStore(directory)


def _require_fixture_substrate(fixture_substrate: bool, action: str) -> None:
    """Force explicit acknowledgement that restore/upgrade apply is a fixture substrate.

    The provider restore/upgrade adapters in this stack mutate only staged
    files beneath the filesystem target; they do not connect to and migrate
    live deployment services. They therefore must never be presented as an
    operational recovery/upgrade. Callers must pass ``--fixture-substrate`` to
    confirm they are proving the journey against a non-live target.
    """
    if not fixture_substrate:
        raise click.ClickException(
            f"{action} apply is a fixture substrate only (it stages files, it does "
            "not mutate a live deployment). Pass --fixture-substrate to confirm you "
            "are not targeting a live deployment."
        )


def _emit(data: Any, *, status: str | None = None, reason_code: str | None = None) -> None:
    ctx = click.get_current_context()
    if ctx.params.get("output_json"):
        click.echo(
            json_envelope(
                data=data,
                status=status or ("planned" if ctx.info_name == "plan" else None),
                reason_code=reason_code,
            )
        )
    else:
        click.echo(json.dumps(data, indent=2, sort_keys=False))


def _read_json(path: str) -> Any:
    from pathlib import Path

    try:
        with Path(path).open(encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        raise click.UsageError(f"could not read plan file {path}: {exc}") from exc


@click.group("operations", cls=PhloGroup)
def operations_group() -> None:
    """Guarded plan-first operations (maintenance, backup, restore, upgrade)."""


@operations_group.group("maintenance", cls=PhloGroup)
def maintenance_group() -> None:
    """Plan and apply v1 table maintenance (compaction, snapshot expiry)."""


@operations_group.group("backup", cls=PhloGroup)
def backup_group() -> None:
    """Create and verify immutable v1 backup sets (ADR 0049 §3)."""


@backup_group.command("create", cls=PhloCommand)
@click.option(
    "--target",
    type=click.Path(file_okay=False, path_type=Path),
    required=True,
    help="New, empty directory that will own the backup set.",
)
@click.option("--json", "output_json", is_flag=True, help="Emit a structured command result.")
@click.option("--format", "output_format", type=click.Choice(["json", "table"]), default="table")
@require_mutation_authorization("operations.backup.create")
def backup_create(target: Path, output_format: str, output_json: bool = False) -> None:
    """Create one verified backup set for all v1-owned state (authorized)."""
    from phlo.capabilities.continuity import BACKUP_PROVIDER_ORDER
    from phlo.operations.backup import create_backup_set, default_backup_contributors
    from phlo.operations.journal import OperationJournalError

    try:
        contributors = default_backup_contributors()
    except LookupError as exc:
        raise click.ClickException(str(exc)) from exc

    # Order contributors by the frozen ADR 0049 sequence.
    contributors = sorted(contributors, key=lambda item: BACKUP_PROVIDER_ORDER.index(item[0]))

    journal = _durable_journal()
    try:
        result = create_backup_set(
            target=target,
            contributors=contributors,
            journal=journal,
        )
    except OperationJournalError as exc:
        raise click.ClickException(
            f"journal error: {exc.code} ({', '.join(exc.identifiers)})"
        ) from exc
    except Exception as exc:
        raise click.ClickException(f"backup create failed: {exc}") from exc

    if output_json or output_format == "json":
        _emit(result.to_dict())
    else:
        click.echo(f"Backup set {result.set_id}: {result.state}")
        if result.manifest:
            click.echo(f"  artifacts: {len(result.manifest.get('artifacts', []))}")
    if not result.accepted:
        raise click.exceptions.Exit(1)


@backup_group.command("verify", cls=PhloCommand)
@click.option(
    "--backup-set",
    "backup_set",
    type=click.Path(file_okay=False, path_type=Path),
    required=True,
    help="Path to the finalized backup set directory.",
)
@click.option(
    "--expected-deployment",
    "expected_deployment",
    default=None,
    help="Reject sets whose recorded source deployment does not match.",
)
@click.option("--json", "output_json", is_flag=True, help="Emit a structured command result.")
@click.option("--format", "output_format", type=click.Choice(["json", "table"]), default="table")
def backup_verify(
    backup_set: Path, expected_deployment: str | None, output_format: str, output_json: bool = False
) -> None:
    """Independently verify a backup set (read-only, no service mutation)."""
    from phlo.operations.backup import verify_backup_set

    result = verify_backup_set(backup_set, expected_deployment_id=expected_deployment)

    if output_json or output_format == "json":
        _emit(result.to_dict())
    else:
        click.echo(f"Backup set {result.set_id or '(unknown)'}: {result.state}")
        for reason in result.reasons:
            click.echo(f"  reason: {reason}")

    if not result.accepted:
        raise SystemExit(1)


@operations_group.group("restore", cls=PhloGroup)
def restore_group() -> None:
    """Plan and apply an explicit-target restore (ADR 0049 §4)."""


@restore_group.command("plan", cls=PhloCommand)
@click.option(
    "--backup-set",
    "backup_set",
    type=click.Path(file_okay=False, path_type=Path),
    required=True,
    help="Path to the finalized backup set directory.",
)
@click.option(
    "--target",
    type=click.Path(file_okay=False, path_type=Path),
    required=True,
    help="Explicit, new/empty target deployment directory.",
)
@click.option("--json", "output_json", is_flag=True, help="Emit a structured command result.")
@click.option("--format", "output_format", type=click.Choice(["json", "table"]), default="table")
def restore_plan_cmd(
    backup_set: Path, target: Path, output_format: str, output_json: bool = False
) -> None:
    """Create a mutation-free restore plan bound to set digest + target."""
    from phlo.capabilities.continuity import RestoreTarget
    from phlo.operations.restore import RestoreError, plan_restore

    try:
        plan = plan_restore(backup_set_dir=backup_set, target=RestoreTarget.of(target))
    except RestoreError as exc:
        raise click.ClickException(
            f"restore plan failed: {exc.code} ({', '.join(exc.identifiers)})"
        ) from exc

    if output_json or output_format == "json":
        _emit(plan.to_dict())
    else:
        click.echo(
            f"Restore plan {plan.plan_token}: set {plan.backup_set_id} → {plan.target.target_id}"
        )
        click.echo("No changes applied. Save the plan with --format json before applying it.")


@restore_group.command("apply", cls=PhloCommand)
@click.option(
    "--plan",
    "plan_path",
    type=click.Path(exists=True),
    required=True,
    help="Path to the JSON restore plan file.",
)
@click.option("--confirmation-token", required=True, help="The plan token from the plan step.")
@click.option(
    "--fixture-substrate",
    is_flag=True,
    help="Acknowledge this restore stage only files beneath the target and does not mutate a live deployment.",
)
@click.option("--json", "output_json", is_flag=True, help="Emit a structured command result.")
@click.option("--format", "output_format", type=click.Choice(["json", "table"]), default="table")
@require_mutation_authorization("operations.restore.apply")
def restore_apply_cmd(
    plan_path: str,
    confirmation_token: str,
    fixture_substrate: bool,
    output_format: str,
    output_json: bool = False,
) -> None:
    """Apply a plan only to its bound target (authorized, fail-before-mutation)."""
    from phlo.capabilities.continuity import RestorePlan
    from phlo.operations.backup import default_backup_contributors
    from phlo.operations.restore import RestoreError, restore_apply

    _require_fixture_substrate(fixture_substrate, "restore")

    plan = RestorePlan.from_dict(_read_json(plan_path))
    contributors = dict(default_backup_contributors())
    journal = _durable_journal()
    try:
        result = restore_apply(
            plan=plan,
            confirmation_token=confirmation_token,
            contributors=contributors,
            journal=journal,
        )
    except RestoreError as exc:
        raise click.ClickException(
            f"restore apply failed: {exc.code} ({', '.join(exc.identifiers)})"
        ) from exc

    if output_json or output_format == "json":
        _emit(
            {
                **result.to_dict(),
                "operational": False,
                "substrate": "fixture",
                "note": "fixture substrate only: staged files under the target, not a live deployment restore",
            }
        )
    else:
        click.echo(
            f"Restore to {result.target_id}: {result.state} "
            f"(FIXTURE SUBSTRATE — not a live deployment restore)"
        )
        for step in result.steps:
            click.echo(
                f"  {step.provider}: {step.state.value} ({step.phase.value}, "
                f"retry_safe={step.retry_safe})"
            )

    if not result.accepted:
        raise SystemExit(1)


@operations_group.group("upgrade", cls=PhloGroup)
def upgrade_group() -> None:
    """Prove the supported deployment upgrade pair (ADR 0049 §5)."""


@upgrade_group.command("plan", cls=PhloCommand)
@click.option("--from", "from_version", required=True, help="Previous version (fixture pair).")
@click.option("--to", "to_version", required=True, help="Candidate version (fixture pair).")
@click.option(
    "--backup-set", "backup_set", type=click.Path(file_okay=False, path_type=Path), required=True
)
@click.option("--target", type=click.Path(file_okay=False, path_type=Path), required=True)
@click.option("--json", "output_json", is_flag=True, help="Emit a structured command result.")
@click.option("--format", "output_format", type=click.Choice(["json", "table"]), default="table")
def upgrade_plan_cmd(
    from_version: str,
    to_version: str,
    backup_set: Path,
    target: Path,
    output_format: str,
    output_json: bool = False,
) -> None:
    """Create a mutation-free upgrade plan after a verified backup."""
    from phlo.capabilities.continuity import RestoreTarget
    from phlo.operations.upgrade import UpgradeError, plan_upgrade

    try:
        plan = plan_upgrade(
            from_version=from_version,
            to_version=to_version,
            backup_set_dir=backup_set,
            target=RestoreTarget.of(target),
        )
    except UpgradeError as exc:
        raise click.ClickException(
            f"upgrade plan failed: {exc.code} ({', '.join(exc.identifiers)})"
        ) from exc

    if output_json or output_format == "json":
        _emit(plan.to_dict())
    else:
        click.echo(
            f"Upgrade plan {plan.plan_token}: {plan.from_version} → {plan.to_version} "
            f"(backup {plan.backup_set_id})"
        )
        click.echo("No changes applied. Save the plan with --format json before applying it.")


@upgrade_group.command("apply", cls=PhloCommand)
@click.option("--plan", "plan_path", type=click.Path(exists=True), required=True)
@click.option("--confirmation-token", required=True)
@click.option(
    "--fixture-substrate",
    is_flag=True,
    help="Acknowledge this upgrade stages version markers only and does not migrate a live deployment.",
)
@click.option("--json", "output_json", is_flag=True, help="Emit a structured command result.")
@click.option("--format", "output_format", type=click.Choice(["json", "table"]), default="table")
@require_mutation_authorization("operations.upgrade.apply")
def upgrade_apply_cmd(
    plan_path: str,
    confirmation_token: str,
    fixture_substrate: bool,
    output_format: str,
    output_json: bool = False,
) -> None:
    """Apply the bound upgrade (authorized, requires verified backup)."""
    from phlo.operations.backup import default_backup_contributors
    from phlo.operations.upgrade import UpgradeError, UpgradePlan, upgrade_apply

    _require_fixture_substrate(fixture_substrate, "upgrade")

    plan = UpgradePlan.from_dict(_read_json(plan_path))
    contributors = dict(default_backup_contributors())
    journal = _durable_journal()
    try:
        result = upgrade_apply(
            plan=plan,
            confirmation_token=confirmation_token,
            contributors=contributors,
            journal=journal,
        )
    except UpgradeError as exc:
        raise click.ClickException(
            f"upgrade apply failed: {exc.code} ({', '.join(exc.identifiers)})"
        ) from exc

    if output_json or output_format == "json":
        _emit(
            {
                **result.to_dict(),
                "operational": False,
                "substrate": "fixture",
                "note": "fixture substrate only: staged version markers, not a live deployment upgrade",
            }
        )
    else:
        click.echo(
            f"Upgrade {result.from_version} → {result.to_version}: {result.state} "
            f"(FIXTURE SUBSTRATE — not a live deployment upgrade)"
        )
        for step in result.steps:
            click.echo(f"  {step.name}: {step.state.value} ({step.phase.value})")

    if not result.accepted:
        raise SystemExit(1)


@maintenance_group.command("inventory", cls=PhloCommand)
@click.option("--json", "output_json", is_flag=True, help="Emit a structured command result.")
@click.option("--format", "output_format", type=click.Choice(["json", "table"]), default="table")
def maintenance_inventory(output_format: str, output_json: bool = False) -> None:
    """List v1 tables with their provider and maintenance state (read-only)."""
    from phlo.capabilities import list_capabilities, resolve_capability
    from phlo.capabilities.discovery import discover_capabilities

    discover_capabilities()
    executors = list_capabilities("maintenance_executor")
    if not executors:
        raise click.ClickException("no maintenance executor capability is registered")

    inventory: list[dict[str, Any]] = []
    for name in executors:
        resolution = resolve_capability("maintenance_executor", name)
        if resolution is None:
            continue
        provider = resolution.provider
        get_inventory = getattr(provider, "get_inventory", None)
        if callable(get_inventory):
            inventory.extend(get_inventory())

    if output_json or output_format == "json":
        _emit({"executors": executors, "tables": inventory})
    else:
        click.echo(f"Executors: {', '.join(executors)}")
        for entry in inventory:
            click.echo(f"  {entry}")


@maintenance_group.command("plan", cls=PhloCommand)
@click.option("--operation", type=click.Choice(["compact", "snapshot_expiry"]), required=True)
@click.option("--table", required=True, help="Fully qualified table name.")
@click.option("--ref", default="main", help="Catalog ref/branch.")
@click.option("--json", "output_json", is_flag=True, help="Emit a structured command result.")
@click.option("--format", "output_format", type=click.Choice(["json", "table"]), default="table")
def maintenance_plan(
    operation: str, table: str, ref: str, output_format: str, output_json: bool = False
) -> None:
    """Create a deterministic maintenance plan (read-only, no mutation)."""
    from phlo.capabilities import resolve_capability
    from phlo.capabilities.discovery import discover_capabilities

    discover_capabilities()
    resolution = resolve_capability("maintenance_executor", operation)
    if resolution is None:
        raise click.ClickException(f"no maintenance executor registered for {operation!r}")

    provider = resolution.provider
    plan_fn = getattr(provider, "plan", None)
    if not callable(plan_fn):
        raise click.ClickException(
            f"maintenance executor {resolution.name!r} does not support planning"
        )

    result = plan_fn(table_name=table, ref=ref)
    plan = result.to_dict() if hasattr(result, "to_dict") else dict(result)
    rejected = plan.get("accepted") is False or plan.get("status") in {"blocked", "failed"}
    valid = not rejected and bool(plan.get("plan_token"))
    if output_json or output_format == "json":
        _emit(
            plan,
            status="planned" if valid else "error",
            reason_code=None if valid else "maintenance_plan_rejected",
        )
    elif not valid:
        click.echo(f"Maintenance plan for {table}: {plan.get('status', 'invalid')}")
        if plan.get("failure"):
            click.echo(f"  {plan['failure']}")
    else:
        click.echo(f"Maintenance plan: {operation} on {table} (ref {ref})")
        click.echo(f"Confirmation token: {plan.get('plan_token', '(unavailable)')}")
        click.echo("No changes applied. Save the plan with --format json before applying it.")
    if not valid:
        raise click.exceptions.Exit(1)


@maintenance_group.command("apply", cls=PhloCommand)
@click.option(
    "--plan",
    "plan_path",
    type=click.Path(exists=True),
    required=True,
    help="Path to the JSON plan file.",
)
@click.option("--confirmation-token", required=True, help="The plan token from the plan step.")
@click.option("--json", "output_json", is_flag=True, help="Emit a structured command result.")
@click.option("--format", "output_format", type=click.Choice(["json", "table"]), default="table")
@require_mutation_authorization("operations.maintenance.apply")
def maintenance_apply(
    plan_path: str, confirmation_token: str, output_format: str, output_json: bool = False
) -> None:
    """Apply an exact, still-current maintenance plan (authorized, fail-before-mutation)."""
    from pathlib import Path

    from phlo.capabilities import resolve_capability
    from phlo.capabilities.discovery import discover_capabilities
    from phlo.operations.journal import (
        OperationJournalError,
        OperationJournalState,
        claim_operation,
        complete_operation,
        mark_submitted,
    )

    with Path(plan_path).open(encoding="utf-8") as f:
        plan_data = json.load(f)

    plan_token = plan_data.get("plan_token", "")
    operation = plan_data.get("operation", "")
    table = plan_data.get("table_name", "")
    ref = plan_data.get("ref", "main")

    if not plan_token or plan_token != confirmation_token:
        raise click.ClickException("confirmation token does not match the plan token")

    if operation == "orphan_delete":
        raise click.ClickException("orphan deletion is unsupported in v1")

    discover_capabilities()
    resolution = resolve_capability("maintenance_executor", operation)
    if resolution is None:
        raise click.ClickException(f"no maintenance executor registered for {operation!r}")

    provider = resolution.provider
    execute_fn = getattr(provider, "execute", None)
    if not callable(execute_fn):
        raise click.ClickException(
            f"maintenance executor {resolution.name!r} does not support execution"
        )

    journal = _durable_journal()
    operation_id = f"{operation}:{table}:{ref}"
    try:
        claim_operation(
            journal,
            operation_id=operation_id,
            subject="operator",
            action=operation,
            target=table,
            plan_token=plan_token,
        )
        mark_submitted(journal, operation_id)
        result = execute_fn(table_name=table, ref=ref, plan_token=plan_token)
        result_dict = result.to_dict() if hasattr(result, "to_dict") else dict(result)
        rejected = result_dict.get("accepted") is False or result_dict.get("status") in {
            "blocked",
            "failed",
        }
        if rejected:
            # Preserve provider evidence while refusing contradictory accepted/status success.
            if not journal.transition(operation_id, OperationJournalState.FAILED, result_dict):
                raise OperationJournalError("unknown_operation", (operation_id,))
        else:
            complete_operation(journal, operation_id, result_dict)
        if output_json or output_format == "json":
            _emit(
                result_dict,
                status="error" if rejected else "success",
                reason_code="maintenance_rejected" if rejected else None,
            )
        else:
            click.echo(
                f"Maintenance {operation} on {table}: {result_dict.get('status', 'unknown')}"
            )
        if rejected:
            raise click.exceptions.Exit(1)
    except OperationJournalError as exc:
        raise click.ClickException(
            f"journal error: {exc.code} ({', '.join(exc.identifiers)})"
        ) from exc
