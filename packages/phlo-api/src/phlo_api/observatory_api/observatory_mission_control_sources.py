"""Derive Mission Control read models from live platform substrate.

Each derivation returns a typed ``SourceOutcome`` — ``ok`` with the source's
actual answer (including an empty collection), ``absent`` for an unknown
resource, ``unavailable`` for a failed dependency, ``unsupported`` when no
provider or producer exists. Routers turn these into live evidence, 404, 503
or an unsupported panel; nothing here falls back to seed data.

Currently derived:
  - Platform services and summary (from ``/services``)
  - Overview metrics, execution and attention (from runs/operations/assets)
  - Run detail, dataset detail/lineage/checks (from runs and the asset graph)
  - Release candidates/detail/completed (from the catalog's WAP branches)
  - Ownership gaps (from declared asset metadata)

Still seeded, because nothing upstream models them yet:
  - Alerts, environments, data products, rail queue/governance/recovery
  - Per-run stages/quality/events/spans/artifacts/consumers/configuration/logs
  - Dataset governance record, publication plans/reviews, access drift, audit
  - Backup coverage and maintenance reporting
  - Provider connections/impact, notifications, members, defaults
"""

from __future__ import annotations

import asyncio
from collections import Counter
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from typing import Any, Literal, TypeVar

from fastapi import HTTPException

from phlo.logging import get_logger
from phlo.run_evidence.redaction import redact_payload
from phlo_api.run_evidence import RunEvidenceStore
from phlo_api.observatory_api.observatory import (
    _cached_read_model_outcome,
    _catalog_branch_provider,
    _load_assets,
    _load_branch_detail,
    _load_dataset_profile,
    _load_operations,
    _load_services,
    _load_table_preview,
    _query_relation_for_table,
    _table_columns_from_metadata,
    _table_column_types_from_metadata,
)
from phlo_api.observatory_api.observatory_mission_control_mode import (
    SourceOutcome,
    absent,
    ok,
    project_id,
    unavailable,
)
from phlo_api.observatory_api.observatory_mission_control_models import (
    MissionAttentionItem,
    MissionHealthService,
    MissionServiceOption,
    MissionOverviewRail,
    MissionQueueItem,
    MissionRailStat,
    MissionServiceVisibility,
    Outcome,
    MissionExecutionRow,
    CandidateEvidenceRow,
    CandidateSnapshotChange,
    CompletedRelease,
    DatasetCheck,
    DatasetOwnership,
    DatasetRunRef,
    MissionDataProduct,
    DatasetSchemaField,
    LineageNode,
    MissionDatasetDetail,
    DatasetPreview,
    PublicationPlanRow,
    ReleaseCandidate,
    ReleaseCandidateDetail,
    MissionRunDetail,
    MissionRunList,
    MissionRunLogLine,
    MissionRunRow,
    OwnershipGap,
    RunArtifact,
    RunCheckOutcome,
    RunConfigurationRow,
    RunConsumer,
    RunEvent,
    RunEventPage,
    RunIdentity,
    RunLogPage,
    RunQualityFailure,
    RunQualityReport,
    RunSpan,
    RunStageView,
    PlatformService,
    PlatformSummaryMetric,
    PromotionPreview,
    PromotionPreviewCheck,
    SummaryMetricRow,
)
from phlo_api.observatory_api.observatory_models import (
    ObservatoryDataset,
    ObservatoryDatasetProfile,
    ObservatoryRun,
    ObservatoryTable,
)
from phlo_api.observatory_api.observatory_runs import (
    _canonical_timestamp,
    load_runs_strict,
)
from phlo_api.observatory_api.observatory_wap import (
    WAP_BLOCKED_STATUSES,
    WAP_PENDING_STATUSES,
    WAP_RELEASED_STATUSES,
    WAP_TARGET_BRANCH,
    Readiness,
    WapReport,
    candidate_readiness,
    evaluate_promotion_preview,
    load_wap_reports,
    verify_launch_binding,
)
from phlo_api.observatory_api.observatory_services import (
    docker_reachable,
    project_compose_name,
)
from phlo_api.observatory_api.observatory_durable_state import read_collection
from phlo_api.observatory_api.observatory_mission_control_state import project_root

logger = get_logger(__name__)

T = TypeVar("T")

RELEASED_BRANCH = "main"

# Mission reads stay short-TTL: the cache exists for single-flight coalescing
# and stale-on-failure fallback, not for serving old data while healthy.
_MISSION_SOURCE_TTL_SECONDS = 15

# Bounded previews and per-dataset run tails — detail payloads stay small.
_PREVIEW_LIMIT = 25
_DATASET_RUNS_LIMIT = 10

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


def _data_or_none(outcome: SourceOutcome[list]) -> list | None:
    """Data for composite builders that still tolerate a missing section."""
    return outcome.data if outcome.status == "ok" else None


def _source_read(name: str, loader: Callable[[], T]) -> tuple[T, dict[str, Any]]:
    """Read a live source through the read-model cache so a provider failure
    can fall back to the last confirmed value — flagged stale, never silent."""
    read = _cached_read_model_outcome(name, _MISSION_SOURCE_TTL_SECONDS, loader)
    confirmed = (
        datetime.fromtimestamp(read.confirmed_at, UTC).isoformat()
        if read.stale and read.confirmed_at is not None
        else None
    )
    return read.value, {"stale": read.stale, "last_confirmed_at": confirmed}


def _safe_source_read(name: str, loader: Callable[[], T]) -> tuple[T | None, dict[str, Any]]:
    """Per-item variant of ``_source_read``: one unreadable item degrades its
    own row instead of failing the whole collection."""
    try:
        return _source_read(name, loader)
    except Exception:  # noqa: BLE001 - item-level failure degrades to a row marker
        logger.warning("mission_source_item_unavailable", source=name, exc_info=True)
        return None, {}


def _merge_freshness(*metas: dict[str, Any]) -> dict[str, Any]:
    """Combine per-source freshness: a composite is stale if any input is."""
    stamps = [
        meta["last_confirmed_at"]
        for meta in metas
        if meta.get("stale") and meta.get("last_confirmed_at")
    ]
    return {
        "stale": any(meta.get("stale") for meta in metas),
        "last_confirmed_at": min(stamps) if stamps else None,
    }


def _guard(source: str, fn: Callable[[], SourceOutcome]) -> SourceOutcome:
    """Convert an unexpected substrate failure into a typed unavailable outcome."""
    try:
        return fn()
    except Exception:  # noqa: BLE001 - the failure itself is the answer
        logger.warning("mission_source_unavailable", source=source, exc_info=True)
        return unavailable("A required dependency is unavailable")


def derive_platform_services() -> SourceOutcome[list[PlatformService]]:
    """Build the Platform service table from live service status.

    ``ok([])`` means the project genuinely has no observed services;
    ``unavailable`` means the container runtime could not be reached.
    """

    def _derive() -> SourceOutcome[list[PlatformService]]:
        services, freshness = _source_read("platform_services_raw", _load_services)

        # Only services whose runtime state actually resolved. The catalog lists
        # every service the platform knows about, most of which are not part of
        # this project; reporting unknown-state rows is noise, not signal.
        observed = [
            service
            for service in services
            if str(getattr(service, "status", "")).strip().lower() not in {"", "unknown"}
        ]
        if not observed:
            if not project_compose_name(project_root()):
                # The project is not service-managed: an empty list is truthful.
                return ok([], **freshness)
            if not docker_reachable() and not freshness["stale"]:
                return unavailable("The container runtime is unreachable")

        rows: list[PlatformService] = []
        for service in observed:
            # `status` is the process state and `health.state` the readiness probe;
            # they are reported separately on purpose and never merged.
            runtime_state = str(service.status).replace("_", " ").title()
            health_state = str(getattr(service.health, "state", "unknown"))
            ready = _is_ready(health_state)
            probe = getattr(service.health, "message", None) or health_state
            restart_policy = str(
                getattr(service, "metadata", {}).get("restart_policy") or ""
            ).lower()
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
                    lifecycle="task" if restart_policy in {"no", "never"} else "service",
                )
            )
        return ok(rows, source="service_catalog", **freshness)

    return _guard("platform_services", _derive)


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


def _run_tone(status: str) -> Outcome:
    state = status.strip().lower()
    if state in {"success", "succeeded"}:
        return "success"
    if state in {"failure", "failed"}:
        return "danger"
    if state in {"started", "running", "queued"}:
        return "accent"
    return "muted"


def _display_ts(value: Any) -> str | None:
    """Operator-facing timestamp — ``Sep 19 · 09:52``. Non-ISO input passes
    through untouched, matching the web client's ``formatTimestamp``."""
    text = _text(value)
    if text is None:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return text
    return parsed.strftime("%b %-d · %H:%M")


def _text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _elapsed_between(start: Any, end: Any) -> str:
    start_dt = _parse_dt(start)
    end_dt = _parse_dt(end)
    if start_dt is None or end_dt is None:
        return "—"
    return _elapsed(max(0.0, (end_dt - start_dt).total_seconds()))


def derive_overview_metrics(store: RunEvidenceStore) -> SourceOutcome[list[SummaryMetricRow]]:
    """Build the Overview summary band from live execution state.

    Governance counters have no upstream source and are reported as
    unavailable rather than invented.
    """

    def _derive() -> SourceOutcome[list[SummaryMetricRow]]:
        runs, runs_meta = _source_read("mission_runs", load_runs_strict)
        operations = _load_operations()
        assets, assets_meta = _source_read("mission_assets", _load_assets)
        quality = _quality_totals(store)
        # Nested outcomes carry their own freshness; fold it into this
        # envelope so a stale release read marks the whole band stale.
        candidates = derive_release_candidates()
        completed = derive_completed_releases()
        freshness = _merge_freshness(
            runs_meta,
            assets_meta,
            *(
                {"stale": o.stale, "last_confirmed_at": o.last_confirmed_at}
                for o in (candidates, completed)
            ),
        )
        return ok(
            _overview_metrics(
                runs,
                operations,
                assets,
                _data_or_none(candidates),
                _data_or_none(completed),
                quality,
            ),
            source="orchestrator+run_evidence",
            **freshness,
        )

    return _guard("overview_summary", _derive)


