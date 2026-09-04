"""Dataset workflow CLI commands.

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

Apply and discard require CLI mutation authorization; plan is a read.
Registered into the phlo CLI by src/phlo/cli/main.py.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import click

from phlo.cli.authorization import CliPrincipalResolver
from phlo.cli.authorization_wrappers import require_mutation_authorization
from phlo.dataset.migration import (
    LegacyOverlayError,
    MigrationPlan,
    MigrationStore,
    import_action_id,
    plan_migration,
    source_file_digest,
)
from phlo.dataset.store import StoreWriteStatus
from phlo.dataset_state import resolve_dataset_state_store

APPLY_COMMAND = "dataset.migrate_overlay_apply"
DISCARD_COMMAND = "dataset.migrate_overlay_discard"
TRANSITION_SCOPE = "lakehouse:operate"

MIGRATE_OVERLAY_APPLY = "apply"
MIGRATE_OVERLAY_DISCARD = "discard"


@click.group(name="dataset")
def dataset_group() -> None:
    """Dataset workflow commands."""


@dataset_group.group(name="migrate-overlay")
def migrate_overlay_group() -> None:
    """Plan, apply, or discard the legacy Observatory dataset workflow overlay."""


@migrate_overlay_group.command(name="plan")
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
        click.echo(f"Wrote migration plan to {output}")
    if output_json:
        click.echo(json.dumps(document, indent=2, sort_keys=True))
        return
    click.echo(f"Source digest: {plan.source_digest}")
    click.echo(f"Plan digest:   {plan.plan_digest()}")
    click.echo(f"Records:       {len(plan.imports)} import(s), {len(plan.rejections)} rejection(s)")
    for entry in plan.rejections:
        click.echo(f"  REJECT {entry.record_id}: {entry.reason}", err=True)
    if plan.rejections:
        click.echo("Rejections fail closed; they are reported and never imported.")


@migrate_overlay_group.command(name="apply")
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
        click.echo(json.dumps(payload, indent=2, sort_keys=True))
        return
    click.echo(f"Overlay migration {result.status.value}: {result.detail}")
    for record in result.records:
        click.echo(f"  {record.dataset_id}")
    for entry in plan.rejections:
        click.echo(f"  REJECTED (not imported) {entry.record_id}: {entry.reason}", err=True)
    if result.status not in {StoreWriteStatus.COMMITTED, StoreWriteStatus.REPLAYED}:
        raise click.exceptions.Exit(1)


@migrate_overlay_group.command(name="discard")
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
        click.echo(json.dumps(payload, indent=2, sort_keys=True))
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
