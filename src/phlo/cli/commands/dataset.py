"""Dataset workflow CLI commands.

Canonical Dataset authority commands:

- ``phlo dataset show <dataset_id>`` returns the one canonical Dataset
  projection built by :mod:`phlo.dataset_projection` over the core service:
  identity, owner, classifications, workflow/publication state, controls and
  evidence, ordered readiness reasons, and allowed transitions. ``--json``
  emits the projection verbatim so CLI JSON and the API Profile agree.
- ``phlo dataset list`` projects every governed table on the surface.
- ``phlo dataset transition <dataset_id> <action>`` applies one authorized
  compare-and-set transition through the core service: the client
  ``--action-id`` idempotency key drives replay, every attempt is audited in
  the durable store, and blocked attempts print the ordered policy reasons
  without writing.

`phlo dataset migrate-overlay` runs the explicit overlay migration:

- ``plan`` is read-only: it maps every legacy Observatory workflow record in
  ``.phlo/observatory/dataset_workflow.json`` through one deterministic
  import-or-reject rule and writes a plan document; it never touches a store.
- ``apply`` is an authorized, digest-confirmed, transactional import: it
  verifies the plan digest against the current source file, then commits every
  imported record plus the workflow configuration inside one durable-store
  transaction. Re-applying the same source replays the stored result without
  duplicate state. The legacy file is never modified or deleted.
- ``discard`` is the explicit, audited alternative to apply: the planned
  overlay is discarded, the legacy file is retained, and the discard is
  journaled in the durable store's audit stream.

Apply, discard, and transition require CLI mutation authorization;
plan, show, and list are reads. Registered into the phlo CLI by
src/phlo/cli/main.py.
"""

from __future__ import annotations

import json
import os
import uuid
from pathlib import Path
from typing import Any

import click

from phlo.cli.authorization import CliPrincipalResolver
from phlo.cli.authorization_wrappers import require_mutation_authorization
from phlo.cli.contract import PhloCommand, PhloGroup
from phlo.cli.output import json_envelope
from phlo.dataset.migration import (
    LegacyOverlayError,
    MigrationPlan,
    MigrationStore,
    import_action_id,
    plan_migration,
    source_file_digest,
)
from phlo.dataset.models import PUBLICATION_ACTIONS, WORKFLOW_ACTIONS, TransitionRequest
from phlo.dataset.store import StoreWriteStatus
from phlo.dataset_projection import DatasetAuthority, build_dataset_authority
from phlo.dataset_state import resolve_dataset_state_store

APPLY_COMMAND = "dataset.migrate_overlay_apply"
DISCARD_COMMAND = "dataset.migrate_overlay_discard"
TRANSITION_COMMAND = "dataset.transition"
TRANSITION_SCOPE = "lakehouse:operate"

MIGRATE_OVERLAY_APPLY = "apply"
MIGRATE_OVERLAY_DISCARD = "discard"

_TRANSITION_ACTIONS = sorted(WORKFLOW_ACTIONS | PUBLICATION_ACTIONS)


@click.group(name="dataset", cls=PhloGroup)
def dataset_group() -> None:
    """Dataset workflow commands."""


@dataset_group.command(name="show", cls=PhloCommand)
@click.argument("dataset_id")
@click.option("--action", "action", default=None, help="Evaluate readiness for this action.")
@click.option(
    "--store-mode",
    "store_mode",
    type=click.Choice(["durable", "memory"]),
    default=None,
    help="Override the store mode (default: durable; memory is the explicit test mode).",
)
@click.option("--json", "output_json", is_flag=True, help="Emit the canonical projection as JSON.")
def show_cmd(
    dataset_id: str, action: str | None, store_mode: str | None, output_json: bool
) -> None:
    """Show the canonical Dataset projection for one Dataset ID."""
    authority = _authority(store_mode)
    projection = authority.projection(dataset_id, action)
    if output_json:
        click.echo(json_envelope(data=projection))
        return
    readiness = projection["readiness"]
    state = projection["publication_state"] or projection["workflow_state"] or "open"
    click.echo(f"Dataset:      {projection['dataset_id']}")
    click.echo(f"Table:        {projection['table_id']}")
    click.echo(f"Owner:        {projection['owner'] or '-'}")
    click.echo(f"Classified:   {', '.join(projection['classifications']) or '-'}")
    click.echo(f"State:        {state}")
    click.echo(f"Ready ({readiness['action']}): {readiness['ready']}")
    for reason in readiness["reasons"]:
        click.echo(f"  - {reason}")


