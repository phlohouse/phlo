/**
 * Mission Control API client.
 *
 * Wraps the `/api/observatory/mission` read models in typed query options so
 * routes call `useQuery(queries.alerts())` and never touch fetch directly.
 *
 * The Vite dev server proxies `/api/observatory` to phlo-api, so relative URLs
 * work in every environment without a configured base.
 */
import { infiniteQueryOptions, queryOptions } from "@tanstack/react-query";

import type {
  AccessDrift,
  AuditEvent,
  CompletedRelease,
  CoverageRow,
  DatasetGovernance,
  MaterializeResult,
  MissionAlert,
  MissionAttentionItem,
  MissionContext,
  MissionDataProduct,
  MissionDatasetDetail,
  MissionEnvironment,
  MissionExecutionRow,
  MissionOverviewRail,
  MissionRunDetail,
  MissionRunList,
  MissionRunLogLine,
  MissionServiceVisibility,
  NotificationRule,
  ObservatoryActionResult,
  ObservatoryAsset,
  ObservatoryDataset,
  ObservatoryListPage,
  ObservatoryRun,
  ObservatoryServiceDetail,
  OwnershipGap,
  PlatformService,
  PlatformSummaryMetric,
  PromotionPreview,
  PromotionResult,
  ProviderConnection,
  ProviderImpact,
  PublicationPlan,
  PublicationReview,
  ReadEnvelope,
  ReleaseCandidate,
  ReleaseCandidateDetail,
  ReleaseSummaryMetric,
  RunActionResult,
  RunArtifact,
  RunConfigurationRow,
  RunConsumer,
  RunEventPage,
  RunLogPage,
  RunQualityReport,
  RunSpan,
  RunStageView,
  ServiceDiagnostics,
  SummaryMetricRow,
  WorkspaceDefault,
  WorkspaceMember,
} from "./types";

/** Machine-readable error carrying the server's problem detail. */
export class ApiError extends Error {
  readonly status: number;

  constructor(status: number, detail: string) {
    super(detail);
    this.name = "ApiError";
    this.status = status;
  }
}

/** Base for the Mission Control read models. */
const MISSION_BASE = "/api/observatory/mission";
/** Base for the platform substrate endpoints (assets, tables, runs). */
const SUBSTRATE_BASE = "/api/observatory";

async function requestRaw<T>(path: string, signal: AbortSignal | undefined, base: string) {
  const response = await fetch(`${base}${path}`, {
    headers: { accept: "application/json" },
    // Same-origin cookie sessions ride along; the API is always same-origin
    // in the installed app and through the dev proxy.
    credentials: "same-origin",
    signal,
  });

  if (!response.ok) {
    // RFC 9457 problem+json when the server sends it, otherwise a generic detail.
    let detail = `${response.status} ${response.statusText}`;
    try {
      const problem = (await response.json()) as { detail?: string };
      if (problem.detail) detail = problem.detail;
    } catch {
      // Non-JSON error body: keep the status line.
    }
    throw new ApiError(response.status, detail);
  }

  return (await response.json()) as T;
}

/**
 * Mission Control GETs answer `{data, evidence}`: the payload plus typed
 * provenance (live/stale/unavailable/unsupported/demo, absent, partial). The
 * envelope is preserved so panels can render evidence states rather than
 * mistaking a degraded or fixture answer for lakehouse truth.
 */
async function request<T>(path: string, signal?: AbortSignal): Promise<ReadEnvelope<T>> {
  return requestRaw<ReadEnvelope<T>>(path, signal, MISSION_BASE);
}

/**
 * CSRF marker the API requires on cookie-backed mutations: cross-site form
 * posts cannot set a custom header, and credentialed cross-origin fetches
 * fail the CORS preflight, so its presence proves same-origin intent.
 * Matches `CSRF_HEADER`/`CSRF_HEADER_VALUE` in `phlo_api/security_manifest.py`.
 */
const CSRF_HEADER = "x-phlo-request";
const CSRF_HEADER_VALUE = "observatory";

/**
 * Mutation entry point for Mission Control actions. Always same-origin with
 * cookie forwarding and the CSRF header; the server rejects unsafe requests
 * that present cookies without it.
 */
