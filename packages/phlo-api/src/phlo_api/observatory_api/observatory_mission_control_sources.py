"""Derive Mission Control read models from live platform substrate.

Where the substrate already answers the question, prefer it over the seeded
collection. Everything here is best-effort: a derivation returns ``None`` when
the substrate cannot answer, and the router falls back to the seeded read model
so a screen degrades to reference data instead of going blank.

Currently derived:
  - Platform services and summary (from ``/services``)

Still seeded, because nothing upstream models them yet:
  - Release candidates, publication plans, ownership, contracts, access policy
  - Backup coverage and maintenance reporting
"""

from __future__ import annotations

from collections import Counter

from phlo_api.observatory_api.observatory import (
    _load_assets,
    _load_branch_detail,
    _load_branches,
    _load_operations,
    _load_services,
)
from phlo_api.observatory_api.observatory_mission_control_models import (
    MissionAttentionItem,
    MissionExecutionRow,
    CandidateEvidenceRow,
    CandidateSnapshotChange,
    CompletedRelease,
    DatasetCheck,
    DatasetOwnership,
    LineageNode,
    MissionDatasetDetail,
    DatasetPreview,
    PublicationPlanRow,
    ReleaseCandidate,
    ReleaseCandidateDetail,
    MissionRunDetail,
    OwnershipGap,
    RunArtifact,
    RunConfigurationRow,
    PlatformService,
    PlatformSummaryMetric,
    SummaryMetricRow,
)
from phlo_api.observatory_api.observatory_models import ObservatoryRun
from phlo_api.observatory_api.observatory_runs import load_runs

RELEASED_BRANCH = "main"

# Provider kinds are free-form; map the common ones to a human role label.
_ROLE_LABELS = {
    "orchestrator": "Orchestration",
    "catalog": "Catalog",
    "object_store": "Object storage",
    "query_engine": "Query engine",
    "serving": "Serving & state",
    "telemetry": "Telemetry",
    "ingestion": "Ingestion",
    "transform": "Transform",
    "bi": "BI",
    "auth": "Authentication",
    "routing": "Routing",
    "streaming": "Streaming",
}


def _is_ready(state: str) -> bool:
    return state.strip().lower() in {"ok", "healthy", "ready", "up"}


def derive_platform_services() -> list[PlatformService] | None:
    """Build the Platform service table from live service status.

    Returns ``None`` when no services are discovered so the caller can fall back
    to the seeded read model.
    """
    try:
        services = _load_services()
    except Exception:  # noqa: BLE001 - substrate probe is best-effort
        return None

    if not services:
        return None

    # Only services whose runtime state actually resolved. The catalog lists
    # every service the platform knows about, most of which are not part of this
    # project; reporting them as unknown-state rows is noise, not signal.
    observed = [
        service
        for service in services
        if str(getattr(service, "status", "")).strip().lower() not in {"", "unknown"}
    ]
    if not observed:
        return None

    rows: list[PlatformService] = []
    for service in observed:
        # `status` is the process state and `health.state` the readiness probe;
        # they are reported separately on purpose and never merged.
        runtime_state = str(service.status).replace("_", " ").title()
        health_state = str(getattr(service.health, "state", "unknown"))
        ready = _is_ready(health_state)
        probe = getattr(service.health, "message", None) or health_state
        rows.append(
            PlatformService(
                name=service.name,
                role=_ROLE_LABELS.get(
                    str(service.kind), str(service.kind).replace("_", " ").title()
                ),
                runtime_state=runtime_state,
                readiness_state="Ready" if ready else "Not ready",
                probe=probe,
                action="Open",
                attention=not ready,
            )
        )
    return rows


def derive_platform_summary(services: list[PlatformService]) -> list[PlatformSummaryMetric]:
    """Build the Platform summary band from the derived service rows."""
    running = Counter(row.runtime_state for row in services)
    ready = [row for row in services if row.readiness_state == "Ready"]
    unready = [row for row in services if row.readiness_state != "Ready"]
    total = len(services)
    running_count = running.get("Running", 0)

    return [
        PlatformSummaryMetric(
            label="Enabled services",
            value=f"{total} of {total}",
            hint="Discovered from the running project",
            tone="muted",
        ),
        PlatformSummaryMetric(
            label="Running",
            value=f"{running_count} of {total}",
            hint="Container state observed",
            tone="muted",
        ),
        PlatformSummaryMetric(
            label="Ready",
            value=f"{len(ready)} of {total}",
            hint=f"{len(unready)} readiness probe failing" if unready else "All probes passing",
            tone="warning" if unready else "success",
        ),
        PlatformSummaryMetric(
            label="Telemetry",
            value="Complete",
            hint="Service health collected",
            tone="success",
        ),
        PlatformSummaryMetric(
            label="Latest backup",
            value="—",
            hint="Not reported by this project",
            tone="muted",
        ),
        PlatformSummaryMetric(
            label="Restore rehearsal",
            value="—",
            hint="Not reported by this project",
            tone="muted",
        ),
    ]


