/**
 * Run detail route: evidence, logs, traces, artifacts and configuration tabs.
 * Section implementations live under `components/sections/run`.
 */
import { createFileRoute } from "@tanstack/react-router";
import { Play } from "lucide-react";

import type {Metric} from "@/components/data/metric-strip";
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
import { runConfig, runMeta } from "@/data/demo";

const METRICS: Array<Metric> = runMeta.metrics;

const TABS = [
  { value: "evidence", label: "Evidence" },
  { value: "logs", label: "Logs · 128" },
  { value: "traces", label: "Traces" },
  { value: "artifacts", label: "Artifacts · 6" },
  { value: "configuration", label: "Configuration" },
];

function RunDetailPage() {
  return (
    <Page>
      <PageHeader
        title={runMeta.workflow}
        titleAccessory={
          <>
            <span className="rounded-[4px] bg-destructive-soft px-1.5 py-1 text-[11px] leading-3.5 text-destructive">
              {runMeta.status}
            </span>
            <ExampleDataChip />
          </>
        }
        description={runMeta.summary}
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
        <MetricStrip metrics={METRICS} />
      </PageBand>

      <PageTabs tabs={TABS} defaultValue="evidence">
        <TabsContent value="evidence">
          <RunContent>
            <ExecutionTimeline />
            <QualityFailure />
            <KeyEvents />
          </RunContent>
        </TabsContent>

        <TabsContent value="logs">
          <RunContent>
            <LogViewer />
          </RunContent>
        </TabsContent>

        <TabsContent value="traces">
          <RunContent>
            <TraceTable />
          </RunContent>
        </TabsContent>

        <TabsContent value="artifacts">
          <RunContent>
            <ArtifactList />
          </RunContent>
        </TabsContent>

        <TabsContent value="configuration">
          <RunContent>
            <ConfigurationPanel rows={runConfig} />
          </RunContent>
        </TabsContent>
      </PageTabs>
    </Page>
  );
}

export const Route = createFileRoute("/runs/orders-daily")({
  component: RunDetailPage,
});