export async function mutate<T>(
  path: string,
  body: unknown,
  method: "POST" | "PUT" | "PATCH" | "DELETE" = "POST",
  base: string = MISSION_BASE,
): Promise<T> {
  const response = await fetch(`${base}${path}`, {
    method,
    credentials: "same-origin",
    headers: {
      accept: "application/json",
      "content-type": "application/json",
      [CSRF_HEADER]: CSRF_HEADER_VALUE,
    },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!response.ok) {
    let detail = `${response.status} ${response.statusText}`;
    try {
      const problem = (await response.json()) as { detail?: string | { error?: string } };
      if (typeof problem.detail === "string") {
        detail = problem.detail;
      } else if (problem.detail?.error) {
        detail = problem.detail.error;
      }
    } catch {
      // Non-JSON error body: keep the status line.
    }
    throw new ApiError(response.status, detail);
  }
  return (await response.json()) as T;
}

/**
 * Guarded run-action mutations against the substrate endpoints.
 *
 * Each call generates a fresh idempotency key so a repeated click is a new
 * intent, never a blind replay; the server binds the key to the payload
 * digest and answers a 409 for in-progress, unknown-outcome, or
 * payload-mismatched reuses. Callers surface `RunActionResult.status`
 * verbatim — a `pending` or `reconciled` outcome is not a failure.
 */
export const mutations = {
  retryRun: (runId: string, options?: { dryRun?: boolean }) =>
    mutate<RunActionResult>(
      `/runs/${encodeURIComponent(runId)}/retry`,
      {
        dry_run: options?.dryRun ?? false,
        idempotency_key: `ui-${crypto.randomUUID()}`,
      },
      "POST",
      SUBSTRATE_BASE,
    ),
  cancelRun: (runId: string, options?: { reason?: string }) =>
    mutate<RunActionResult>(
      `/runs/${encodeURIComponent(runId)}/cancel`,
      {
        reason: options?.reason ?? "",
        idempotency_key: `ui-${crypto.randomUUID()}`,
      },
      "POST",
      SUBSTRATE_BASE,
    ),
  /**
   * Materialization preview = the provider's dry-run validation (permission,
   * materializability, job resolution) with no run launched. Commit is a
   * separate call with its own idempotency key — a preview is never reused as
   * a commit.
   */
  previewMaterialize: (assetId: string, options?: { partitionKey?: string }) =>
    mutate<MaterializeResult>(
      `/assets/${encodeURIComponent(assetId)}/materialize`,
      {
        dry_run: true,
        partition_key: options?.partitionKey,
        idempotency_key: `ui-${crypto.randomUUID()}`,
      },
      "POST",
      SUBSTRATE_BASE,
    ),
  materializeAsset: (assetId: string, options?: { partitionKey?: string }) =>
    mutate<MaterializeResult>(
      `/assets/${encodeURIComponent(assetId)}/materialize`,
      {
        dry_run: false,
        partition_key: options?.partitionKey,
        idempotency_key: `ui-${crypto.randomUUID()}`,
      },
      "POST",
      SUBSTRATE_BASE,
    ),
  /**
   * Guarded service control through `/actions`: only declared managed
   * services are dispatched (start/stop/restart on in-stack services, add on
   * installable registry ones); the server rejects unmanaged targets.
   */
  serviceAction: (serviceId: string, action: "start" | "stop" | "restart" | "add") =>
    mutate<ObservatoryActionResult>(
      "/actions",
      {
        action_id: `service:${serviceId}:${action}`,
        idempotency_key: `ui-${crypto.randomUUID()}`,
      },
      "POST",
      SUBSTRATE_BASE,
    ),
  /**
   * Guarded promotion through the shared WAP authority. The preview digest
   * binds this confirmation to the exact gate/revision evidence the operator
   * reviewed — the server re-evaluates it inside the promotion lock, so a
   * moved target or a competing sensor win resolves truthfully instead of
   * executing a second promotion.
   */
  promoteCandidate: (candidateId: string, previewDigest: string | undefined) =>
    mutate<PromotionResult>(
      `/releases/candidates/${encodeURIComponent(candidateId)}/promotion`,
      {
        preview_digest: previewDigest ?? null,
        idempotency_key: `ui-${crypto.randomUUID()}`,
      },
    ),
  /**
   * Overview rail service visibility: `shown` names the catalog services the
   * rail may display (setup tasks may be included explicitly); `null` clears
   * the recorded configuration and returns the rail to its default view.
   */
  updateServiceVisibility: (shown: Array<string> | null) =>
    mutate<ReadEnvelope<MissionServiceVisibility>>(
      "/overview/rail/service-visibility",
      { shown },
      "PUT",
    ),
};

/**
 * Active-page polling for live surfaces: the page refetches every 15s while
 * visible, TanStack's focus awareness pauses it for hidden tabs, and each
 * consecutive fetch failure doubles the interval up to a 2-minute ceiling.
 * This is real polling — `staleTime` alone never refetches an idle query.
 */
const ACTIVE_POLL_MS = 15_000;
const POLL_CEILING_MS = 120_000;

export function missionPoll(query: {
  state: { fetchFailureCount: number };
}): number | false {
  const failures = query.state.fetchFailureCount;
  if (failures <= 0) return ACTIVE_POLL_MS;
  return Math.min(ACTIVE_POLL_MS * 2 ** Math.min(failures, 3), POLL_CEILING_MS);
}

/** Query keys are namespaced by domain so invalidation stays precise. */
const keys = {
  context: ["mission", "context"] as const,
  alerts: ["mission", "alerts"] as const,
  environments: ["mission", "environments"] as const,
  attention: ["mission", "attention"] as const,
  execution: ["mission", "execution"] as const,
  overviewSummary: ["mission", "overview-summary"] as const,
  dataProducts: ["mission", "data-products"] as const,
  overviewRail: ["mission", "overview-rail"] as const,
  runDetail: (runId: string) => ["mission", "runs", runId, "detail"] as const,
  runLogs: (runId: string) => ["mission", "runs", runId, "logs"] as const,
  runStages: (runId: string) => ["mission", "runs", runId, "stages"] as const,
  runQuality: (runId: string) => ["mission", "runs", runId, "quality"] as const,
  runEvents: (runId: string) => ["mission", "runs", runId, "events"] as const,
  runTraces: (runId: string) => ["mission", "runs", runId, "traces"] as const,
  runArtifacts: (runId: string) => ["mission", "runs", runId, "artifacts"] as const,
  runConsumers: (runId: string) => ["mission", "runs", runId, "consumers"] as const,
  runConfiguration: (runId: string) => ["mission", "runs", runId, "configuration"] as const,
  assets: ["mission", "assets"] as const,
  runsList: (limit: number) => ["mission", "runs", "list", limit] as const,
  datasetsList: (search: string, limit: number) =>
    ["mission", "datasets", "list", search, limit] as const,
  datasetDetail: (datasetId: string) => ["mission", "datasets", datasetId] as const,
  datasetGovernance: (datasetId: string) =>
    ["mission", "datasets", datasetId, "governance"] as const,
  releaseSummary: ["mission", "releases", "summary"] as const,
  releaseCandidates: ["mission", "releases", "candidates"] as const,
  releaseCandidate: (candidateId: string) =>
    ["mission", "releases", "candidates", candidateId] as const,
  promotionPreview: (candidateId: string) =>
    ["mission", "releases", "candidates", candidateId, "preview"] as const,
  completedReleases: ["mission", "releases", "completed"] as const,
  platformSummary: ["mission", "platform", "summary"] as const,
  platformServices: ["mission", "platform", "services"] as const,
  serviceDiagnostics: (serviceId: string) => ["mission", "platform", "services", serviceId] as const,
  serviceProbe: (serviceId: string) => ["substrate", "services", serviceId, "probe"] as const,
  backupCoverage: ["mission", "platform", "backup"] as const,
  maintenance: ["mission", "platform", "maintenance"] as const,
  governanceSummary: ["mission", "governance", "summary"] as const,
  publicationPlan: (datasetId: string) => ["mission", "governance", "plan", datasetId] as const,
  publicationReviews: ["mission", "governance", "publication-reviews"] as const,
  accessDrift: ["mission", "governance", "access-drift"] as const,
  ownershipGaps: ["mission", "governance", "ownership-gaps"] as const,
  auditEvents: ["mission", "governance", "audit"] as const,
  settingsSummary: ["mission", "settings", "summary"] as const,
  providerConnections: ["mission", "settings", "providers"] as const,
  providerImpact: (providerId: string) => ["mission", "settings", "providers", providerId] as const,
  notificationRules: ["mission", "settings", "notifications"] as const,
  workspaceMembers: ["mission", "settings", "members"] as const,
  workspaceDefaults: ["mission", "settings", "defaults"] as const,
};

/**
 * Query options for every Mission Control read model. `staleTime` matches the
 * footer's 15s refresh promise; the shell refetches on window focus.
 */
export const queries = {
  context: () =>
    queryOptions({
      queryKey: keys.context,
      queryFn: ({ signal }) => request<MissionContext>("/context", signal),
    }),
  alerts: () =>
    queryOptions({ queryKey: keys.alerts, queryFn: ({ signal }) => request<Array<MissionAlert>>("/overview/alerts", signal) }),
  environments: () =>
    queryOptions({
      queryKey: keys.environments,
      queryFn: ({ signal }) => request<Array<MissionEnvironment>>("/overview/environments", signal),
    }),
  attention: () =>
    queryOptions({
      queryKey: keys.attention,
      queryFn: ({ signal }) => request<Array<MissionAttentionItem>>("/overview/attention", signal),
    }),
  execution: () =>
    queryOptions({
      queryKey: keys.execution,
      queryFn: ({ signal }) => request<Array<MissionExecutionRow>>("/overview/execution", signal),
    }),

  overviewSummary: () =>
    queryOptions({
      queryKey: keys.overviewSummary,
      queryFn: ({ signal }) => request<Array<SummaryMetricRow>>("/overview/summary", signal),
    }),
  dataProducts: () =>
    queryOptions({
      queryKey: keys.dataProducts,
      queryFn: ({ signal }) => request<Array<MissionDataProduct>>("/overview/data-products", signal),
    }),
  overviewRail: () =>
    queryOptions({
      queryKey: keys.overviewRail,
      queryFn: ({ signal }) => request<MissionOverviewRail>("/overview/rail", signal),
    }),

  runDetail: (runId: string) =>
    queryOptions({
      queryKey: keys.runDetail(runId),
      queryFn: ({ signal }) => request<MissionRunDetail>(`/runs/${encodeURIComponent(runId)}`, signal),
    }),
  /**
   * Bounded, cursor-paginated retained log lines. Cursor resumption means a
   * reconnecting client continues from the last retained row — no duplicates,
   * no skipped events.
   */
  runLogs: (runId: string, limit = 100) =>
    infiniteQueryOptions({
      queryKey: keys.runLogs(runId),
      queryFn: async ({ signal, pageParam }) => {
        const params = new URLSearchParams({ limit: String(limit) });
        if (pageParam) params.set("cursor", pageParam);
        const envelope = await request<RunLogPage>(
          `/runs/${encodeURIComponent(runId)}/logs?${params}`,
          signal,
        );
        return envelope.data ?? { items: [], next_cursor: null, total: null };
      },
      initialPageParam: null as string | null,
      getNextPageParam: (last) => last.next_cursor ?? undefined,
    }),

  runStages: (runId: string) =>
    queryOptions({
      queryKey: keys.runStages(runId),
      queryFn: ({ signal }) => request<Array<RunStageView>>(`/runs/${encodeURIComponent(runId)}/stages`, signal),
    }),
  runQuality: (runId: string) =>
    queryOptions({
      queryKey: keys.runQuality(runId),
      queryFn: ({ signal }) => request<RunQualityReport>(`/runs/${encodeURIComponent(runId)}/quality`, signal),
    }),
  /**
   * Bounded, cursor-paginated recorded events for the run.
   */
  runEvents: (runId: string, limit = 50) =>
    infiniteQueryOptions({
      queryKey: keys.runEvents(runId),
      queryFn: async ({ signal, pageParam }) => {
        const params = new URLSearchParams({ limit: String(limit) });
        if (pageParam) params.set("cursor", pageParam);
        const envelope = await request<RunEventPage>(
          `/runs/${encodeURIComponent(runId)}/events?${params}`,
          signal,
        );
        return envelope.data ?? { items: [], next_cursor: null, total: null };
      },
      initialPageParam: null as string | null,
      getNextPageParam: (last) => last.next_cursor ?? undefined,
    }),
  runTraces: (runId: string) =>
    queryOptions({
      queryKey: keys.runTraces(runId),
      queryFn: ({ signal }) => request<Array<RunSpan>>(`/runs/${encodeURIComponent(runId)}/traces`, signal),
    }),
  runArtifacts: (runId: string) =>
    queryOptions({
      queryKey: keys.runArtifacts(runId),
      queryFn: ({ signal }) => request<Array<RunArtifact>>(`/runs/${encodeURIComponent(runId)}/artifacts`, signal),
    }),
  runConsumers: (runId: string) =>
    queryOptions({
      queryKey: keys.runConsumers(runId),
      queryFn: ({ signal }) => request<Array<RunConsumer>>(`/runs/${encodeURIComponent(runId)}/consumers`, signal),
    }),
  runConfiguration: (runId: string) =>
    queryOptions({
      queryKey: keys.runConfiguration(runId),
      queryFn: ({ signal }) => request<Array<RunConfigurationRow>>(`/runs/${encodeURIComponent(runId)}/configuration`, signal),
    }),

  assets: () =>
    queryOptions({
      queryKey: keys.assets,
      queryFn: async ({ signal }) => {
        // Substrate endpoint — not a mission read model, no evidence envelope.
        const payload = await requestRaw<
          { items: Array<ObservatoryAsset> } | Array<ObservatoryAsset>
        >("/assets", signal, SUBSTRATE_BASE);
        return Array.isArray(payload) ? payload : payload.items;
      },
    }),

  /**
   * Bounded, cursor-paginated run list from the same orchestrator population
   * the overview counters use — drilldown and summary can never disagree.
   */
  runsList: (limit = 50) =>
    infiniteQueryOptions({
      queryKey: keys.runsList(limit),
      queryFn: async ({ signal, pageParam }) => {
        const params = new URLSearchParams({ limit: String(limit) });
        if (pageParam) params.set("cursor", pageParam);
        const envelope = await request<MissionRunList>(`/runs?${params}`, signal);
        return envelope.data ?? { items: [], next_cursor: null };
      },
      initialPageParam: null as string | null,
      getNextPageParam: (last) => last.next_cursor ?? undefined,
    }),
  /**
   * Bounded, cursor-paginated substrate dataset collection (server-side `q`
   * filter applies before pagination). Substrate endpoints answer a plain
   * `{items, next_cursor}` envelope — no mission evidence wrapper.
   */
  datasetsList: (search = "", limit = 100) =>
    infiniteQueryOptions({
      queryKey: keys.datasetsList(search, limit),
      queryFn: ({ signal, pageParam }) => {
        const params = new URLSearchParams({ limit: String(limit) });
        const needle = search.trim();
        if (needle) params.set("q", needle);
        if (pageParam) params.set("cursor", pageParam);
        return requestRaw<ObservatoryListPage<ObservatoryDataset>>(
          `/datasets?${params}`,
          signal,
          SUBSTRATE_BASE,
        );
      },
      initialPageParam: null as string | null,
      getNextPageParam: (last) => last.next_cursor ?? undefined,
    }),
  datasetDetail: (datasetId: string) =>
    queryOptions({
      queryKey: keys.datasetDetail(datasetId),
      queryFn: ({ signal }) =>
        request<MissionDatasetDetail>(`/datasets/${encodeURIComponent(datasetId)}`, signal),
    }),

  datasetGovernance: (datasetId: string) =>
    queryOptions({
      queryKey: keys.datasetGovernance(datasetId),
      queryFn: ({ signal }) =>
        request<DatasetGovernance>(`/datasets/${encodeURIComponent(datasetId)}/governance`, signal),
    }),

  releaseSummary: () =>
    queryOptions({
      queryKey: keys.releaseSummary,
      queryFn: ({ signal }) => request<Array<ReleaseSummaryMetric>>("/releases/summary", signal),
    }),
  releaseCandidates: () =>
    queryOptions({
      queryKey: keys.releaseCandidates,
      queryFn: ({ signal }) => request<Array<ReleaseCandidate>>("/releases/candidates", signal),
    }),
  releaseCandidate: (candidateId: string) =>
    queryOptions({
      queryKey: keys.releaseCandidate(candidateId),
      queryFn: ({ signal }) =>
        request<ReleaseCandidateDetail>(`/releases/candidates/${encodeURIComponent(candidateId)}`, signal),
    }),
  /**
   * Read-only promotion-gate evaluation. Re-fetched on demand (revisions can
   * move between preview and confirm) — no polling: the operator re-runs the
   * preview to see the current verdicts.
   */
  promotionPreview: (candidateId: string) =>
    queryOptions({
      queryKey: keys.promotionPreview(candidateId),
      queryFn: ({ signal }) =>
        request<PromotionPreview>(
          `/releases/candidates/${encodeURIComponent(candidateId)}/preview`,
          signal,
        ),
    }),
  completedReleases: () =>
    queryOptions({
      queryKey: keys.completedReleases,
      queryFn: ({ signal }) => request<Array<CompletedRelease>>("/releases/completed", signal),
    }),

  platformSummary: () =>
    queryOptions({
      queryKey: keys.platformSummary,
      queryFn: ({ signal }) => request<Array<PlatformSummaryMetric>>("/platform/summary", signal),
    }),
  platformServices: () =>
    queryOptions({
      queryKey: keys.platformServices,
      queryFn: ({ signal }) => request<Array<PlatformService>>("/platform/services", signal),
    }),
  serviceDiagnostics: (serviceId: string) =>
    queryOptions({
      queryKey: keys.serviceDiagnostics(serviceId),
      queryFn: ({ signal }) =>
        request<ServiceDiagnostics>(`/platform/services/${encodeURIComponent(serviceId)}`, signal),
    }),
  backupCoverage: () =>
    queryOptions({
      queryKey: keys.backupCoverage,
      queryFn: ({ signal }) => request<Array<CoverageRow>>("/platform/backup", signal),
    }),
  maintenance: () =>
    queryOptions({
      queryKey: keys.maintenance,
      queryFn: ({ signal }) => request<Array<CoverageRow>>("/platform/maintenance", signal),
    }),

  governanceSummary: () =>
    queryOptions({
      queryKey: keys.governanceSummary,
      queryFn: ({ signal }) => request<Array<SummaryMetricRow>>("/governance/summary", signal),
    }),
  publicationPlan: (datasetId: string) =>
    queryOptions({
      queryKey: keys.publicationPlan(datasetId),
      queryFn: ({ signal }) =>
        request<PublicationPlan>(`/governance/publication-plan/${encodeURIComponent(datasetId)}`, signal),
    }),

  publicationReviews: () =>
    queryOptions({
      queryKey: keys.publicationReviews,
      queryFn: ({ signal }) => request<Array<PublicationReview>>("/governance/publication-reviews", signal),
    }),
  accessDrift: () =>
    queryOptions({
      queryKey: keys.accessDrift,
      queryFn: ({ signal }) => request<Array<AccessDrift>>("/governance/access-drift", signal),
    }),
  ownershipGaps: () =>
    queryOptions({
      queryKey: keys.ownershipGaps,
      queryFn: ({ signal }) => request<Array<OwnershipGap>>("/governance/ownership-gaps", signal),
    }),
  auditEvents: () =>
    queryOptions({
      queryKey: keys.auditEvents,
      queryFn: ({ signal }) => request<Array<AuditEvent>>("/governance/audit", signal),
    }),

  settingsSummary: () =>
    queryOptions({
      queryKey: keys.settingsSummary,
      queryFn: ({ signal }) => request<Array<SummaryMetricRow>>("/settings/summary", signal),
    }),
  providerConnections: () =>
    queryOptions({
      queryKey: keys.providerConnections,
      queryFn: ({ signal }) => request<Array<ProviderConnection>>("/settings/providers", signal),
    }),
  providerImpact: (providerId: string) =>
    queryOptions({
      queryKey: keys.providerImpact(providerId),
      queryFn: ({ signal }) =>
        request<ProviderImpact>(`/settings/providers/${encodeURIComponent(providerId)}/impact`, signal),
    }),
  notificationRules: () =>
    queryOptions({
      queryKey: keys.notificationRules,
      queryFn: ({ signal }) => request<Array<NotificationRule>>("/settings/notifications", signal),
    }),
  workspaceMembers: () =>
    queryOptions({
      queryKey: keys.workspaceMembers,
      queryFn: ({ signal }) => request<Array<WorkspaceMember>>("/settings/members", signal),
    }),
  workspaceDefaults: () =>
    queryOptions({
      queryKey: keys.workspaceDefaults,
      queryFn: ({ signal }) => request<Array<WorkspaceDefault>>("/settings/defaults", signal),
    }),

  /**
   * Provider-backed probe of one declared managed service: runtime and
   * readiness from the live container state, declared dependencies, dependents
   * (workflows that break if it restarts), and the enabled guarded actions.
   * Substrate endpoint — plain payload, not an evidence envelope.
   */
  serviceProbe: (serviceId: string) =>
    queryOptions({
      queryKey: keys.serviceProbe(serviceId),
      queryFn: ({ signal }) =>
        requestRaw<ObservatoryServiceDetail>(
          `/services/${encodeURIComponent(serviceId)}/probe`,
          signal,
          SUBSTRATE_BASE,
        ),
      refetchInterval: ACTIVE_POLL_MS,
    }),
};