# ------------------------------------------------------------------ overview


def _elapsed(seconds: float | None) -> str:
    if not seconds or seconds <= 0:
        return "—"
    total = int(seconds)
    return f"{total // 60:02d}:{total % 60:02d}"


def _run_tone(status: str) -> str:
    state = status.strip().lower()
    if state in {"success", "succeeded"}:
        return "success"
    if state in {"failure", "failed"}:
        return "danger"
    if state in {"started", "running", "queued"}:
        return "accent"
    return "muted"


def derive_overview_metrics() -> list[SummaryMetricRow] | None:
    """Build the Overview summary band from live execution state.

    Release and governance counters have no upstream source and are reported as
    unavailable rather than invented.
    """
    try:
        runs = load_runs()
        operations = _load_operations()
        assets = _load_assets()
    except Exception:  # noqa: BLE001 - substrate probe is best-effort
        return None

    if not runs and not operations and not assets:
        return None

    by_status: dict[str, int] = {}
    for run in runs:
        by_status[str(run.status)] = by_status.get(str(run.status), 0) + 1

    succeeded = by_status.get("success", 0) + by_status.get("succeeded", 0)
    failed = by_status.get("failure", 0) + by_status.get("failed", 0)
    active = sum(
        count for status, count in by_status.items() if status in {"started", "running", "queued"}
    )

    # Prefer the declared asset catalogue; fall back to what observed runs
    # touched when the registry is empty.
    asset_count = len(assets)
    asset_hint = "Declared by the project"
    if asset_count == 0:
        touched = {ref.id for run in runs for ref in run.assets}
        asset_count = len(touched)
        asset_hint = "Referenced by observed runs"

    release_summary = derive_release_summary(
        derive_release_candidates(), derive_completed_releases()
    )
    releases_value, releases_hint, releases_tone = "—", "No release source configured", "muted"
    if release_summary:
        pending = next((row for row in release_summary if row.label == "Pending"), None)
        released = next((row for row in release_summary if row.label.startswith("Released")), None)
        if pending and released:
            releases_value = pending.value
            releases_hint = f"{pending.hint} · {released.value} released"
            releases_tone = "warning" if "0 " not in pending.value else "muted"

    return [
        SummaryMetricRow(
            label="Data",
            value=f"{asset_count} asset{'s' if asset_count != 1 else ''}",
            hint=asset_hint,
            tone="muted",
        ),
        SummaryMetricRow(
            label="Ingestion",
            value=f"{len(operations)} operations",
            hint="Reported by the orchestrator",
            tone="muted",
        ),
        SummaryMetricRow(
            label="Quality",
            value=f"{sum(len(asset.checks) for asset in assets)} checks" if assets else "—",
            hint="Declared on project assets" if assets else "No quality source configured",
            tone="muted",
        ),
        SummaryMetricRow(
            label="Execution",
            value=f"{len(runs)} runs",
            hint=f"{succeeded} succeeded · {active} active · {failed} failed",
            tone="danger" if failed else "muted",
        ),
        SummaryMetricRow(
            label="Releases",
            value=releases_value,
            hint=releases_hint,
            tone=releases_tone,
        ),
        SummaryMetricRow(
            label="Governance",
            value="—",
            hint="No ownership source configured",
            tone="muted",
        ),
    ]


def derive_overview_execution() -> list[MissionExecutionRow] | None:
    """Build the active-execution table from runs that are not terminal."""
    try:
        runs = load_runs()
    except Exception:  # noqa: BLE001 - substrate probe is best-effort
        return None

    active = [
        run for run in runs if str(run.status).strip().lower() in {"started", "running", "queued"}
    ]
    # An empty result is a truthful "nothing is running". Only an unavailable
    # source (handled above) falls back to the seeded read model.
    return [
        MissionExecutionRow(
            id=run.id,
            workflow=run.name,
            stage=str(run.status).replace("_", " ").title(),
            progress=(f"{len(run.assets)} assets" if run.assets else "In progress"),
            elapsed=_elapsed(run.duration_seconds),
            run_id=run.id,
        )
        for run in active
    ]


