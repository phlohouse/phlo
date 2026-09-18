/**
 * Run detail route: evidence, logs, traces, artifacts and configuration tabs.
 * Section implementations live under `components/sections/run`.
 */
import { createFileRoute } from "@tanstack/react-router";
import { useQuery } from "@tanstack/react-query";
import { Play } from "lucide-react";

import type {Metric} from "@/components/data/metric-strip";
import { queries } from "@/api/mission-control";

import {  MetricStrip } from "@/components/data/metric-strip";
import { Page, PageBand } from "@/components/layout/page";
import { ExampleDataChip, PageHeader } from "@/components/layout/page-header";
import { PageTabs, TabsContent } from "@/components/layout/page-tabs";
import { RunContent } from "@/components/sections/run/run-content";
import { ExecutionTimeline, KeyEvents, QualityFailure } from "@/components/sections/run/run-evidence";
import {
  ArtifactList,
  ConfigurationPanel,
  LogViewer,
  TraceTable,
} from "@/components/sections/run/run-rail";
import { Button } from "@/components/ui/button";

const TABS = [
  { value: "evidence", label: "Evidence" },
  { value: "logs", label: "Logs · 128" },
  { value: "traces", label: "Traces" },
  { value: "artifacts", label: "Artifacts · 6" },
  { value: "configuration", label: "Configuration" },
];

const RUN_ID = "r7e42b";

function RunDetailPage() {
  const detail = useQuery(queries.runDetail(RUN_ID));
  const stages = useQuery(queries.runStages(RUN_ID));
  const quality = useQuery(queries.runQuality(RUN_ID));
  const events = useQuery(queries.runEvents(RUN_ID));
  const traces = useQuery(queries.runTraces(RUN_ID));
  const artifacts = useQuery(queries.runArtifacts(RUN_ID));
  const logs = useQuery(queries.runLogs(RUN_ID));

  const run = detail.data;
  const metrics: Array<Metric> = (run?.metrics ?? []).map((metric) => ({
    label: metric.label,
    value: metric.value,
    hint: metric.hint,
    tone: metric.tone === "muted" ? undefined : `text-${metric.tone === "danger" ? "destructive" : metric.tone}`,
  }));

  return (
    <Page>
      <PageHeader
        title={run?.workflow ?? "Run"}
        titleAccessory={
          <>
            <span className="rounded-[4px] bg-destructive-soft px-1.5 py-1 text-[11px] leading-3.5 text-destructive">
              {run?.status ?? "Loading"}
            </span>
            <ExampleDataChip />
          </>
        }
        description={run?.summary}
        actions={
          <>
            <Button variant="outline">Compare with last success</Button>
            <Button>
              <Play />
              Preview retry
            </Button>
          </>
        }
      />

      <PageBand>
        <MetricStrip metrics={metrics} />
      </PageBand>

      <PageTabs tabs={TABS} defaultValue="evidence">
        <TabsContent value="evidence">
          <RunContent run={run}>
            {stages.data ? <ExecutionTimeline stages={stages.data} /> : null}
            {quality.data ? <QualityFailure failure={quality.data} /> : null}
            {events.data ? <KeyEvents events={events.data} /> : null}
          </RunContent>
        </TabsContent>

        <TabsContent value="logs">
          <RunContent run={run}>
            {logs.data ? <LogViewer lines={logs.data} /> : null}
          </RunContent>
        </TabsContent>

        <TabsContent value="traces">
          <RunContent run={run}>
            {traces.data ? <TraceTable spans={traces.data} /> : null}
          </RunContent>
        </TabsContent>

        <TabsContent value="artifacts">
          <RunContent run={run}>
            {artifacts.data ? <ArtifactList artifacts={artifacts.data} /> : null}
          </RunContent>
        </TabsContent>

        <TabsContent value="configuration">
          <RunContent run={run}>
            {run ? <ConfigurationPanel rows={run.details} /> : null}
          </RunContent>
        </TabsContent>
      </PageTabs>
    </Page>
  );
}

export const Route = createFileRoute("/runs/orders-daily")({
  component: RunDetailPage,
});