def _quality_totals(store: RunEvidenceStore) -> tuple[int, int] | None:
    """Total and passed executed check results across all recorded runs.

    Results are keyed by the project identity stamped at run time, which may
    differ from the configured project name — so each run row supplies its own
    project id rather than assuming ``project_id()``.
    """
    try:
        total = 0
        passed = 0
        for run_row in store.list_runs():
            run_id = str(run_row.get("run_id") or "")
            evidence_project = str(run_row.get("project_id") or "")
            if not run_id or not evidence_project:
                continue
            for check in store.list_quality_results(evidence_project, run_id):
                total += 1
                if check.get("passed") is True:
                    passed += 1
        return (total, passed)
    except Exception:
        logger.warning("quality_totals_read_failed", exc_info=True)
        return None


def _overview_metrics(
    runs: list[ObservatoryRun],
    operations: list,
    assets: list,
    candidates: list[ReleaseCandidate] | None,
    completed: list[CompletedRelease] | None,
    quality: tuple[int, int] | None,
) -> list[SummaryMetricRow]:

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

    release_summary = derive_release_summary(candidates, completed)
    releases_value, releases_hint, releases_tone = "—", "No release source configured", "muted"
    if release_summary:
        pending = next((row for row in release_summary if row.label == "Pending"), None)
        blocked = next((row for row in release_summary if row.label == "Blocked"), None)
        last = next((row for row in release_summary if row.label == "Last release"), None)
        if pending and last:
            blocked_count = int(blocked.value.split()[0]) if blocked else 0
            releases_value = pending.value
            releases_hint = " · ".join(
                part
                for part in (
                    f"{blocked_count} blocked" if blocked_count else None,
                    f"last {last.value}" if last.value != "—" else None,
                    "none pending" if pending.value.startswith("0") else None,
                )
                if part
            )
            releases_tone = "danger" if blocked_count else "muted"

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
            value=(f"{quality[0]} checks" if quality and quality[0] else "—"),
            hint=(
                f"{quality[1]} passed · Recorded in run evidence"
                if quality and quality[0]
                else (
                    "Quality evidence unavailable"
                    if quality is None
                    else "No checks recorded in run evidence"
                )
            ),
            tone="danger" if quality and quality[0] > quality[1] else "muted",
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
    ]


def derive_overview_execution(limit: int = 10) -> SourceOutcome[list[MissionExecutionRow]]:
    """Build the execution table: in-flight runs first, then the most recent
    terminal runs so the panel still shows real work when nothing is running.

    An empty result is a truthful "no runs recorded"; only an unavailable
    orchestrator produces ``unavailable``.
    """

    def _derive() -> SourceOutcome[list[MissionExecutionRow]]:
        runs, freshness = _source_read("mission_runs", load_runs_strict)
        active_statuses = {"started", "running", "queued"}

        def _status(run: ObservatoryRun) -> str:
            return str(run.status).strip().lower()

        def _activity(run: ObservatoryRun) -> str:
            return run.completed_at or run.started_at or ""

        active = sorted(
            (run for run in runs if _status(run) in active_statuses),
            key=lambda run: run.started_at or "",
            reverse=True,
        )
        recent = sorted(
            (run for run in runs if _status(run) not in active_statuses),
            key=_activity,
            reverse=True,
        )
        bounded = (active + recent)[: max(1, limit)]

        def _progress(run: ObservatoryRun) -> str:
            if run.assets:
                return f"{len(run.assets)} assets"
            return "In progress" if _status(run) in active_statuses else "—"

        return ok(
            [
                MissionExecutionRow(
                    id=run.id,
                    workflow=run.name,
                    stage=str(run.status).replace("_", " ").title(),
                    progress=_progress(run),
                    elapsed=_elapsed(run.duration_seconds),
                    run_id=run.id,
                )
                for run in bounded
            ],
            source="orchestrator",
            **freshness,
        )

    return _guard("overview_execution", _derive)


def derive_overview_attention() -> SourceOutcome[list[MissionAttentionItem]]:
    """Surface failed runs as priority items, newest first."""

    def _derive() -> SourceOutcome[list[MissionAttentionItem]]:
        runs, freshness = _source_read("mission_runs", load_runs_strict)
        failed = [run for run in runs if str(run.status).strip().lower() in {"failure", "failed"}]
        failed.sort(key=lambda run: run.completed_at or "", reverse=True)
        return ok(
            [
                MissionAttentionItem(
                    id=f"attn-{run.id}",
                    severity="danger",
                    title=f"{run.name} failed",
                    detail=(
                        f"Run {run.id} · {len(run.assets)} asset{'s' if len(run.assets) != 1 else ''} · "
                        f"{_display_ts(run.completed_at or run.started_at) or 'unknown time'}"
                    ),
                    action="Inspect run",
                    target=f"/runs/{run.id}",
                )
                for run in failed[:5]
            ],
            source="orchestrator",
            **freshness,
        )

    return _guard("overview_attention", _derive)


SERVICE_VISIBILITY_COLLECTION = "service_visibility"
_SERVICE_VISIBILITY_RECORD = "service_health"


def read_service_visibility() -> MissionServiceVisibility:
    """Return the operator-curated rail service list, or unconfigured.

    The durable record names the catalog services the rail may show; an absent
    collection means nothing was configured and every service renders. A
    corrupt or unreachable store raises so the caller can fail closed rather
    than silently showing everything.
    """
    records = read_collection(project_root(), SERVICE_VISIBILITY_COLLECTION)
    if records is None:
        return MissionServiceVisibility(shown=None, configured=False)
    for record in records:
        if record.get("id") != _SERVICE_VISIBILITY_RECORD:
            continue
        shown = record.get("shown")
        if shown is None:
            return MissionServiceVisibility(shown=None, configured=True)
        if isinstance(shown, list):
            return MissionServiceVisibility(shown=[str(name) for name in shown], configured=True)
    return MissionServiceVisibility(shown=None, configured=False)


def derive_overview_rail() -> SourceOutcome[MissionOverviewRail]:
    """Build the Overview rail from live sources.

    Services come from the live service catalog filtered by the recorded
    visibility configuration, and the release queue from governed WAP
    reports. Governance and recovery have no live producer in this
    deployment, so they are reported as unrecorded — never seeded.
    """

    def _derive() -> SourceOutcome[MissionOverviewRail]:
        services_outcome = derive_platform_services()
        candidates_outcome = derive_release_candidates()
        unavailable_parts = [
            outcome.detail or "source unavailable"
            for outcome in (services_outcome, candidates_outcome)
            if outcome.status == "unavailable"
        ]
        if unavailable_parts:
            return unavailable("; ".join(unavailable_parts))
        services = services_outcome.data or []
        candidates = candidates_outcome.data or []
        visibility = read_service_visibility()
        if visibility.shown is not None:
            keep = set(visibility.shown)
            shown_services = [row for row in services if row.name in keep]
        else:
            # Unconfigured: long-running services only — one-shot jobs (setup
            # containers declared restart: "no") are not health signal.
            shown_services = [row for row in services if row.lifecycle != "task"]
        rail = MissionOverviewRail(
            id="rail",
            ready_count=sum(1 for row in shown_services if row.readiness_state == "Ready"),
            total_services=len(shown_services),
            services=[
                MissionHealthService(name=row.name, role=row.role, state=row.readiness_state)
                for row in shown_services
            ],
            service_options=[
                MissionServiceOption(
                    name=row.name,
                    role=row.role,
                    state=row.readiness_state,
                    lifecycle=row.lifecycle,
                )
                for row in services
            ],
            service_visibility=visibility,
            release_queue=[
                MissionQueueItem(name=row.dataset, state=row.readiness, detail=row.evidence)
                for row in candidates
            ],
            governance=[
                MissionRailStat(
                    label="Governance data",
                    value="Not recorded for this project",
                )
            ],
            recovery=[
                MissionRailStat(
                    label="Recovery data",
                    value="Not recorded for this project",
                )
            ],
        )
        sources = "+".join(
            outcome.source for outcome in (services_outcome, candidates_outcome) if outcome.source
        )
        return ok(rail, source=sources or "live")

    return _guard("overview_rail", _derive)


def derive_data_products(store: RunEvidenceStore) -> SourceOutcome[list[MissionDataProduct]]:
    """Released datasets for the Overview.

    One row per table recorded as a governed release output: release time from
    the WAP receipt, partition freshness and check counts from run evidence,
    consumers from the declared asset graph. A table with no evidence trail is
    never invented.
    """

    def _derive() -> SourceOutcome[list[MissionDataProduct]]:
        reports = [
            report
            for report in load_wap_reports(project_root())
            if report.status in WAP_RELEASED_STATUSES
        ]
        assets, freshness = _source_read("mission_assets", _load_assets)
        released: dict[str, dict[str, Any]] = {}
        for report in reports:
            # Evidence is keyed by the identity stamped at launch; the report's
            # own tag is authoritative when the deployment's runtime id differs
            # from the configured project name.
            evidence_project = report.launch_tags.get("phlo/project_id") or project_id()
            for resource in store.list_resources(evidence_project, report.logical_run_id):
                if resource.get("resource_kind") != "iceberg_table":
                    continue
                if resource.get("role") != "output":
                    continue
                table = str(resource.get("table_name") or "").strip()
                if not table:
                    continue
                entry = released.setdefault(
                    table,
                    {"run_id": report.logical_run_id, "released": "", "project": evidence_project},
                )
                if (report.updated_at or "") > entry["released"]:
                    entry["released"] = report.updated_at or ""
                    entry["run_id"] = report.logical_run_id
                    entry["project"] = evidence_project
        rows: list[MissionDataProduct] = []
        for table, info in sorted(released.items(), key=lambda kv: kv[1]["released"], reverse=True):
            short = table.split(".")[-1]
            asset = _asset_for(short, assets)
            consumers = sum(
                1
                for item in assets
                if asset is not None and asset.id in getattr(item, "dependencies", ())
            )
            checks = store.list_quality_results(info["project"], info["run_id"])
            passed = sum(1 for check in checks if check.get("passed") is True)
            run_row = store.get_run(info["project"], info["run_id"])
            partition = str((run_row or {}).get("partition_key") or "").strip()
            rows.append(
                MissionDataProduct(
                    id=table,
                    name=short,
                    freshness=partition or "—",
                    quality=f"{passed}/{len(checks)} checks" if checks else "—",
                    released=_display_ts(info["released"]) or "—",
                    consumers=consumers,
                    target=f"/datasets/{asset.id}" if asset is not None else f"/datasets/{short}",
                )
            )
        return ok(rows, source="wap_reports+run_evidence", **freshness)

    return _guard("data_products", _derive)