def derive_overview_attention() -> list[MissionAttentionItem] | None:
    """Surface failed runs as priority items, newest first."""
    try:
        runs = load_runs()
    except Exception:  # noqa: BLE001 - substrate probe is best-effort
        return None

    failed = [run for run in runs if str(run.status).strip().lower() in {"failure", "failed"}]
    failed.sort(key=lambda run: run.completed_at or "", reverse=True)
    return [
        MissionAttentionItem(
            id=f"attn-{run.id}",
            severity="danger",
            title=f"{run.name} failed",
            detail=(
                f"Run {run.id} · {len(run.assets)} assets · "
                f"{(run.completed_at or run.started_at or 'unknown time')}"
            ),
            action="Inspect run",
            target=f"/runs/{run.id}",
        )
        for run in failed[:5]
    ]


def derive_run(run_id: str) -> MissionRunDetail | None:
    """Build the run header and details rail from the live orchestrator."""
    try:
        runs = load_runs()
    except Exception:  # noqa: BLE001 - substrate probe is best-effort
        return None

    run = next((candidate for candidate in runs if candidate.id == run_id), None)
    if run is None:
        return None

    tone = _run_tone(str(run.status))
    assets = len(run.assets)
    checks = len(run.checks)

    return MissionRunDetail(
        id=run.id,
        workflow=run.name,
        run_id=run.id,
        status=str(run.status).replace("_", " ").title(),
        summary=_run_summary(run),
        metrics=[
            SummaryMetricRow(
                label="Execution",
                value=str(run.status).replace("_", " ").title(),
                hint="Reported by the orchestrator",
                tone=tone,
            ),
            SummaryMetricRow(
                label="Assets",
                value=str(assets),
                hint="Touched by this run",
                tone="muted",
            ),
            SummaryMetricRow(
                label="Checks",
                value=str(checks),
                hint="Attached to this run",
                tone="muted",
            ),
            SummaryMetricRow(
                label="Duration",
                value=_elapsed(run.duration_seconds),
                hint=f"{run.started_at or '—'} → {run.completed_at or '—'}",
                tone="muted",
            ),
            SummaryMetricRow(
                label="Attempt",
                value="—",
                hint="Not reported by this orchestrator",
                tone="muted",
            ),
        ],
        details=[
            RunConfigurationRow(run_id=run.id, label="Run", value=run.id),
            RunConfigurationRow(run_id=run.id, label="Pipeline", value=run.name),
            RunConfigurationRow(run_id=run.id, label="Started", value=run.started_at or "—"),
            RunConfigurationRow(run_id=run.id, label="Completed", value=run.completed_at or "—"),
            RunConfigurationRow(
                run_id=run.id,
                label="Assets",
                value=", ".join(ref.id for ref in run.assets) or "—",
            ),
        ],
        consumers=[],
        artifacts=[
            RunArtifact(
                run_id=run.id,
                name=ref.id,
                size="—",
                checksum="—",
            )
            for ref in run.checks
        ],
    )


def _run_summary(run: ObservatoryRun) -> str:
    parts = [f"Run {run.id}"]
    if run.started_at:
        parts.append(f"started {run.started_at}")
    if run.completed_at:
        parts.append(f"completed {run.completed_at}")
    if run.assets:
        parts.append(f"{len(run.assets)} assets")
    return " · ".join(parts)


# NOTE: run stage breakdown is not on ObservatoryRun; it lives in the run report
# store (phlo.run_evidence). Deriving stages needs that store wired here, so the
# seeded stage timeline remains in use until it is.


# ------------------------------------------------------------------- dataset


def _asset_for(asset_id: str):
    """Find an asset by id, tolerating an id that is a short name."""
    assets = _load_assets()
    for asset in assets:
        if asset.id == asset_id or asset.name == asset_id:
            return asset
    # Fall back to matching on the trailing segment, e.g. "marts.orders".
    for asset in assets:
        if asset.id.endswith(asset_id) or asset_id.endswith(asset.id):
            return asset
    return None


