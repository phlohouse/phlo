/**
 * Orders Daily component.
 */
import { createFileRoute } from "@tanstack/react-router";
import { Play } from "lucide-react";

import type {Metric} from "@/components/metric-strip";
import {  MetricStrip } from "@/components/metric-strip";
import { ExampleDataChip, PageHeader } from "@/components/page-header";
import {
  ExecutionTimeline,
  KeyEvents,
  QualityFailure,
} from "@/components/run/run-evidence";
import {
  ArtifactList,
  ConfigurationPanel,
  LogViewer,
  RunDetailsRail,
  TraceTable,
} from "@/components/run/run-rail";
import { Button } from "@/components/ui/button";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { runConfig, runMeta } from "@/data/demo";

const METRICS: Array<Metric> = runMeta.metrics;

function RunDetailPage() {
  return (
    <div className="flex flex-col">
      <div className="px-6">
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
      </div>

      <div className="px-6 pb-4">
        <MetricStrip metrics={METRICS} />
      </div>

      <Tabs defaultValue="evidence" className="gap-0">
        <div className="border-b border-border px-6">
          <TabsList className="border-b-0">
            <TabsTrigger value="evidence">Evidence</TabsTrigger>
            <TabsTrigger value="logs">Logs · 128</TabsTrigger>
            <TabsTrigger value="traces">Traces</TabsTrigger>
            <TabsTrigger value="artifacts">Artifacts · 6</TabsTrigger>
            <TabsTrigger value="configuration">Configuration</TabsTrigger>
          </TabsList>
        </div>

        <TabsContent value="evidence">
          <div className="flex gap-5.5 px-6 pt-4.5 pb-5">
            <div className="flex min-w-0 flex-1 flex-col gap-4.5">
              <ExecutionTimeline />
              <QualityFailure />
              <KeyEvents />
            </div>
            <RunDetailsRail />
          </div>
        </TabsContent>

        <TabsContent value="logs">
          <div className="flex gap-5.5 px-6 pt-4.5 pb-5">
            <div className="flex min-w-0 flex-1 flex-col gap-4.5">
              <LogViewer />
            </div>
            <RunDetailsRail />
          </div>
        </TabsContent>

        <TabsContent value="traces">
          <div className="flex gap-5.5 px-6 pt-4.5 pb-5">
            <div className="flex min-w-0 flex-1 flex-col gap-4.5">
              <TraceTable />
            </div>
            <RunDetailsRail />
          </div>
        </TabsContent>

        <TabsContent value="artifacts">
          <div className="flex gap-5.5 px-6 pt-4.5 pb-5">
            <div className="flex min-w-0 flex-1 flex-col gap-4.5">
              <ArtifactList />
            </div>
            <RunDetailsRail />
          </div>
        </TabsContent>

        <TabsContent value="configuration">
          <div className="flex gap-5.5 px-6 pt-4.5 pb-5">
            <div className="flex min-w-0 flex-1 flex-col gap-4.5">
              <ConfigurationPanel rows={runConfig} />
            </div>
            <RunDetailsRail />
          </div>
        </TabsContent>
      </Tabs>
    </div>
  );
}

export const Route = createFileRoute("/runs/orders-daily")({
  component: RunDetailPage,
});