@dataset_group.command(name="list", cls=PhloCommand)
@click.option(
    "--store-mode",
    "store_mode",
    type=click.Choice(["durable", "memory"]),
    default=None,
    help="Override the store mode (default: durable; memory is the explicit test mode).",
)
@click.option("--json", "output_json", is_flag=True, help="Emit canonical projections as JSON.")
def list_cmd(store_mode: str | None, output_json: bool) -> None:
    """List canonical projections for every governed table."""
    authority = _authority(store_mode)
    projections = [authority.projection(table) for table in sorted(authority.surface.tables)]
    if output_json:
        click.echo(json_envelope(data={"datasets": projections}))
        return
    if not projections:
        click.echo("No governed datasets found.")
    for projection in projections:
        state = projection["publication_state"] or projection["workflow_state"] or "open"
        readiness = projection["readiness"]
        flag = "ready" if readiness["ready"] else f"blocked ({len(readiness['reasons'])})"
        click.echo(f"{projection['dataset_id']}: state={state} {readiness['action']}={flag}")


@dataset_group.command(name="transition", cls=PhloCommand)
@click.argument("dataset_id")
@click.argument("action", type=click.Choice(_TRANSITION_ACTIONS))
@click.option(
    "--action-id",
    "action_id",
    default=None,
    help="Client idempotency key; reuse it to replay the committed outcome (default: generated).",
)
@click.option(
    "--expected-state",
    "expected_state",
    default=None,
    help="Compare-and-set pre-state the caller observed (default: read from the store).",
)
@click.option(
    "--owner",
    "owner",
    default=None,
    help="Operating owner recorded when a claim creates the candidate (default: the operator).",
)
@click.option(
    "--store-mode",
    "store_mode",
    type=click.Choice(["durable", "memory"]),
    default=None,
    help="Override the store mode (default: durable; memory is the explicit test mode).",
)
@click.option("--json", "output_json", is_flag=True, help="Emit machine-readable JSON.")
@require_mutation_authorization(TRANSITION_COMMAND)
def transition_cmd(
    dataset_id: str,
    action: str,
    action_id: str | None,
    expected_state: str | None,
    owner: str | None,
    store_mode: str | None,
    output_json: bool,
) -> None:
    """Apply one authorized Dataset transition through the core service."""
    actor = _actor()
    resolved_action_id = action_id or f"cli-{uuid.uuid4()}"
    authority = _authority(store_mode)
    outcome = authority.transition(
        TransitionRequest(
            resource_id=dataset_id,
            action=action,
            action_id=resolved_action_id,
            actor=actor,
            scope=TRANSITION_SCOPE,
            expected_state=expected_state,
            owner=owner or actor,
        )
    )
    payload = _transition_payload(outcome)
    if output_json:
        click.echo(json_envelope(data=payload))
    else:
        click.echo(f"{payload['status']}: {payload['message']}")
        for reason in payload["reasons"]:
            click.echo(f"  - {reason}")
    if payload["status"] in {"conflict", "blocked"}:
        raise click.exceptions.Exit(1)


def _transition_payload(outcome: Any) -> dict[str, Any]:
    verdict = outcome.verdict
    return {
        "status": outcome.status.value,
        "action_id": outcome.request.action_id,
        "resource_id": outcome.request.resource_id,
        "action": outcome.request.action,
        "actor": outcome.request.actor,
        "before_state": outcome.before_state,
        "after_state": outcome.after_state,
        "message": outcome.message,
        "reasons": list(verdict.reasons) if verdict is not None else [],
        "record": outcome.record.to_read_model() if outcome.record else None,
        "audit": outcome.audit.to_read_model() if outcome.audit else None,
    }