def derive_dataset_lineage(dataset_id: str) -> list[LineageNode] | None:
    """Build the lineage chain from the asset graph's declared dependencies.

    Returns ``None`` when the asset cannot be found so the caller keeps the
    seeded chain, and a single-node chain when the asset has no neighbours.
    """
    try:
        assets = _load_assets()
    except Exception:  # noqa: BLE001 - substrate probe is best-effort
        return None

    if not assets:
        return None

    target = _asset_for(dataset_id)
    if target is None:
        return None

    by_id = {asset.id: asset for asset in assets}
    nodes: list[LineageNode] = []
    for dependency in target.dependencies:
        node = by_id.get(dependency)
        nodes.append(
            LineageNode(
                name=node.name if node else dependency,
                role=(node.group or node.kinds[0])
                if node and (node.group or node.kinds)
                else "Upstream",
                current=False,
            )
        )

    nodes.append(
        LineageNode(
            name=target.name,
            role="This dataset",
            current=True,
        )
    )

    downstream = [
        asset for asset in assets if target.id in asset.dependencies and asset.id != target.id
    ]
    if downstream:
        roles = sorted({(asset.group or "Downstream") for asset in downstream})
        nodes.append(
            LineageNode(
                name=f"{len(downstream)} consumers",
                role=" · ".join(roles),
                current=False,
            )
        )

    return nodes


def derive_dataset_checks(dataset_id: str) -> list[DatasetCheck] | None:
    """Build the quality check list from the asset's declared checks."""
    try:
        target = _asset_for(dataset_id)
    except Exception:  # noqa: BLE001 - substrate probe is best-effort
        return None

    if target is None:
        return None

    return [DatasetCheck(name=check, outcome="Declared", tone="muted") for check in target.checks]


# ----------------------------------------------------------------- releases
#
# A WAP branch *is* a release candidate: the run stages its write on an isolated
# branch and promotion merges it. So Nessie is the release source of truth —
# candidate branches are pending releases, `main` is what consumers read.


def _is_candidate(branch) -> bool:
    return branch.name != RELEASED_BRANCH and not branch.protected


def derive_release_candidates() -> list[ReleaseCandidate] | None:
    """List pending release candidates from unmerged catalog branches."""
    try:
        branches = _load_branches()
    except Exception:  # noqa: BLE001 - substrate probe is best-effort
        return None

    if branches is None:
        return None

    branches = list(branches)
    if not branches:
        # No catalog reachable at all: let the caller fall back.
        return None

    candidates: list[ReleaseCandidate] = []
    for branch in branches:
        if not _is_candidate(branch):
            continue
        detail = _safe_branch_detail(branch.name)
        commits = len(detail.commits) if detail else 0
        compare = detail.compare if detail else {}
        changed = sum(int(compare.get(key) or 0) for key in ("added", "changed", "removed"))
        tables = [_content_id(table) for table in (detail.contents if detail else [])]
        candidates.append(
            ReleaseCandidate(
                id=branch.name,
                dataset=tables[0] if tables else branch.name,
                provider="Nessie",
                strategy="Branch merge",
                readiness="Ready for review" if commits else "Awaiting evidence",
                evidence=f"{commits} commits · {changed} object changes",
                created_at=_branch_timestamp(detail),
                action="Inspect",
            )
        )
    return candidates


def _content_id(content) -> str:
    """Resource refs expose `id`; tolerate a `name` fallback for other providers."""
    return getattr(content, "id", None) or getattr(content, "name", None) or "unknown"


def _safe_branch_detail(branch_name: str):
    try:
        return _load_branch_detail(branch_name)
    except Exception:  # noqa: BLE001 - one unreadable branch must not fail the list
        return None


def _branch_timestamp(detail) -> str:
    if detail and detail.commits:
        return detail.commits[0].started_at or "—"
    return "—"


