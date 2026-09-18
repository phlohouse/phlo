/**
 * Index component.
 */
import { createFileRoute, useNavigate } from "@tanstack/react-router";
import { Plus } from "lucide-react";

import { AttentionList } from "@/components/overview/attention-list";
import { DataProductsTable } from "@/components/overview/data-products-table";
import { ExecutionTable } from "@/components/overview/execution-table";
import { HealthRail } from "@/components/overview/health-rail";
import { MetricStrip, type Metric } from "@/components/metric-strip";
import { ExampleDataChip, PageHeader } from "@/components/page-header";
import { Button } from "@/components/ui/button";
import { summaryMetrics } from "@/data/demo";

const METRICS: Metric[] = summaryMetrics.map((metric) => ({
  label: metric.label,
  value: metric.value,
  hint: metric.hint,
}));

function OverviewPage() {
  const navigate = useNavigate();
  return (
    <>
      <div className="px-6">
        <PageHeader
          title="Overview"
          titleAccessory={<ExampleDataChip />}
          actions={
            <>
              <Button variant="outline">Last 24 hours</Button>
              <Button onClick={() => navigate({ to: "/runs/orders-daily" })}>
                <Plus />
                Create workflow
              </Button>
            </>
          }
        />
      </div>

      <div className="px-6 pb-4.5">
        <MetricStrip metrics={METRICS} />
      </div>

      <div className="flex gap-5.5 px-6 pt-4.5 pb-5">
        <div className="flex min-w-0 flex-1 flex-col gap-4.5">
          <AttentionList />
          <ExecutionTable />
          <DataProductsTable />
        </div>
        <HealthRail />
      </div>
    </>
  );
}

export const Route = createFileRoute("/")({ component: OverviewPage });