def _authority(store_mode: str | None) -> DatasetAuthority:
    """Build the canonical authority, failing closed with guidance."""
    try:
        return build_dataset_authority(_project_root(), store_mode=store_mode)
    except Exception as exc:
        raise click.ClickException(f"Dataset authority unavailable: {exc}") from exc


@dataset_group.group(name="migrate-overlay", cls=PhloGroup)
def migrate_overlay_group() -> None:
    """Plan, apply, or discard the legacy Observatory dataset workflow overlay."""


@migrate_overlay_group.command(name="plan", cls=PhloCommand)
@click.option(
    "--source",
    "source",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    required=True,
    help="Path to the legacy .phlo/observatory/dataset_workflow.json overlay.",
)
@click.option(
    "--output",
    "output",
    type=click.Path(dir_okay=False, path_type=Path),
    default=None,
    help="Write the plan document to this path (default: stdout).",
)
@click.option("--json", "output_json", is_flag=True, help="Emit the full plan document as JSON.")
def plan_cmd(source: Path, output: Path | None, output_json: bool) -> None:
    """Plan the overlay import without touching any store (read-only)."""
    plan = _load_plan(source)
    document = plan.to_read_model()
    if output is not None:
        output.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        if not output_json:
            click.echo(f"Wrote migration plan to {output}")
    if output_json:
        click.echo(json_envelope(data=document, status="planned"))
        return
    click.echo(f"Source digest: {plan.source_digest}")
    click.echo(f"Plan digest:   {plan.plan_digest()}")
    click.echo(f"Records:       {len(plan.imports)} import(s), {len(plan.rejections)} rejection(s)")
    for entry in plan.rejections:
        click.echo(f"  REJECT {entry.record_id}: {entry.reason}", err=True)
    if plan.rejections:
        click.echo("Rejections fail closed; they are reported and never imported.")


@migrate_overlay_group.command(name="apply", cls=PhloCommand)
@click.option(
    "--source",
    "source",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    required=True,
    help="Path to the legacy overlay the plan was built from.",
)
@click.option(
    "--plan",
    "plan_path",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    required=True,
    help="Path to the PLAN.json produced by `phlo dataset migrate-overlay plan`.",
)
@click.option("--digest", required=True, help="The plan digest printed by the plan command.")
@click.option(
    "--store-mode",
    "store_mode",
    type=click.Choice(["durable", "memory"]),
    default=None,
    help="Override the store mode (default: durable; memory is the explicit test mode).",
)
@click.option("--json", "output_json", is_flag=True, help="Emit machine-readable JSON.")
@require_mutation_authorization(APPLY_COMMAND)
def apply_cmd(
    source: Path,
    plan_path: Path,
    digest: str,
    store_mode: str | None,
    output_json: bool,
) -> None:
    """Import the planned overlay once, digest-confirmed and idempotent."""
    plan = _confirmed_plan(source, plan_path, digest)
    store = _migration_store(store_mode)
    actor = _actor()
    result = store.commit_migration(
        records=plan.records,
        config=plan.config,
        action_id=import_action_id(plan.source_digest),
        fingerprint=plan.plan_digest(),
        actor=actor,
        scope=TRANSITION_SCOPE,
    )
    payload: dict[str, Any] = {
        "status": result.status.value,
        "records": [record.to_read_model() for record in result.records],
        "record_ids": [record.dataset_id for record in result.records],
        "detail": result.detail,
        "source_digest": plan.source_digest,
        "plan_digest": plan.plan_digest(),
        "rejections": [entry.to_read_model() for entry in plan.rejections],
        "actor": actor,
    }
    if output_json:
        click.echo(json_envelope(data=payload))
        if result.status not in {StoreWriteStatus.COMMITTED, StoreWriteStatus.REPLAYED}:
            raise click.exceptions.Exit(1)
        return
    click.echo(f"Overlay migration {result.status.value}: {result.detail}")
    for record in result.records:
        click.echo(f"  {record.dataset_id}")
    for entry in plan.rejections:
        click.echo(f"  REJECTED (not imported) {entry.record_id}: {entry.reason}", err=True)
    if result.status not in {StoreWriteStatus.COMMITTED, StoreWriteStatus.REPLAYED}:
        raise click.exceptions.Exit(1)