def derive_runs_page(limit: int, cursor: str | None) -> SourceOutcome[MissionRunList]:
    """One bounded page of the orchestrator run population.

    Offset cursors over a freshly-read snapshot; `limit` is capped so a page
    stays bounded. This is the same source `derive_overview_execution` reads,
    so list rows and overview counters can never disagree.
    """

    def _derive() -> SourceOutcome[MissionRunList]:
        runs, freshness = _source_read("mission_runs", load_runs_strict)
        try:
            offset = int(cursor) if cursor else 0
        except ValueError:
            offset = 0
        offset = max(0, offset)
        safe_limit = max(1, min(limit, 500))
        page = runs[offset : offset + safe_limit]
        next_cursor = str(offset + safe_limit) if offset + safe_limit < len(runs) else None
        return ok(
            MissionRunList(
                items=[
                    MissionRunRow(
                        id=run.id,
                        name=run.name,
                        status=str(run.status),
                        started_at=run.started_at,
                        completed_at=run.completed_at,
                        duration_seconds=run.duration_seconds,
                        asset_ids=[ref.id for ref in run.assets],
                    )
                    for run in page
                ],
                next_cursor=next_cursor,
            ),
            source="orchestrator",
            **freshness,
        )

    return _guard("runs_list", _derive)


def derive_run(store: RunEvidenceStore, run_id: str) -> SourceOutcome[MissionRunDetail]:
    """Build the run header and details rail from the live orchestrator,
    joined to the retained evidence record.

    When the orchestrator is unreachable but the run has durable evidence,
    the retained record is served flagged stale — an offline provider never
    erases confirmed evidence.
    """

    def _derive() -> SourceOutcome[MissionRunDetail]:
        runs, freshness = _safe_source_read("mission_runs", load_runs_strict)
        run = next((candidate for candidate in runs or [] if candidate.id == run_id), None)
        row = store.get_run(project_id(), run_id)
        if run is None:
            if row is None:
                if runs is None:
                    return unavailable("orchestrator")
                return absent(f"Unknown run {run_id}")
            detail = _retained_run_detail(run_id, row)
            detail.identity = _run_identity(store, run_id, run, row)
            return ok(
                detail,
                source="run_evidence",
                stale=True,
                last_confirmed_at=_text(row.get("finished_at")) or _text(row.get("started_at")),
            )
        detail = _run_detail(run)
        detail.identity = _run_identity(store, run_id, run, row)
        if str(run.status).lower() == "failed":
            events = _orchestrator_run_events(run_id)
            detail.failure = _orchestrator_failure_summary(events or [])
        return ok(detail, source="orchestrator", **freshness)

    return _guard("run_detail", _derive)


def _orchestrator_failure_summary(events: list[dict[str, Any]]) -> str | None:
    """The first recorded step failure, or any run-level failure message —
    leads the operator to the step and its deepest recorded cause."""
    for event in events:
        if str(event.get("eventType")) == "STEP_FAILURE":
            message = _dagster_event_error_message(event)
            step = event.get("stepKey")
            if message:
                return f"{step}: {message}" if step else message
    for event in events:
        if "FAILURE" in str(event.get("eventType") or ""):
            message = _dagster_event_error_message(event) or str(event.get("message") or "")
            if message.strip():
                return message.strip()
    return None


def _run_identity(
    store: RunEvidenceStore,
    run_id: str,
    run: ObservatoryRun | None,
    row: dict[str, Any] | None,
) -> RunIdentity:
    """Join the logical run id to every other identity the evidence carries —
    provider run id, attempt, report identity, operation id, WAP launch digest
    and the asset ids the run actually touched."""
    report: str | None = None
    if run is not None and run.report_identity is not None:
        report = ":".join(
            str(part)
            for part in (
                run.report_identity.project_id,
                run.report_identity.run_id,
                run.report_identity.attempt,
            )
        )
    launch_digest: str | None = None
    operation_id: str | None = None
    durable_run_id: str | None = None
    asset_ids: list[str] = []
    # The durable WAP report joins on the orchestrator's own run id — present
    # even when the evidence store retained nothing for this run.
    wap_report = next(
        (
            candidate
            for candidate in load_wap_reports(project_root())
            if candidate.dagster_run_id == run_id
        ),
        None,
    )
    if wap_report is not None:
        report = report or wap_report.logical_run_id
        durable_run_id = wap_report.logical_run_id
        launch_digest = wap_report.launch_manifest_checksum
    if row is not None:
        payload = row.get("payload_json")
        if isinstance(payload, dict):
            operation_id = _text(payload.get("operation_id"))
        if operation_id is None:
            operation_id = _text(row.get("effective_identity"))
        changes = store.list_catalog_changes(project_id(), run_id)
        launch_digest = next(
            (
                digest
                for digest in (
                    _text(change.get("source_hash")) or _text(change.get("commit_hash"))
                    for change in changes
                )
                if digest
            ),
            None,
        )
        asset_ids = sorted(
            {
                str(resource.get("normalized_identity") or resource.get("resource_id"))
                for resource in store.list_resources(project_id(), run_id)
                if resource.get("normalized_identity") or resource.get("resource_id")
            }
        )
    if not asset_ids and run is not None:
        asset_ids = sorted(ref.id for ref in run.assets)
    if durable_run_id is None:
        durable_run_id = _text(row.get("run_id")) if row else None
    return RunIdentity(
        run_id=run_id,
        durable_run_id=durable_run_id,
        provider_run_id=(_text(row.get("provider_run_id")) if row else None)
        or (run_id if run is not None else None),
        attempt=int(row["attempt"]) if row and row.get("attempt") is not None else None,
        report=report,
        pipeline=(run.name if run is not None else None)
        or (_text(row.get("pipeline_name")) if row else None),
        operation_id=operation_id,
        launch_digest=launch_digest,
        asset_ids=asset_ids,
    )


def _retained_run_detail(run_id: str, row: dict[str, Any]) -> MissionRunDetail:
    """Header/details built purely from the durable record — used only when
    the orchestrator cannot reconfirm the run."""
    status = str(row.get("status") or "unknown")
    pipeline = _text(row.get("pipeline_name")) or "—"
    return MissionRunDetail(
        id=run_id,
        workflow=pipeline,
        run_id=run_id,
        status=status.replace("_", " ").title(),
        summary=(
            f"Run {run_id} — orchestrator unreachable; showing retained evidence "
            f"recorded for attempt {row.get('attempt') or 1}."
        ),
        metrics=[
            SummaryMetricRow(
                label="Execution",
                value=status.replace("_", " ").title(),
                hint="From retained evidence",
                tone=_run_tone(status),
            ),
            SummaryMetricRow(
                label="Attempt",
                value=str(row.get("attempt") or 1),
                hint="From retained evidence",
                tone="muted",
            ),
            SummaryMetricRow(
                label="Completeness",
                value=str(row.get("evidence_completeness") or "unknown"),
                hint="Recorded evidence coverage",
                tone="muted",
            ),
        ],
        details=[
            RunConfigurationRow(run_id=run_id, label="Run", value=run_id),
            RunConfigurationRow(run_id=run_id, label="Pipeline", value=pipeline),
            RunConfigurationRow(
                run_id=run_id, label="Started", value=_display_ts(row.get("started_at")) or "—"
            ),
            RunConfigurationRow(
                run_id=run_id, label="Completed", value=_display_ts(row.get("finished_at")) or "—"
            ),
        ],
        consumers=[],
        artifacts=[],
    )


def _run_detail(run: ObservatoryRun) -> MissionRunDetail:

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
                hint=f"{_display_ts(run.started_at) or '—'} → {_display_ts(run.completed_at) or '—'}",
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
            RunConfigurationRow(
                run_id=run.id, label="Started", value=_display_ts(run.started_at) or "—"
            ),
            RunConfigurationRow(
                run_id=run.id, label="Completed", value=_display_ts(run.completed_at) or "—"
            ),
            RunConfigurationRow(
                run_id=run.id,
                label="Assets",
                value=", ".join(ref.id for ref in run.assets) or "—",
            ),
        ],
        consumers=[],
        artifacts=[],
    )


def _run_summary(run: ObservatoryRun) -> str:
    parts = [f"Run {run.id}"]
    if run.started_at:
        parts.append(f"started {_display_ts(run.started_at)}")
    if run.completed_at:
        parts.append(f"completed {_display_ts(run.completed_at)}")
    if run.assets:
        parts.append(f"{len(run.assets)} asset{'s' if len(run.assets) != 1 else ''}")
    return " · ".join(parts)


# ------------------------------------------------------------ run evidence
#
# Every derive below reads the durable run-evidence store scoped to
# (project_id, run_id, attempt). Because the store query itself carries the
# run identity, no displayed evidence id can ever belong to a different run,
# and attempts are never merged — `attempt=None` returns the recorded rows
# for every attempt with the row's own attempt attached.


def _stored_run_or_absent(
    store: RunEvidenceStore, run_id: str
) -> dict[str, Any] | SourceOutcome[Any] | None:
    """Return the durable run row, or an absent outcome when the run is
    unknown to both the evidence store and the orchestrator."""
    row = store.get_run(project_id(), run_id)
    if row is not None:
        return row
    runs, _ = _safe_source_read("mission_runs", load_runs_strict)
    if runs is None or not any(candidate.id == run_id for candidate in runs):
        return absent(f"Unknown run {run_id}")
    # Known to the orchestrator but no retained evidence yet — the sections
    # fall back to the orchestrator's own event stream rather than showing
    # empty collections for a run that did produce diagnostics.
    return None


def _orchestrator_run_events(run_id: str) -> list[dict[str, Any]] | None:
    """Fetch the orchestrator's retained event stream for a run.

    ``None`` means the orchestrator could not answer — the caller degrades
    the section instead of implying the run produced no diagnostics.
    """
    from phlo_api.observatory_api.dagster import fetch_run_events

    try:
        events = asyncio.run(fetch_run_events(run_id))
    except Exception:
        logger.warning("run_events_orchestrator_fetch_failed", run_id=run_id, exc_info=True)
        return None
    return events if isinstance(events, list) else None


def _dagster_timestamp(event: dict[str, Any]) -> str | None:
    """Dagster event timestamps are millisecond-epoch strings."""
    raw = event.get("timestamp")
    try:
        return datetime.fromtimestamp(float(str(raw)) / 1000, UTC).isoformat()
    except (TypeError, ValueError, OSError, OverflowError):
        return None


