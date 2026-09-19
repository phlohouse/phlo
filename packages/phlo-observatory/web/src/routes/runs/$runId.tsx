/**
 * Run detail route: `/runs/$runId` resolves the orchestrator's run id
 * straight from the URL param — evidence, logs, traces, artifacts and
 * configuration tabs. Section implementations live under `components/sections/run`.
 */
import { createFileRoute } from "@tanstack/react-router";
import { useInfiniteQuery, useQuery } from "@tanstack/react-query";
import { parseAsString, useQueryState } from "nuqs";

import type {Metric} from "@/components/data/metric-strip";
import { queries } from "@/api/mission-control";

import {  MetricStrip } from "@/components/data/metric-strip";
import { Banner } from "@/components/feedback/banner";
import { Page, PageBand, PageContent } from "@/components/layout/page";
import { PageHeader, ReadStateChip } from "@/components/layout/page-header";
import { PageTabs, TabsContent } from "@/components/layout/page-tabs";
import { RunActions } from "@/components/sections/run/run-actions";
import { RunContent } from "@/components/sections/run/run-content";
import {
  ExecutionTimeline,
  KeyEvents,
  QualityReport,
  RunIdentityBlock,
} from "@/components/sections/run/run-evidence";
import {
  ArtifactList,
  ConfigurationPanel,
  LogViewer,
  TraceTable,
} from "@/components/sections/run/run-rail";

const TABS = (logCount: number | null, artifactCount: number | null) => [
  { value: "evidence", label: "Evidence" },
  { value: "logs", label: logCount === null ? "Logs" : `Logs · ${logCount}` },
  { value: "traces", label: "Traces" },
  { value: "artifacts", label: artifactCount === null ? "Artifacts" : `Artifacts · ${artifactCount}` },
  { value: "configuration", label: "Configuration" },
];

function RunDetailPage() {
  const { runId } = Route.useParams();
  const [tab, setTab] = useQueryState(
    "tab",
    parseAsString.withDefault("evidence").withOptions({ clearOnDefault: true }),
  );
  const detail = useQuery(queries.runDetail(runId));
  const stages = useQuery(queries.runStages(runId));
  const quality = useQuery(queries.runQuality(runId));
  const events = useInfiniteQuery(queries.runEvents(runId));
  const traces = useQuery(queries.runTraces(runId));
  const artifacts = useQuery(queries.runArtifacts(runId));
  const consumers = useQuery(queries.runConsumers(runId));
  const configuration = useQuery(queries.runConfiguration(runId));
  const logs = useInfiniteQuery(queries.runLogs(runId));

  const run = detail.data?.data ?? undefined;
  const metrics: Array<Metric> = (run?.metrics ?? []).map((metric) => ({
    label: metric.label,
    value: metric.value,
    hint: metric.hint,
    tone: metric.tone === "muted" ? undefined : `text-${metric.tone === "danger" ? "destructive" : metric.tone}`,
  }));

  const eventItems = events.data?.pages.flatMap((page) => page.items) ?? [];
  const logItems = logs.data?.pages.flatMap((page) => page.items) ?? [];
  const logTotal = logs.data?.pages[0]?.total ?? null;
  const consumerItems = consumers.data?.data ?? [];
  const artifactItems = artifacts.data?.data ?? [];

  if (detail.isError) {
    return (
      <Page>
        <PageHeader title="Run" />
        <PageContent>
          <Banner
            tone="danger"
            title="Run not found"
            detail={`The orchestrator does not report run ${runId} — it may have been cleaned up, or the id is wrong.`}
          />
        </PageContent>
      </Page>
    );
  }

  return (
    <Page>
      <PageHeader
        title={run?.workflow ?? "Run"}
        titleAccessory={
          <>
            <span className="rounded-[4px] bg-destructive-soft px-1.5 py-1 text-[11px] leading-3.5 text-destructive">
              {run?.status ?? "Loading"}
            </span>
            <ReadStateChip evidence={detail.data?.evidence} />
          </>
        }
        description={run?.summary}
        actions={run ? <RunActions runId={run.run_id} status={run.status} /> : null}
      />

      <PageBand>
        <MetricStrip metrics={metrics} />
      </PageBand>

      {run?.failure ? (
        <PageContent>
          <Banner tone="danger" title="Run failed" detail={run.failure} />
        </PageContent>
      ) : null}

      <PageTabs
        tabs={TABS(logTotal, artifacts.data?.data ? artifactItems.length : null)}
        value={tab}
        onValueChange={(value) => void setTab(value)}
      >
        <TabsContent value="evidence">
          <RunContent run={run} consumers={consumerItems} artifacts={artifactItems}>
            {run?.identity ? <RunIdentityBlock identity={run.identity} /> : null}
            {stages.data?.data ? <ExecutionTimeline stages={stages.data.data} /> : null}
            {quality.data?.data ? <QualityReport report={quality.data.data} /> : null}
            {eventItems.length > 0 ? (
              <KeyEvents events={eventItems} onOpenLogs={() => void setTab("logs")} />
            ) : null}
          </RunContent>
        </TabsContent>

        <TabsContent value="logs">
          <RunContent run={run} consumers={consumerItems} artifacts={artifactItems}>
            <LogViewer
              lines={logItems}
              total={logTotal}
              hasMore={logs.hasNextPage}
              onLoadMore={() => void logs.fetchNextPage()}
            />
          </RunContent>
        </TabsContent>

        <TabsContent value="traces">
          <RunContent run={run} consumers={consumerItems} artifacts={artifactItems}>
            {traces.data?.data ? <TraceTable spans={traces.data.data} /> : null}
          </RunContent>
        </TabsContent>

        <TabsContent value="artifacts">
          <RunContent run={run} consumers={consumerItems} artifacts={artifactItems}>
            {artifacts.data?.data ? <ArtifactList artifacts={artifactItems} /> : null}
          </RunContent>
        </TabsContent>

        <TabsContent value="configuration">
          <RunContent run={run} consumers={consumerItems} artifacts={artifactItems}>
            {configuration.data?.data ? (
              <ConfigurationPanel rows={configuration.data.data} />
            ) : run ? (
              <ConfigurationPanel rows={run.details} />
            ) : null}
          </RunContent>
        </TabsContent>
      </PageTabs>
    </Page>
  );
}

export const Route = createFileRoute("/runs/$runId")({
  validateSearch: (search: Record<string, unknown>): { tab?: string } => ({
    tab: typeof search.tab === "string" && search.tab ? search.tab : undefined,
  }),
  component: RunDetailPage,
});