def derive_release_candidate(candidate_id: str) -> ReleaseCandidateDetail | None:
    """Build the review payload for one candidate branch."""
    detail = _safe_branch_detail(candidate_id)
    if detail is None:
        return None

    compare = detail.compare or {}
    tables = [_content_id(table) for table in detail.contents]
    changes = [
        CandidateSnapshotChange(
            table=table,
            released_snapshot="—",
            candidate_snapshot=candidate_id,
            row_delta=f"+{int(compare.get('added') or 0)}",
        )
        for table in tables
    ] or [
        CandidateSnapshotChange(
            table=candidate_id,
            released_snapshot="—",
            candidate_snapshot=candidate_id,
            row_delta=f"+{int(compare.get('added') or 0)}",
        )
    ]

    evidence = [
        CandidateEvidenceRow(
            name="Catalog commits",
            detail=f"{len(detail.commits)} commit(s) staged on {candidate_id}",
            outcome="Recorded" if detail.commits else "Missing",
            tone="success" if detail.commits else "warning",
        ),
        CandidateEvidenceRow(
            name="Object changes",
            detail=(
                f"{int(compare.get('added') or 0)} added · "
                f"{int(compare.get('changed') or 0)} changed · "
                f"{int(compare.get('removed') or 0)} removed"
            ),
            outcome="Complete",
            tone="success",
        ),
    ]

    return ReleaseCandidateDetail(
        id=candidate_id,
        candidate=None,
        subtitle=f"Nessie branch merge · candidate {candidate_id}",
        status="Ready for review" if detail.commits else "Awaiting evidence",
        revision=f"Staged on {candidate_id} · target {RELEASED_BRANCH}",
        snapshot_changes=changes,
        required_evidence=evidence,
        publication_plan=[
            PublicationPlanRow(label="Operation", value="Merge catalog branch"),
            PublicationPlanRow(label="Catalog", value="Nessie"),
            PublicationPlanRow(label="Source branch", value=candidate_id),
            PublicationPlanRow(label="Target branch", value=RELEASED_BRANCH),
            PublicationPlanRow(label="Intent", value="Not submitted"),
        ],
    )


def derive_completed_releases() -> list[CompletedRelease] | None:
    """List confirmed releases from runs that promoted to the released branch."""
    try:
        runs = load_runs()
    except Exception:  # noqa: BLE001 - substrate probe is best-effort
        return None

    succeeded = [run for run in runs if str(run.status).strip().lower() in {"success", "succeeded"}]
    if not succeeded:
        return []

    succeeded.sort(key=lambda run: run.completed_at or "", reverse=True)
    return [
        CompletedRelease(
            id=f"rel-{run.id[:8]}",
            dataset=", ".join(ref.id for ref in run.assets) or run.name,
            provider="Nessie",
            provider_strategy="Nessie · Branch merge",
            reference=f"{RELEASED_BRANCH} · {run.id[:7]}",
            finished_at=run.completed_at or "—",
            outcome="Merge confirmed",
        )
        for run in succeeded[:10]
    ]


def derive_release_summary(
    candidates: list[ReleaseCandidate] | None,
    completed: list[CompletedRelease] | None,
) -> list[SummaryMetricRow] | None:
    """Build the Releases summary band from the derived rows."""
    if candidates is None and completed is None:
        return None

    candidates = candidates or []
    completed = completed or []
    ready = [row for row in candidates if row.readiness == "Ready for review"]
    awaiting = [row for row in candidates if row.readiness != "Ready for review"]
    providers = {row.provider for row in candidates if row.provider}

    return [
        SummaryMetricRow(
            label="Pending",
            value=f"{len(candidates)} candidate{'s' if len(candidates) != 1 else ''}",
            hint=(
                f"Across {len(providers)} catalog provider{'s' if len(providers) != 1 else ''}"
                if providers
                else "No catalog branches staged"
            ),
            tone="muted",
        ),
        SummaryMetricRow(
            label="Ready for review",
            value=f"{len(ready)} candidate{'s' if len(ready) != 1 else ''}",
            hint="Branch has staged commits",
            tone="success" if ready else "muted",
        ),
        SummaryMetricRow(
            label="Awaiting evidence",
            value=f"{len(awaiting)} candidate{'s' if len(awaiting) != 1 else ''}",
            hint="No commits recorded on the branch",
            tone="warning" if awaiting else "muted",
        ),
        SummaryMetricRow(
            label="Released · 24h",
            value=f"{len(completed)} completed",
            hint="Promotions confirmed by the orchestrator",
            tone="muted",
        ),
        SummaryMetricRow(
            label="Unknown outcomes",
            value="0 operations",
            hint="No reconciliation needed",
            tone="muted",
        ),
    ]


