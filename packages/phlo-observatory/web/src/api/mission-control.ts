/**
 * Mission Control API client.
 *
 * Wraps the `/api/observatory/mission` read models in typed query options so
 * routes call `useQuery(queries.alerts())` and never touch fetch directly.
 *
 * The Vite dev server proxies `/api/observatory` to phlo-api, so relative URLs
 * work in every environment without a configured base.
 */
import { queryOptions } from "@tanstack/react-query";

import type {
  AccessDrift,
  AuditEvent,
  CompletedRelease,
  CoverageRow,
  DatasetGovernance,
  MissionAlert,
  MissionAttentionItem,
  MissionDataProduct,
  MissionDatasetDetail,
  MissionEnvironment,
  MissionExecutionRow,
  MissionOverviewRail,
  MissionRunDetail,
  MissionRunLogLine,
  NotificationRule,
  ObservatoryAsset,
  OwnershipGap,
  PlatformService,
  PlatformSummaryMetric,
  ProviderConnection,
  ProviderImpact,
  PublicationPlan,
  PublicationReview,
  ReleaseCandidate,
  ReleaseCandidateDetail,
  ReleaseSummaryMetric,
  RunArtifact,
  RunConfigurationRow,
  RunConsumer,
  RunEvent,
  RunQualityFailure,
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

async function request<T>(
  path: string,
  signal?: AbortSignal,
  base: string = MISSION_BASE,
): Promise<T> {
  const response = await fetch(`${base}${path}`, {
    headers: { accept: "application/json" },
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

/** Query keys are namespaced by domain so invalidation stays precise. */
const keys = {
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
  datasetDetail: (datasetId: string) => ["mission", "datasets", datasetId] as const,
  datasetGovernance: (datasetId: string) =>
    ["mission", "datasets", datasetId, "governance"] as const,
  releaseSummary: ["mission", "releases", "summary"] as const,
  releaseCandidates: ["mission", "releases", "candidates"] as const,
  releaseCandidate: (candidateId: string) =>
    ["mission", "releases", "candidates", candidateId] as const,
  completedReleases: ["mission", "releases", "completed"] as const,
  platformSummary: ["mission", "platform", "summary"] as const,
  platformServices: ["mission", "platform", "services"] as const,
  serviceDiagnostics: (serviceId: string) => ["mission", "platform", "services", serviceId] as const,
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
  runLogs: (runId: string) =>
    queryOptions({
      queryKey: keys.runLogs(runId),
      queryFn: ({ signal }) =>
        request<Array<MissionRunLogLine>>(`/runs/${encodeURIComponent(runId)}/logs`, signal),
    }),

  runStages: (runId: string) =>
    queryOptions({
      queryKey: keys.runStages(runId),
      queryFn: ({ signal }) => request<Array<RunStageView>>(`/runs/${encodeURIComponent(runId)}/stages`, signal),
    }),
  runQuality: (runId: string) =>
    queryOptions({
      queryKey: keys.runQuality(runId),
      queryFn: ({ signal }) => request<RunQualityFailure>(`/runs/${encodeURIComponent(runId)}/quality`, signal),
    }),
  runEvents: (runId: string) =>
    queryOptions({
      queryKey: keys.runEvents(runId),
      queryFn: ({ signal }) => request<Array<RunEvent>>(`/runs/${encodeURIComponent(runId)}/events`, signal),
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
        const payload = await request<
          { items: Array<ObservatoryAsset> } | Array<ObservatoryAsset>
        >("/assets", signal, SUBSTRATE_BASE);
        return Array.isArray(payload) ? payload : payload.items;
      },
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
};