def _dagster_event_error_message(event: dict[str, Any]) -> str | None:
    """The deepest recorded cause — Dagster wraps real errors in retry policy."""
    error = event.get("error")
    if not isinstance(error, dict):
        return None
    causes = error.get("causes")
    if isinstance(causes, list):
        for cause in causes:
            if isinstance(cause, dict):
                message = str(cause.get("message") or "").strip()
                if message:
                    return message
    message = str(error.get("message") or "").strip()
    return message or None


def _orchestrator_event_rows(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Reshape Dagster log events into evidence-store row form so the event
    and log derives render orchestrator diagnostics identically."""
    rows: list[dict[str, Any]] = []
    for event in events:
        event_type = str(event.get("eventType") or event.get("__typename") or "event")
        message = _dagster_event_error_message(event) or str(event.get("message") or "")
        step = event.get("stepKey")
        if step and message:
            message = f"{step} · {message}"
        rows.append(
            {
                "event_type": event_type,
                "payload": {
                    "message": message,
                    "level": str(event.get("level") or "INFO").upper(),
                },
                "observed_at": _dagster_timestamp(event),
                "attempt": None,
            }
        )
    return rows


_STEP_EVENT_STATUSES = {
    "STEP_UP_FOR_RETRY": "retrying",
    "STEP_RESTARTED": "restarted",
    "STEP_SUCCESS": "success",
    "STEP_FAILURE": "failed",
    "STEP_SKIPPED": "skipped",
}


def _orchestrator_stage_rows(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Fold Dagster step events into stage rows matching the evidence shape —
    one row per step, terminal outcome where one was recorded, error note
    from the failure event's deepest cause."""
    steps: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for event in events:
        step = event.get("stepKey")
        event_type = str(event.get("eventType") or "")
        if not step or (event_type != "STEP_START" and event_type not in _STEP_EVENT_STATUSES):
            continue
        key = str(step)
        if key not in steps:
            steps[key] = {
                "stage_id": key,
                "provider": "dagster",
                "status": "unknown",
                "started_at": None,
                "finished_at": None,
                "error": None,
            }
            order.append(key)
        row = steps[key]
        timestamp = _dagster_timestamp(event)
        if event_type == "STEP_START" and row["started_at"] is None:
            row["started_at"] = timestamp
        elif event_type in {"STEP_SUCCESS", "STEP_FAILURE", "STEP_SKIPPED"}:
            row["status"] = _STEP_EVENT_STATUSES[event_type]
            row["finished_at"] = timestamp
            if event_type == "STEP_FAILURE":
                row["error"] = _dagster_event_error_message(event)
        elif row["status"] == "unknown":
            row["status"] = _STEP_EVENT_STATUSES[event_type]
        if timestamp and row["started_at"] is None:
            row["started_at"] = timestamp
    return [steps[key] for key in order]


def _parse_dt(value: Any) -> datetime | None:
    text = _text(value)
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None


def _stage_bars(
    rows: list[dict[str, Any]],
) -> list[tuple[float, float]]:
    """Compute (offset, width) percents across the run's execution window."""
    starts = [_parse_dt(r.get("started_at")) for r in rows]
    ends = [_parse_dt(r.get("finished_at")) for r in rows]
    window_lo = min((s for s in starts if s is not None), default=None)
    window_hi = max((e for e in ends if e is not None), default=None)
    span = (
        (window_hi - window_lo).total_seconds()
        if window_lo is not None and window_hi is not None
        else 0.0
    )
    if window_lo is None or span <= 0:
        width = 100.0 / max(len(rows), 1)
        return [(i * width, width) for i in range(len(rows))]
    bars: list[tuple[float, float]] = []
    for start, end in zip(starts, ends, strict=False):
        if start is None:
            bars.append((0.0, 100.0))
            continue
        offset = max(0.0, (start - window_lo).total_seconds() / span * 100.0)
        width = max(
            2.0,
            ((end - start).total_seconds() if end is not None else 0.0) / span * 100.0,
        )
        bars.append((min(offset, 100.0), min(width, 100.0 - min(offset, 100.0))))
    return bars


def derive_run_stages(store: RunEvidenceStore, run_id: str) -> SourceOutcome[list[RunStageView]]:
    """Stage breakdown from the recorded run stages."""

    def _derive() -> SourceOutcome[list[RunStageView]]:
        resolved = _stored_run_or_absent(store, run_id)
        if isinstance(resolved, SourceOutcome):
            return resolved
        source = "run_evidence"
        rows = store.list_stages(project_id(), run_id)
        if not rows:
            events = _orchestrator_run_events(run_id)
            if events is None:
                return unavailable("orchestrator")
            rows = _orchestrator_stage_rows(events)
            source = "orchestrator"
        bars = _stage_bars(rows)
        stages: list[RunStageView] = []
        for row, (offset, width) in zip(rows, bars, strict=False):
            status = str(row.get("status") or "unknown")
            stages.append(
                RunStageView(
                    run_id=run_id,
                    name=str(row.get("stage_id") or "unknown"),
                    provider=str(row.get("provider") or row.get("tool") or "—"),
                    outcome=status.replace("_", " ").title(),
                    offset_percent=round(offset, 1),
                    width_percent=round(width, 1),
                    duration=_elapsed_between(row.get("started_at"), row.get("finished_at")),
                    note=_text(row.get("error")),
                    flagged=status in {"failed", "error"},
                )
            )
        return ok(stages, source=source)

    return _guard("run_stages", _derive)


def derive_run_spans(store: RunEvidenceStore, run_id: str) -> SourceOutcome[list[RunSpan]]:
    """Trace-shaped view over recorded stages — spans are only emitted for
    stages that were actually recorded; none are manufactured."""

    def _derive() -> SourceOutcome[list[RunSpan]]:
        resolved = _stored_run_or_absent(store, run_id)
        if isinstance(resolved, SourceOutcome):
            return resolved
        source = "run_evidence"
        rows = store.list_stages(project_id(), run_id)
        if not rows:
            events = _orchestrator_run_events(run_id)
            if events is None:
                return unavailable("orchestrator")
            rows = _orchestrator_stage_rows(events)
            source = "orchestrator"
        bars = _stage_bars(rows)
        spans: list[RunSpan] = []
        for row, (_, width) in zip(rows, bars, strict=False):
            status = str(row.get("status") or "unknown")
            spans.append(
                RunSpan(
                    run_id=run_id,
                    name=str(row.get("stage_id") or "unknown"),
                    duration=_elapsed_between(row.get("started_at"), row.get("finished_at")),
                    width_percent=round(width, 1),
                    tone=_run_tone(status),
                )
            )
        return ok(spans, source=source)

    return _guard("run_spans", _derive)


def _event_level(row: dict[str, Any]) -> Literal["INFO", "ERROR"]:
    event_type = str(row.get("event_type") or "")
    payload_level = str((row.get("payload") or {}).get("level") or "").upper()
    if payload_level in {"ERROR", "CRITICAL", "FATAL"}:
        return "ERROR"
    if any(marker in event_type.lower() for marker in ("fail", "error", "abort")):
        return "ERROR"
    return "INFO"


def _event_message(row: dict[str, Any]) -> str:
    payload = redact_payload(row.get("payload") or {})
    message = payload.get("message") or payload.get("summary") or ""
    event_type = str(row.get("event_type") or "event")
    text = str(message).strip()
    if text and len(text) > 300:
        text = text[:300] + "…"
    return text or event_type


def derive_run_events(
    store: RunEvidenceStore,
    run_id: str,
    *,
    limit: int = 50,
    cursor: str | None = None,
) -> SourceOutcome[RunEventPage]:
    """Bounded page of the run's recorded events, ordered by sequence."""

    def _derive() -> SourceOutcome[RunEventPage]:
        resolved = _stored_run_or_absent(store, run_id)
        if isinstance(resolved, SourceOutcome):
            return resolved
        source = "run_evidence"
        rows = store.list_events(project_id(), run_id)
        if not rows:
            events = _orchestrator_run_events(run_id)
            if events is None:
                return unavailable("orchestrator")
            rows = _orchestrator_event_rows(events)
            source = "orchestrator"
        total = len(rows) if source == "orchestrator" else store.count_events(project_id(), run_id)
        offset = int(cursor) if cursor and cursor.isdigit() else 0
        page = rows[offset : offset + limit]
        next_cursor = str(offset + limit) if offset + limit < len(rows) else None
        return ok(
            RunEventPage(
                items=[
                    RunEvent(
                        run_id=run_id,
                        at=_canonical_timestamp(row.get("observed_at")) or "—",
                        level=_event_level(row),
                        message=_event_message(row),
                        attempt=int(row["attempt"]) if row.get("attempt") is not None else None,
                    )
                    for row in page
                ],
                next_cursor=next_cursor,
                total=total,
            ),
            source=source,
        )

    return _guard("run_events", _derive)


def derive_run_logs(
    store: RunEvidenceStore,
    run_id: str,
    *,
    limit: int = 100,
    cursor: str | None = None,
) -> SourceOutcome[RunLogPage]:
    """Bounded page of raw retained log lines — the event stream rendered
    verbatim so reconnects resume by cursor without duplicating or skipping
    retained rows."""

    def _derive() -> SourceOutcome[RunLogPage]:
        resolved = _stored_run_or_absent(store, run_id)
        if isinstance(resolved, SourceOutcome):
            return resolved
        source = "run_evidence"
        rows = store.list_events(project_id(), run_id)
        if not rows:
            events = _orchestrator_run_events(run_id)
            if events is None:
                return unavailable("orchestrator")
            rows = _orchestrator_event_rows(events)
            source = "orchestrator"
        total = len(rows) if source == "orchestrator" else store.count_events(project_id(), run_id)
        offset = int(cursor) if cursor and cursor.isdigit() else 0
        page = rows[offset : offset + limit]
        next_cursor = str(offset + limit) if offset + limit < len(rows) else None
        return ok(
            RunLogPage(
                items=[
                    MissionRunLogLine(
                        run_id=run_id,
                        at=_canonical_timestamp(row.get("observed_at")) or "—",
                        level=_event_level(row),
                        message=(f"{row.get('event_type') or 'event'} · {_event_message(row)}"),
                        attempt=int(row["attempt"]) if row.get("attempt") is not None else None,
                    )
                    for row in page
                ],
                next_cursor=next_cursor,
                total=total,
            ),
            source=source,
        )

    return _guard("run_logs", _derive)


def derive_run_quality(store: RunEvidenceStore, run_id: str) -> SourceOutcome[RunQualityReport]:
    """Quality outcomes recorded against this run's attempt — results carry
    their own attempt, and a passing check from another run can never appear
    here because the store query is run-scoped."""

    def _derive() -> SourceOutcome[RunQualityReport]:
        resolved = _stored_run_or_absent(store, run_id)
        if isinstance(resolved, SourceOutcome):
            return resolved
        rows = store.list_quality_results(project_id(), run_id)
        attempt = resolved.get("attempt") if resolved else None
        results: list[RunCheckOutcome] = []
        blocking_failure: RunQualityFailure | None = None
        for row in rows:
            passed = bool(row.get("passed"))
            blocking = bool(row.get("blocking"))
            evaluated = row.get("evaluated_count")
            failed = row.get("failed_count")
            outcome = "Executed · passed" if passed else "Executed · failed"
            if blocking:
                outcome += " · blocking"
            results.append(
                RunCheckOutcome(
                    run_id=run_id,
                    check=str(row.get("check_id") or "unknown"),
                    asset=_text(row.get("asset")),
                    stage=_text(row.get("stage_id")),
                    attempt=int(row.get("attempt") or 1),
                    outcome=outcome,
                    severity=_text(row.get("severity")),
                    blocking=blocking,
                    evaluated=int(evaluated) if evaluated is not None else None,
                    failed=int(failed) if failed is not None else None,
                    tone="success" if passed else ("danger" if blocking else "warning"),
                )
            )
            if not passed and blocking and blocking_failure is None:
                blocking_failure = RunQualityFailure(
                    run_id=run_id,
                    check=str(row.get("check_id") or "unknown"),
                    verdict="Failed · blocking",
                    detail=(
                        f"{failed if failed is not None else '?'} of "
                        f"{evaluated if evaluated is not None else '?'} "
                        f"evaluated rows failed for asset "
                        f"{_text(row.get('asset')) or 'unknown'}."
                    ),
                    sample=[],
                    sample_total=int(failed) if failed is not None else 0,
                )
        return ok(
            RunQualityReport(
                run_id=run_id,
                attempt=int(attempt) if attempt is not None else None,
                results=results,
                blocking_failure=blocking_failure,
            ),
            source="run_evidence",
        )

    return _guard("run_quality", _derive)


def derive_run_artifacts(store: RunEvidenceStore, run_id: str) -> SourceOutcome[list[RunArtifact]]:
    """Recorded evidence artifacts for the run — durable identity survives
    content expiry, so `size` reports only what was recorded."""

    def _derive() -> SourceOutcome[list[RunArtifact]]:
        resolved = _stored_run_or_absent(store, run_id)
        if isinstance(resolved, SourceOutcome):
            return resolved
        rows = store.list_artifacts(project_id(), run_id)
        return ok(
            [
                RunArtifact(
                    run_id=run_id,
                    name=str(row.get("artifact_id") or "unknown"),
                    size=_text(row.get("content_type")) or "—",
                    checksum=_text(row.get("checksum")) or "—",
                )
                for row in rows
            ],
            source="run_evidence",
        )

    return _guard("run_artifacts", _derive)


def derive_run_consumers(store: RunEvidenceStore, run_id: str) -> SourceOutcome[list[RunConsumer]]:
    """Downstream readers from recorded lineage edges — only edges actually
    observed or declared for this run appear; consumers are never
    manufactured."""

    def _derive() -> SourceOutcome[list[RunConsumer]]:
        resolved = _stored_run_or_absent(store, run_id)
        if isinstance(resolved, SourceOutcome):
            return resolved
        edges = store.list_lineage_edges(project_id(), run_id)
        resources = {
            str(r.get("normalized_identity") or r.get("resource_id") or ""): r
            for r in store.list_resources(project_id(), run_id)
        }
        consumers: list[RunConsumer] = []
        for edge in edges:
            target = str(edge.get("target") or "")
            if not target:
                continue
            resource = resources.get(target) or {}
            consumers.append(
                RunConsumer(
                    run_id=run_id,
                    name=target,
                    role=str(edge.get("origin") or "observed"),
                    current_snapshot=_text(resource.get("snapshot_after")),
                )
            )
        return ok(consumers, source="run_evidence")

    return _guard("run_consumers", _derive)


def derive_run_configuration(
    store: RunEvidenceStore, run_id: str
) -> SourceOutcome[list[RunConfigurationRow]]:
    """Resolved configuration entries recorded against the run row."""

    def _derive() -> SourceOutcome[list[RunConfigurationRow]]:
        resolved = _stored_run_or_absent(store, run_id)
        if isinstance(resolved, SourceOutcome):
            return resolved
        if resolved is None:
            return ok([], source="run_evidence")
        row = resolved
        fields = (
            ("Pipeline", "pipeline_name"),
            ("Trigger", "trigger"),
            ("Initiator", "initiator"),
            ("Effective identity", "effective_identity"),
            ("Partition", "partition_key"),
            ("Code version", "code_version"),
            ("Config version", "config_version"),
            ("Attempt", "attempt"),
            ("Trace", "trace_id"),
            ("Evidence completeness", "evidence_completeness"),
        )
        return ok(
            [
                RunConfigurationRow(run_id=run_id, label=label, value=_text(row.get(key)) or "—")
                for label, key in fields
            ],
            source="run_evidence",
        )

    return _guard("run_configuration", _derive)


# ------------------------------------------------------------------- dataset


def _asset_for(asset_id: str, assets: list | None = None):
    """Find an asset by id, tolerating an id that is a short name."""
    if assets is None:
        assets = _load_assets()
    for asset in assets:
        if asset.id == asset_id or asset.name == asset_id:
            return asset
    # Fall back to matching on the trailing segment, e.g. "marts.orders".
    for asset in assets:
        if asset.id.endswith(asset_id) or asset_id.endswith(asset.id):
            return asset
    return None


def derive_dataset_lineage(dataset_id: str) -> SourceOutcome[list[LineageNode]]:
    """Build the lineage chain from the asset graph's declared dependencies."""

    def _derive() -> SourceOutcome[list[LineageNode]]:
        assets, freshness = _source_read("mission_assets", _load_assets)
        target = _asset_for(dataset_id, assets)
        if target is None:
            return absent(f"Unknown dataset {dataset_id}")

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

        return ok(nodes, source="asset_registry", **freshness)

    return _guard("dataset_lineage", _derive)


def _load_quality_snapshot() -> dict[str, Any]:
    """Read the Dagster check snapshot through the existing provider owner."""
    from phlo_api.observatory_api.quality import (
        fetch_quality_snapshot,
        resolve_dagster_url,
    )

    snapshot = asyncio.run(fetch_quality_snapshot(resolve_dagster_url()))
    if snapshot is None:
        raise RuntimeError("quality snapshot fetch failed")
    return snapshot


def _asset_key_matches(asset_key: list[str], target_ids: set[str]) -> bool:
    """Match a provider asset key (path list) against a dataset's known ids,
    tolerating dot/slash joins and flat registry keys."""
    joined_slash = "/".join(asset_key)
    joined_dot = ".".join(asset_key)
    if joined_slash in target_ids or joined_dot in target_ids:
        return True
    tail = asset_key[-1] if asset_key else ""
    return bool(tail) and (tail in target_ids or any(key.endswith(tail) for key in target_ids))


_CHECK_OUTCOME_LABELS: dict[str, tuple[str, Outcome]] = {
    "PASSED": ("Passed", "success"),
    "FAILED": ("Failed", "danger"),
    "IN_PROGRESS": ("Running", "accent"),
    "SKIPPED": ("Skipped", "muted"),
    "NEVER_EVALUATED": ("Never evaluated", "muted"),
}


def _provider_dataset_checks(snapshot: dict[str, Any], target_ids: set[str]) -> list[DatasetCheck]:
    """Provider check rows for one asset — execution status stays distinct
    from evaluation result (running/skipped/never-evaluated are not pass or
    fail)."""
    checks: list[DatasetCheck] = []
    for check in snapshot.get("latest_checks", []):
        if not _asset_key_matches(check.asset_key, target_ids):
            continue
        label, tone = _CHECK_OUTCOME_LABELS.get(check.status, ("Unknown", "muted"))
        if check.status == "FAILED" and check.severity == "WARN":
            label, tone = "Failed · warning", "warning"
        checks.append(DatasetCheck(name=check.name, outcome=label, tone=tone))
    return checks


def _mission_dataset_checks(
    dataset_id: str,
    profile: ObservatoryDatasetProfile,
) -> tuple[list[DatasetCheck], dict[str, Any], str]:
    """Checks for the dataset page: provider executions win when the quality
    reader answers; declared manifest checks stay labelled as declared, and
    an unreachable provider never fabricates an evaluation state. Returns
    (checks, freshness, source)."""
    declared = [_mission_dataset_check(check) for check in profile.quality]
    snapshot, freshness = _safe_source_read("mission_quality_snapshot", _load_quality_snapshot)
    if snapshot is None:
        return declared, freshness, "asset_registry"
    target_ids = {dataset_id, profile.dataset.id}
    if profile.asset is not None:
        target_ids |= {profile.asset.id, profile.asset.name}
    checks = _provider_dataset_checks(snapshot, target_ids)
    seen = {check.name for check in checks}
    checks.extend(row for row in declared if row.name not in seen)
    return checks, freshness, "quality_provider"


def derive_dataset_checks(dataset_id: str) -> SourceOutcome[list[DatasetCheck]]:
    """Standalone check-list derive — the same provider-first mapping the
    dataset detail payload uses, over the registry asset's identity."""

    def _derive() -> SourceOutcome[list[DatasetCheck]]:
        assets, freshness = _source_read("mission_assets", _load_assets)
        target = _asset_for(dataset_id, assets)
        if target is None:
            return absent(f"Unknown dataset {dataset_id}")
        snapshot, snap_freshness = _safe_source_read(
            "mission_quality_snapshot", _load_quality_snapshot
        )
        target_ids = {dataset_id, target.id, target.name}
        checks = _provider_dataset_checks(snapshot, target_ids) if snapshot is not None else []
        seen = {check.name for check in checks}
        checks.extend(
            DatasetCheck(name=check, outcome="Declared", tone="muted")
            for check in target.checks
            if check not in seen
        )
        return ok(
            checks,
            source="quality_provider" if snapshot is not None else "asset_registry",
            **_merge_freshness(freshness, snap_freshness),
        )

    return _guard("dataset_checks", _derive)


# ----------------------------------------------------------------- releases
#
# A WAP branch *is* a release candidate: the run stages its write on an isolated
# branch and promotion merges it. So Nessie is the release source of truth —
# candidate branches are pending releases, `main` is what consumers read.


def _wap_report_for(candidate_id: str, reports: list[WapReport]) -> WapReport | None:
    """Find the lifecycle report a candidate id addresses.

    Accepts the canonical logical run id or the staging ref (branch) as an
    alias so branch-shaped URLs still resolve to the governed record.
    """
    for report in reports:
        if report.logical_run_id == candidate_id:
            return report
    for report in reports:
        if report.branch == candidate_id:
            return report
    return None


def _candidate_tables(report: WapReport, detail) -> list[str]:
    """Table names the candidate stages: audited snapshot rows first, then the
    staging ref's declared contents, then nothing (never a guessed name)."""
    tables = [str(row["table"]) for row in report.candidates if row.get("table")]
    if tables:
        return tables
    return [_content_id(table) for table in (detail.contents if detail else [])]


def _readiness_label(readiness: Readiness, report: WapReport) -> str:
    return {
        "ready": "Ready for review",
        "in_progress": "Promotion in progress",
        "blocked": "Blocked",
        "awaiting": "Awaiting evidence",
    }[readiness]


def _candidate_row(report: WapReport, detail) -> ReleaseCandidate:
    manifest_valid = verify_launch_binding(project_root(), report)
    readiness, blockers = candidate_readiness(report, manifest_valid=manifest_valid)
    tables = _candidate_tables(report, detail)
    compare = detail.compare if detail else {}
    changed = sum(int(compare.get(key) or 0) for key in ("added", "changed", "removed"))
    commits = len(detail.commits) if detail else 0
    return ReleaseCandidate(
        id=report.logical_run_id,
        dataset=tables[0] if tables else report.branch or report.logical_run_id,
        provider="Nessie" if report.strategy == "branch" else "Snapshot catalog",
        strategy="Branch merge" if report.strategy == "branch" else "Snapshot promotion",
        readiness=_readiness_label(readiness, report),
        evidence=(
            f"{len(report.candidates)} audited snapshots"
            if report.candidates
            else f"{commits} commits · {changed} object changes"
            if detail is not None
            else "Branch detail unavailable"
        ),
        created_at=report.created_at or "—",
        action="Inspect",
        run_id=report.logical_run_id,
        orchestrator_run_id=report.dagster_run_id,
        staging_ref=report.branch,
        source_revision=report.launch_source_hash or report.source_hash,
        target_revision=report.launch_target_hash_before or report.target_hash_before,
        blockers=blockers,
    )


def derive_release_candidates() -> SourceOutcome[list[ReleaseCandidate]]:
    """List release candidates from governed WAP launch evidence.

    A candidate exists only where a WAP lifecycle report does — a bare branch
    named like a staging ref is retained data, not a promotable candidate.
    """

    def _derive() -> SourceOutcome[list[ReleaseCandidate]]:
        reports = [
            report
            for report in load_wap_reports(project_root())
            if report.status in WAP_PENDING_STATUSES | WAP_BLOCKED_STATUSES
        ]
        freshness: dict[str, Any] = {"stale": False, "last_confirmed_at": None}
        candidates: list[ReleaseCandidate] = []
        for report in reports:
            detail = None
            if report.branch:
                detail, detail_meta = _safe_source_read(
                    f"mission_branch_detail/{report.branch}",
                    lambda branch_name=report.branch: _load_branch_detail(branch_name),
                )
                freshness = _merge_freshness(freshness, detail_meta)
            candidates.append(_candidate_row(report, detail))
        return ok(candidates, source="wap_reports", **freshness)

    return _guard("release_candidates", _derive)


def _content_id(content) -> str:
    """Resource refs expose `id`; tolerate a `name` fallback for other providers."""
    return getattr(content, "id", None) or getattr(content, "name", None) or "unknown"


def _branch_timestamp(detail) -> str:
    if detail and detail.commits:
        return detail.commits[0].started_at or "—"
    return "—"


def derive_release_candidate(candidate_id: str) -> SourceOutcome[ReleaseCandidateDetail]:
    """Build the review payload for one candidate's governed launch evidence."""

    def _derive() -> SourceOutcome[ReleaseCandidateDetail]:
        reports = load_wap_reports(project_root())
        report = _wap_report_for(candidate_id, reports)
        if report is None:
            return absent(f"Unknown release candidate {candidate_id}")
        detail = None
        freshness: dict[str, Any] = {"stale": False, "last_confirmed_at": None}
        branch_name = report.branch
        if branch_name:
            try:
                detail, detail_meta = _source_read(
                    f"mission_branch_detail/{branch_name}",
                    lambda: _load_branch_detail(branch_name),
                )
                freshness = _merge_freshness(freshness, detail_meta)
            except HTTPException:
                # A deleted staging ref is evidence, not absence of the candidate.
                detail = None
        return ok(
            _release_candidate_detail(candidate_id, report, detail),
            source="wap_reports",
            **freshness,
        )

    return _guard("release_candidate_detail", _derive)


def _catalog_registry_view() -> Any | None:
    """The registry ``_catalog_branch_provider`` sees — resolved through the
    observatory module attribute so loader seams that patch it apply here."""
    from phlo_api.observatory_api import observatory as _observatory

    return _observatory._load_capability_registry()


def _catalog_provider() -> Any | None:
    """Resolve the registered catalog provider regardless of strategy shape —
    branch catalogs (Nessie) and snapshot-promotion catalogs (Polaris) both
    back release review."""
    catalog = _catalog_branch_provider()
    if catalog is not None:
        return catalog
    # Same registry seam as _catalog_branch_provider (its module's
    # _load_capability_registry is the patchable loader): enumerate the
    # catalog specs and take the first provider rather than consulting a
    # second registry view.
    registry = _catalog_registry_view()
    if registry is None:
        return None
    try:
        catalog_specs = registry.list("catalog")
    except Exception:
        return None
    for spec in catalog_specs:
        provider = getattr(spec, "provider", None)
        if provider is not None:
            return provider
    return None


def _released_snapshot_for(table: str, catalog: Any) -> str | None:
    """The snapshot consumers currently resolve for a table, when the catalog
    exposes release records (snapshot-promotion providers only)."""
    resolve = getattr(catalog, "resolve_release", None)
    if not callable(resolve):
        return None
    try:
        record = resolve(table_name=table)
    except Exception:
        return None
    return str(record.snapshot_id) if record is not None else None


def _released_revision_for(report: WapReport, catalog: Any) -> str | None:
    """Branch-strategy released state is the target ref's current head."""
    get_hash = getattr(catalog, "get_branch_hash", None)
    if not callable(get_hash):
        return None
    try:
        return get_hash(report.target_branch or WAP_TARGET_BRANCH)
    except Exception:
        return None


def _release_candidate_detail(
    candidate_id: str, report: WapReport, detail
) -> ReleaseCandidateDetail:
    manifest_valid = verify_launch_binding(project_root(), report)
    readiness, blockers = candidate_readiness(report, manifest_valid=manifest_valid)
    catalog = _catalog_provider()
    tables = _candidate_tables(report, detail)
    candidate_snapshots = {
        str(row.get("table")): str(row.get("snapshot_id"))
        for row in report.candidates
        if row.get("table") and row.get("snapshot_id") is not None
    }
    fallback_snapshot = report.source_hash or report.launch_source_hash or "unrecorded"
    released_ref = _released_revision_for(report, catalog)
    changes = [
        CandidateSnapshotChange(
            table=table,
            released_snapshot=(_released_snapshot_for(table, catalog) or released_ref or "—"),
            candidate_snapshot=candidate_snapshots.get(table, fallback_snapshot),
            row_delta="—",
        )
        for table in tables
    ]

    evidence = [
        CandidateEvidenceRow(
            name="Launch binding",
            detail=(
                "Launch manifest verified against the recorded launch facts"
                if manifest_valid
                else "No launch manifest checksum recorded"
                if manifest_valid is None
                else "Launch manifest missing or content-mismatched"
            ),
            outcome="Verified"
            if manifest_valid
            else "Missing"
            if manifest_valid is None
            else "Invalid",
            tone="success" if manifest_valid else "warning" if manifest_valid is None else "danger",
        ),
        CandidateEvidenceRow(
            name="Orchestrator run",
            detail=(
                f"Logical run {report.logical_run_id}"
                + (f" · orchestrator run {report.dagster_run_id}" if report.dagster_run_id else "")
            ),
            outcome="Recorded" if report.dagster_run_id else "Missing",
            tone="success" if report.dagster_run_id else "danger",
        ),
        CandidateEvidenceRow(
            name="Audit decision",
            detail=(
                str(report.raw.get("quality_evidence", {}).get("decision"))
                if isinstance(report.raw.get("quality_evidence"), dict)
                and report.raw["quality_evidence"].get("decision")
                else "No durable aggregate decision recorded"
            ),
            outcome="Recorded"
            if isinstance(report.raw.get("quality_evidence"), dict)
            and report.raw["quality_evidence"].get("decision")
            else "Missing",
            tone="success"
            if isinstance(report.raw.get("quality_evidence"), dict)
            and report.raw["quality_evidence"].get("decision") in {"passed", "passed_with_warnings"}
            else "warning",
        ),
        CandidateEvidenceRow(
            name="Merge receipt",
            detail=(
                f"{report.target_branch or 'main'} advanced to {report.target_hash_after}"
                if report.target_hash_after
                else f"Lifecycle status: {report.status.replace('_', ' ')}"
            ),
            outcome="Confirmed"
            if report.status in WAP_RELEASED_STATUSES and report.target_hash_after
            else "Not promoted",
            tone="success"
            if report.status in WAP_RELEASED_STATUSES and report.target_hash_after
            else "muted",
        ),
    ]
    if blockers:
        evidence.append(
            CandidateEvidenceRow(
                name="Blockers",
                detail=" · ".join(blockers),
                outcome="Blocking",
                tone="danger",
            )
        )
    if report.failure_detail:
        evidence.append(
            CandidateEvidenceRow(
                name="Catalog detail",
                detail=report.failure_detail,
                outcome="Provider response",
                tone="muted",
            )
        )

    strategy_label = "Branch merge" if report.strategy == "branch" else "Snapshot promotion"
    return ReleaseCandidateDetail(
        id=report.logical_run_id,
        candidate=None,
        subtitle=f"{strategy_label} · candidate {report.logical_run_id}",
        status=_readiness_label(readiness, report),
        revision=(
            f"Source {report.launch_source_hash or report.source_hash or 'unrecorded'} · "
            f"target {report.launch_target_hash_before or report.target_hash_before or 'unrecorded'}"
        ),
        snapshot_changes=changes,
        required_evidence=evidence,
        publication_plan=[
            PublicationPlanRow(label="Operation", value=f"WAP {strategy_label.lower()}"),
            PublicationPlanRow(label="Strategy", value=report.strategy),
            PublicationPlanRow(label="Source ref", value=report.branch or "unrecorded"),
            PublicationPlanRow(label="Target", value=report.target_branch or WAP_TARGET_BRANCH),
            PublicationPlanRow(
                label="Intent",
                value=report.status.replace("_", " ").title(),
            ),
        ],
        run_id=report.logical_run_id,
        orchestrator_run_id=report.dagster_run_id,
        staging_ref=report.branch,
        strategy=report.strategy,
        source_revision=report.launch_source_hash or report.source_hash,
        target_revision=report.launch_target_hash_before or report.target_hash_before,
        blockers=blockers,
        failure_detail=report.failure_detail,
    )


def _current_revisions(report: WapReport, catalog: Any) -> tuple[str | None, str | None]:
    """Read the catalog's current source/target revisions for this candidate.

    Branch strategy: source = staging-ref head, target = main's head.
    Snapshot strategy: source = the namespace's current candidate snapshot set
    (joined like the promotion record's ``source_hash``), target = the release
    pointer revision.
    """
    if catalog is None:
        return None, None
    if report.strategy == "snapshot":
        source_hash = None
        list_candidates = getattr(catalog, "list_candidates", None)
        if callable(list_candidates) and report.branch:
            try:
                rows = list_candidates(namespace=report.branch)
                source_hash = (
                    ",".join(sorted(str(getattr(row, "snapshot_id", "")) for row in rows)) or None
                )
            except Exception:
                source_hash = None
        revision = getattr(catalog, "release_revision", None)
        try:
            target = str(int(revision())) if callable(revision) else None
        except Exception:
            target = None
        return source_hash, target
    get_branch_hash = getattr(catalog, "get_branch_hash", None)
    if not callable(get_branch_hash):
        return None, None
    try:
        source = get_branch_hash(report.branch) if report.branch else None
        target = get_branch_hash(report.target_branch or WAP_TARGET_BRANCH)
    except Exception:
        return None, None
    return (str(source) if source else None), (str(target) if target else None)


def derive_promotion_preview(candidate_id: str) -> SourceOutcome[PromotionPreview]:
    """Read-only evaluation of the WAP promotion gates for one candidate.

    Re-checks the launch binding, strategy match, audit decision and current
    source/target revisions — the same gates the promotion path enforces —
    and binds them into the preview digest. Never mutates catalog state.
    """

    def _derive() -> SourceOutcome[PromotionPreview]:
        reports = load_wap_reports(project_root())
        report = _wap_report_for(candidate_id, reports)
        if report is None or report.status in WAP_RELEASED_STATUSES:
            return absent(f"Unknown release candidate {candidate_id}")
        if report.status in WAP_BLOCKED_STATUSES:
            return absent(
                f"Candidate {candidate_id} is not promotable ({report.status.replace('_', ' ')})"
            )

        from phlo.infrastructure import load_wap_config

        catalog = _catalog_provider()
        try:
            configured_strategy = load_wap_config(project_root()).strategy
        except Exception:
            configured_strategy = "branch"
        current_source, current_target = _current_revisions(report, catalog)
        quality_evidence = report.raw.get("quality_evidence")
        quality_decision = (
            str(quality_evidence.get("decision"))
            if isinstance(quality_evidence, dict) and quality_evidence.get("decision")
            else None
        )
        preview = evaluate_promotion_preview(
            report,
            project_root=project_root(),
            configured_strategy=configured_strategy,
            current_source_hash=current_source,
            current_target_revision=current_target,
            quality_decision=quality_decision,
        )
        return ok(
            PromotionPreview(
                candidate_id=preview.candidate_id,
                eligible=preview.eligible,
                checks=[
                    PromotionPreviewCheck(
                        name=check.name,
                        outcome=check.outcome,
                        detail=check.detail,
                        passed=check.passed,
                    )
                    for check in preview.checks
                ],
                digest=preview.digest,
                strategy=preview.strategy,
            ),
            source="wap_reports",
            stale=False,
            last_confirmed_at=None,
        )

    return _guard("promotion_preview", _derive)


def _wap_release_row(report: WapReport) -> CompletedRelease:
    return CompletedRelease(
        id=report.release_id or f"rel-{report.logical_run_id[:8]}",
        dataset=", ".join(_candidate_tables(report, None))
        or report.branch
        or report.logical_run_id,
        provider="Nessie" if report.strategy == "branch" else "Snapshot catalog",
        provider_strategy=(
            "Nessie · Branch merge" if report.strategy == "branch" else "Snapshot promotion"
        ),
        reference=(
            f"{report.target_branch or WAP_TARGET_BRANCH} · "
            f"{(report.target_hash_after or 'unrecorded')[:12]}"
        ),
        finished_at=report.updated_at or "—",
        outcome=(
            "Merge confirmed"
            if report.target_hash_after
            else "Promoted — merge receipt hash unrecorded"
        ),
        run_id=report.logical_run_id,
        orchestrator_run_id=report.dagster_run_id,
        release_revision=report.target_hash_after,
    )


def _commit_release_row(entry: Any, strategy: str, outcome: str) -> CompletedRelease:
    """A catalog commit on the consumer ref rendered as a completed release —
    the commit IS the publish for non-WAP writers."""
    tables = sorted(set(entry.operations))
    dataset = ", ".join(tables[:3]) or entry.message or "catalog change"
    if len(tables) > 3:
        dataset += f" +{len(tables) - 3} more"
    return CompletedRelease(
        id=f"commit-{entry.hash[:16]}",
        dataset=dataset,
        provider="Nessie",
        provider_strategy=strategy,
        reference=f"{WAP_TARGET_BRANCH} · {entry.hash[:12]}",
        finished_at=entry.committed_at or "—",
        outcome=outcome,
        release_revision=entry.hash,
    )


def _consumer_ref_commit_rows(receipt_hashes: set[str]) -> list[CompletedRelease]:
    """Direct writes and unrecorded merges on the consumer-facing ref.

    Walks ``main``'s first-parent chain: commits reachable only through a
    merge's second parent are that merge's payload and are already counted
    inside its release receipt. On-chain commits split into merge receipts
    (two or more parents) and direct writes (single parent) — the publish
    path a non-WAP workflow actually takes.
    """
    catalog = _catalog_provider()
    get_log = getattr(catalog, "get_commit_log", None)
    if not callable(get_log):
        return []
    try:
        entries = get_log(WAP_TARGET_BRANCH)
    except Exception:  # noqa: BLE001 - catalog log is additive evidence, not a blocker
        logger.warning("completed_releases_commit_log_failed", exc_info=True)
        return []
    by_hash = {entry.hash: entry for entry in entries}
    rows: list[CompletedRelease] = []
    cursor = entries[0].hash if entries else None
    visited: set[str] = set()
    while cursor and cursor in by_hash and cursor not in visited:
        visited.add(cursor)
        entry = by_hash[cursor]
        if entry.hash in receipt_hashes:
            pass  # WAP merge receipt — already a row from the durable report
        elif len(entry.parent_hashes) >= 2:
            rows.append(_commit_release_row(entry, "Nessie · Merge", "Merge confirmed"))
        else:
            rows.append(_commit_release_row(entry, "Nessie · Direct write", "Committed to main"))
        cursor = entry.parent_hashes[0] if entry.parent_hashes else None
    return rows


def derive_completed_releases() -> SourceOutcome[list[CompletedRelease]]:
    """List what consumers can read: governed WAP merge receipts plus the
    direct commits non-WAP workflows publish onto the consumer ref.

    A succeeded run is not a release: WAP rows require a lifecycle report
    that reached ``promoted``/``cleanup_complete``; direct commits are
    confirmed by their presence on ``main``'s first-parent history.
    """

    def _derive() -> SourceOutcome[list[CompletedRelease]]:
        reports = load_wap_reports(project_root())
        receipt_hashes = {
            report.target_hash_after for report in reports if report.target_hash_after
        }
        rows = [
            _wap_release_row(report) for report in reports if report.status in WAP_RELEASED_STATUSES
        ]
        rows.extend(_consumer_ref_commit_rows(receipt_hashes))
        rows.sort(key=lambda row: row.finished_at or "", reverse=True)
        return ok(rows[:15], source="wap_reports", stale=False, last_confirmed_at=None)

    return _guard("completed_releases", _derive)


def _release_timestamp(iso: str | None) -> str:
    if not iso:
        return "—"
    try:
        return datetime.fromisoformat(iso.replace("Z", "+00:00")).strftime("%b %d · %H:%M")
    except ValueError:
        return iso


def build_release_summary(
    candidates: list[ReleaseCandidate],
    completed: list[CompletedRelease],
) -> list[SummaryMetricRow]:
    """The Releases summary band — one metric per promotion state the rows
    actually occupy, so the counters can never describe a bucket no row is in.
    A candidate is a staging branch holding audited snapshots; "ready" means
    the promotion gates record no blocker, "blocked" means a terminal state
    stopped the release."""
    by_readiness = Counter(row.readiness for row in candidates)
    providers = {row.provider for row in candidates if row.provider}

    def _candidates(count: int) -> str:
        return f"{count} candidate{'s' if count != 1 else ''}"

    metrics = [
        SummaryMetricRow(
            label="Pending",
            value=_candidates(len(candidates)),
            hint=(
                f"Staging branches across {len(providers)} catalog provider{'s' if len(providers) != 1 else ''}"
                if providers
                else "No staging branches recorded"
            ),
            tone="muted",
        ),
        SummaryMetricRow(
            label="Ready for review",
            value=_candidates(by_readiness["Ready for review"]),
            hint="No promotion blockers recorded",
            tone="success" if by_readiness["Ready for review"] else "muted",
        ),
        SummaryMetricRow(
            label="Blocked",
            value=_candidates(by_readiness["Blocked"]),
            hint="Promotion failed or evidence missing",
            tone="danger" if by_readiness["Blocked"] else "muted",
        ),
    ]
    for label, hint in (
        ("Awaiting evidence", "Run still producing evidence"),
        ("Promotion in progress", "Merge attempted; outcome pending"),
    ):
        if by_readiness[label]:
            metrics.append(
                SummaryMetricRow(
                    label=label,
                    value=_candidates(by_readiness[label]),
                    hint=hint,
                    tone="warning",
                )
            )
    latest = max((row.finished_at for row in completed if row.finished_at), default=None)
    metrics.append(
        SummaryMetricRow(
            label="Last release",
            value=_release_timestamp(latest),
            hint=f"{len(completed)} promotion{'s' if len(completed) != 1 else ''} confirmed",
            tone="muted",
        )
    )
    return metrics


def derive_release_summary(
    candidates: list[ReleaseCandidate] | None,
    completed: list[CompletedRelease] | None,
) -> list[SummaryMetricRow] | None:
    """Build the Releases summary band from the derived rows."""
    if candidates is None and completed is None:
        return None
    return build_release_summary(candidates or [], completed or [])


def derive_dataset(dataset_id: str) -> SourceOutcome[MissionDatasetDetail]:
    """Full dataset page resolved through the shared identity cascade.

    ``_load_dataset_profile`` owns id resolution (listed dataset → asset →
    candidate/promoted table), so ``marts/orders``, ``marts.orders`` and
    ``candidate:<table>`` ids all reach the same authoritative record without
    aliasing. Schema and preview come from the resolved table's real catalog
    metadata and the query engine; a live ``select`` is labelled current-state,
    never snapshot-pinned.
    """

    def _derive() -> SourceOutcome[MissionDatasetDetail]:
        profile, freshness = _source_read(
            f"mission_datasets/{dataset_id}",
            lambda: _load_mission_dataset_profile(dataset_id),
        )
        if profile is None:
            return absent(f"Unknown dataset {dataset_id}")

        dataset = profile.dataset
        primary_table = profile.tables[0] if profile.tables else None
        preview, preview_freshness = _mission_preview(primary_table)
        related_ids = {
            dataset.id,
            *([profile.asset.id] if profile.asset is not None else []),
            *(table.id for table in profile.tables),
            *(ref.id for ref in dataset.source_refs),
        }
        runs, runs_state, runs_freshness = _mission_dataset_runs(related_ids)
        checks, checks_freshness, _checks_source = _mission_dataset_checks(dataset_id, profile)
        merged = _merge_freshness(freshness, preview_freshness, runs_freshness, checks_freshness)
        return ok(
            MissionDatasetDetail(
                id=dataset.id,
                name=dataset.name or dataset.id,
                status=_dataset_status_label(dataset),
                summary=dataset.description or f"Dataset {dataset.id}",
                metrics=_dataset_metrics(profile, checks, preview),
                schema_fields=_mission_schema_fields(primary_table),
                preview=preview,
                checks=checks,
                lineage=_mission_lineage(profile),
                ownership=_mission_ownership(profile),
                access=[],
                runs=runs,
                runs_state=runs_state,
            ),
            source="dataset_profile",
            **merged,
        )

    return _guard("dataset_detail", _derive)


def _load_mission_dataset_profile(dataset_id: str) -> ObservatoryDatasetProfile | None:
    """Resolve a dataset via the substrate cascade; ``None`` = absent."""
    try:
        return _load_dataset_profile(dataset_id)
    except HTTPException as exc:
        if exc.status_code == 404:
            return None
        raise


def _dataset_status_label(dataset: ObservatoryDataset) -> str:
    if dataset.candidate:
        return "Candidate"
    state = dataset.publication_state or "draft"
    return {
        "published": "Published",
        "draft": "Draft",
        "archived": "Archived",
    }.get(state, state.replace("_", " ").title())


def _mission_schema_fields(table: ObservatoryTable | None) -> list[DatasetSchemaField]:
    """Schema columns from the resolved table's metadata — never invented."""
    if table is None:
        return []
    columns = _table_columns_from_metadata(table)
    types = _table_column_types_from_metadata(table, columns)
    meta_columns = table.metadata.get("columns")
    by_name: dict[str, Mapping[str, Any]] = {}
    if isinstance(meta_columns, list):
        for column in meta_columns:
            if isinstance(column, Mapping):
                name = column.get("name") or column.get("column_name")
                if name is not None:
                    by_name[str(name)] = column
    fields: list[DatasetSchemaField] = []
    for index, name in enumerate(columns):
        column = by_name.get(name, {})
        roles = [
            label
            for key, label in (
                ("primary_key", "Primary key"),
                ("unique", "Unique"),
                ("partition", "Partition source"),
            )
            if column.get(key) is True
        ]
        nullable = column.get("nullable")
        fields.append(
            DatasetSchemaField(
                field=name,
                type=types[index],
                nullable="No" if nullable is False else ("Yes" if nullable is True else "Unknown"),
                role=" · ".join(roles) or "—",
            )
        )
    return fields


def _mission_preview(
    table: ObservatoryTable | None,
) -> tuple[DatasetPreview, dict[str, Any]]:
    """Bounded row preview through the provider query path.

    ``pinned`` is only claimed when a preview is read against a recorded
    snapshot — a live ``select`` is current-state, not snapshot-consistent.
    """
    if table is None:
        return DatasetPreview(
            columns=[],
            rows=[],
            state="no_table",
            detail="No queryable table resolves for this dataset.",
        ), {}

    def _load() -> DatasetPreview:
        preview = _load_table_preview(table.id, limit=_PREVIEW_LIMIT, offset=0)
        rows = [
            ["" if row.get(column) is None else str(row.get(column)) for column in preview.columns]
            for row in preview.rows
            if isinstance(row, Mapping)
        ]
        return DatasetPreview(
            columns=preview.columns,
            rows=rows,
            ref=_query_relation_for_table(table) or table.id,
            pinned=False,
            state=preview.state,
            detail=preview.message,
            has_more=preview.has_more,
            limit=preview.limit,
            offset=preview.offset,
        )

    result, freshness = _safe_source_read(f"mission_table_preview/{table.id}", _load)
    if result is None:
        return DatasetPreview(
            columns=[],
            rows=[],
            state="unavailable",
            detail="The configured query engine could not return a preview.",
        ), freshness
    return result, freshness


def _mission_dataset_runs(
    related_ids: set[str],
) -> tuple[list[DatasetRunRef], str, dict[str, Any]]:
    """Recent runs that produced or published this dataset's resources."""
    runs, freshness = _safe_source_read("mission_runs", load_runs_strict)
    if runs is None:
        return [], "unavailable", freshness
    matched = [run for run in runs if related_ids.intersection(ref.id for ref in run.assets)]
    matched.sort(key=lambda run: run.completed_at or run.started_at or "", reverse=True)
    return (
        [
            DatasetRunRef(
                run_id=run.id,
                finished_at=run.completed_at or run.started_at or "—",
                outcome=run.status,
                release=_metadata_text(run.metadata, "branch") or "—",
                duration=_elapsed(run.duration_seconds),
            )
            for run in matched[:_DATASET_RUNS_LIMIT]
        ],
        "ready",
        freshness,
    )


def _mission_lineage(profile: ObservatoryDatasetProfile) -> list[LineageNode]:
    nodes = [
        LineageNode(name=ref.label or ref.id, role=f"{ref.kind} · Upstream")
        for ref in profile.upstream
    ]
    nodes.append(LineageNode(name=profile.dataset.name, role="This dataset", current=True))
    nodes.extend(
        LineageNode(name=ref.label or ref.id, role=f"{ref.kind} · Downstream")
        for ref in profile.downstream
    )
    return nodes


def _mission_ownership(profile: ObservatoryDatasetProfile) -> DatasetOwnership:
    if profile.asset is not None:
        return _ownership_from_asset(profile.asset)
    dataset = profile.dataset
    metadata = dataset.metadata if isinstance(dataset.metadata, Mapping) else {}
    return DatasetOwnership(
        owner=dataset.owner or _metadata_text(metadata, "owner"),
        domain=_metadata_text(metadata, "group") or _metadata_text(metadata, "domain"),
        freshness_target=_freshness_label(metadata),
        schedule=_metadata_text(metadata, "schedule"),
        classification=", ".join(dataset.classifications) or None,
        contract_version=_metadata_text(metadata, "contract_version"),
        retention=_metadata_text(metadata, "retention"),
    )


def _mission_dataset_check(check) -> DatasetCheck:
    status = getattr(check, "status", "unknown")
    blocking = bool(getattr(check, "blocking", False))
    label = status.replace("_", " ").title()
    if blocking and status.lower() in {"failed", "failure"}:
        label = f"{label} · blocking"
    return DatasetCheck(
        name=getattr(check, "name", None) or getattr(check, "id", "check"),
        outcome=label,
        tone=_run_tone(status),
    )


def _dataset_metrics(
    profile: ObservatoryDatasetProfile,
    checks: list[DatasetCheck],
    preview: DatasetPreview,
) -> list[SummaryMetricRow]:
    dataset = profile.dataset
    passed = sum(1 for check in checks if check.outcome.lower().startswith("passed"))
    metrics = [
        SummaryMetricRow(
            label="Upstream",
            value=str(len(profile.upstream)),
            hint="Declared dependencies",
            tone="muted",
        ),
        SummaryMetricRow(
            label="Downstream",
            value=str(len(profile.downstream)),
            hint="Assets that depend on this dataset",
            tone="muted",
        ),
        SummaryMetricRow(
            label="Checks",
            value=str(len(checks)),
            hint=f"{passed} passed" if checks else "No checks declared",
            tone="success" if checks and passed == len(checks) else "muted",
        ),
    ]
    if preview.state == "ready" and preview.rows:
        metrics.append(
            SummaryMetricRow(
                label="Preview",
                value=f"{len(preview.rows)} rows",
                hint="Current table contents · not snapshot-pinned",
                tone="muted",
            )
        )
    readiness = getattr(dataset, "readiness_state", None)
    if readiness:
        metrics.append(
            SummaryMetricRow(
                label="Readiness",
                value=str(readiness).replace("_", " ").title(),
                hint="Reported by the dataset authority",
                tone="muted",
            )
        )
    return metrics


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


def derive_ownership_gaps() -> SourceOutcome[list[OwnershipGap]]:
    """Derive ownership gaps from declared asset metadata.

    An asset that declares no ``owner`` is a gap. The requirement named is the
    one that is actually missing, so the report stays truthful as more
    declarations are added rather than hardcoding a fixed list of gaps. An
    empty result means every observable asset declares what it needs.
    """

    def _derive() -> SourceOutcome[list[OwnershipGap]]:
        assets, freshness = _source_read("mission_assets", _load_assets)
        return ok(_ownership_gaps(assets), source="asset_registry", **freshness)

    return _guard("ownership_gaps", _derive)


def _ownership_gaps(assets: list) -> list[OwnershipGap]:
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