@migrate_overlay_group.command(name="discard", cls=PhloCommand)
@click.option(
    "--source",
    "source",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    required=True,
    help="Path to the legacy overlay the plan was built from.",
)
@click.option(
    "--plan",
    "plan_path",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    required=True,
    help="Path to the PLAN.json produced by `phlo dataset migrate-overlay plan`.",
)
@click.option("--digest", required=True, help="The plan digest printed by the plan command.")
@click.option(
    "--store-mode",
    "store_mode",
    type=click.Choice(["durable", "memory"]),
    default=None,
    help="Override the store mode (default: durable; memory is the explicit test mode).",
)
@click.option("--json", "output_json", is_flag=True, help="Emit machine-readable JSON.")
@require_mutation_authorization(DISCARD_COMMAND)
def discard_cmd(
    source: Path,
    plan_path: Path,
    digest: str,
    store_mode: str | None,
    output_json: bool,
) -> None:
    """Explicitly discard the planned overlay import (audited; legacy file retained)."""
    plan = _confirmed_plan(source, plan_path, digest)
    store = _migration_store(store_mode)
    actor = _actor()
    store.record_discard(
        source_digest=plan.source_digest,
        plan_digest=plan.plan_digest(),
        actor=actor,
        scope=TRANSITION_SCOPE,
    )
    payload: dict[str, Any] = {
        "status": "discarded",
        "source_digest": plan.source_digest,
        "plan_digest": plan.plan_digest(),
        "actor": actor,
        "legacy_source_retained": True,
    }
    if output_json:
        click.echo(json_envelope(data=payload))
        return
    click.echo("Overlay import discarded and audited; the legacy file is retained untouched.")


def _load_plan(source: Path) -> MigrationPlan:
    """Plan one legacy overlay file, failing closed on unreadable input."""
    try:
        payload = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise click.ClickException(
            f"Legacy overlay {source} cannot be read as JSON: {exc}"
        ) from exc
    try:
        return plan_migration(payload, source_digest=source_file_digest(str(source)))
    except LegacyOverlayError as exc:
        raise click.ClickException(f"Legacy overlay fails closed: {exc}") from exc


def _confirmed_plan(source: Path, plan_path: Path, digest: str) -> MigrationPlan:
    """Re-derive the plan and confirm both digests, failing closed on mismatch."""
    fresh = _load_plan(source)
    try:
        document = json.loads(plan_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise click.ClickException(f"Plan file {plan_path} cannot be read as JSON: {exc}") from exc
    if not isinstance(document, dict) or "plan_digest" not in document:
        raise click.ClickException(f"Plan file {plan_path} is not a migration plan document.")
    if digest != document.get("plan_digest"):
        raise click.ClickException(
            f"Digest confirmation failed: --digest {digest!r} does not match the plan's digest."
        )
    if document.get("plan_digest") != fresh.plan_digest():
        raise click.ClickException(
            "Digest confirmation failed: the source file changed since the plan was made. "
            "Re-run `phlo dataset migrate-overlay plan`."
        )
    return fresh


def _migration_store(store_mode: str | None) -> MigrationStore:
    store = resolve_dataset_state_store(_project_root(), mode=store_mode)
    if not isinstance(store, MigrationStore):
        raise click.ClickException(
            "The resolved dataset state store does not support overlay migration."
        )
    return store


def _project_root() -> str:
    return str(Path(os.environ.get("PHLO_PROJECT_PATH", Path.cwd())).resolve())


def _actor() -> str | None:
    return CliPrincipalResolver.resolve().subject