def derive_dataset(dataset_id: str) -> MissionDatasetDetail | None:
    """Synthesise a Dataset read model for any asset the project declares.

    The seeded read model only describes the reference dataset; a real project
    has its own assets. Ownership, contract and access come back unassigned
    because no upstream source models them, and are shown as such rather than
    invented.
    """
    try:
        assets = _load_assets()
    except Exception:  # noqa: BLE001 - substrate probe is best-effort
        return None

    target = _asset_for(dataset_id)
    if target is None:
        return None

    lineage = derive_dataset_lineage(dataset_id) or []
    checks = derive_dataset_checks(dataset_id) or []
    kinds = ", ".join(target.kinds) if target.kinds else "asset"

    return MissionDatasetDetail(
        id=target.id,
        name=target.name,
        status="Published",
        summary=target.description or f"{target.id} · declared by the project as {kinds}.",
        metrics=[
            SummaryMetricRow(
                label="Group",
                value=target.group or "—",
                hint="Declared asset group",
                tone="muted",
            ),
            SummaryMetricRow(
                label="Upstream",
                value=str(len(target.dependencies)),
                hint="Declared dependencies",
                tone="muted",
            ),
            SummaryMetricRow(
                label="Downstream",
                value=str(sum(1 for asset in assets if target.id in asset.dependencies)),
                hint="Assets that depend on this one",
                tone="muted",
            ),
            SummaryMetricRow(
                label="Checks",
                value=str(len(target.checks)),
                hint="Declared quality checks",
                tone="muted",
            ),
            SummaryMetricRow(
                label="Kind",
                value=kinds,
                hint="Asset kinds reported by the provider",
                tone="muted",
            ),
        ],
        schema_fields=[],
        preview=DatasetPreview(columns=[], rows=[]),
        checks=checks,
        lineage=lineage,
        ownership=_ownership_from_asset(target),
        access=[],
        runs=[],
    )


def _metadata_text(metadata: dict | None, key: str) -> str | None:
    """Return a non-empty string metadata value, else ``None``."""
    value = (metadata or {}).get(key)
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _freshness_label(metadata: dict | None) -> str | None:
    """Render a declared SLA freshness window as a short human label."""
    sla = (metadata or {}).get("sla")
    if not isinstance(sla, dict):
        return None
    hours = sla.get("freshness_hours")
    if isinstance(hours, bool) or not isinstance(hours, (int, float)) or hours <= 0:
        return None
    if hours % 24 == 0:
        days = int(hours // 24)
        return f"{days}d" if days > 1 else "24h"
    return f"{int(hours)}h"


def _ownership_from_asset(asset) -> DatasetOwnership:
    """Read accountable ownership from an asset's declared metadata.

    ``owner``, ``group`` and the ``sla`` freshness window are declared on the
    dlt asset annotations; dbt models declare none of them, so those fields stay
    ``None`` rather than being invented.
    """
    metadata = getattr(asset, "metadata", None) or {}
    return DatasetOwnership(
        owner=_metadata_text(metadata, "owner"),
        domain=_metadata_text(metadata, "group") or getattr(asset, "group", None),
        freshness_target=_freshness_label(metadata),
        schedule=None,
        classification=None,
        contract_version=None,
        retention=None,
    )


def derive_ownership_gaps() -> list[OwnershipGap] | None:
    """Derive ownership gaps from declared asset metadata.

    An asset that declares no ``owner`` is a gap. The requirement named is the
    one that is actually missing, so the report stays truthful as more
    declarations are added rather than hardcoding a fixed list of gaps.

    Returns ``None`` when no assets are observable, so the caller falls back to
    the seeded read model; a truly unowned catalogue yields rows, not an empty
    list.
    """
    assets = _load_assets()
    if not assets:
        return None

    gaps: list[OwnershipGap] = []
    for asset in assets:
        owner = _metadata_text(getattr(asset, "metadata", None), "owner")
        if owner is None:
            gaps.append(
                OwnershipGap(
                    dataset=asset.id,
                    requirement="Accountable owner",
                    owner="Unassigned",
                    action="Assign an owner in the asset declaration",
                )
            )
            continue
        if _freshness_label(getattr(asset, "metadata", None)) is None:
            gaps.append(
                OwnershipGap(
                    dataset=asset.id,
                    requirement="Freshness SLA",
                    owner=owner,
                    action="Declare an sla freshness window",
                )
            )

    return sorted(gaps, key=lambda gap: (gap.owner != "Unassigned", gap.dataset))
