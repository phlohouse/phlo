/**
 * Overview route: summary band, attention list, execution and data-product
 * tables beside the health rail.
 */
import { createFileRoute, useNavigate } from "@tanstack/react-router";
import { Plus } from "lucide-react";

import type {Metric} from "@/components/data/metric-strip";
import {  MetricStrip } from "@/components/data/metric-strip";
import { Page, PageBand, PageContent } from "@/components/layout/page";
import { ExampleDataChip, PageHeader } from "@/components/layout/page-header";
import { AttentionList } from "@/components/sections/overview/attention-list";
import { DataProductsTable } from "@/components/sections/overview/data-products-table";
import { ExecutionTable } from "@/components/sections/overview/execution-table";
import { HealthRail } from "@/components/sections/overview/health-rail";
import { Button } from "@/components/ui/button";
import {
  activeExecution,
  attentionItems,
  dataProducts,
  governanceOverview,
  recoveryOverview,
  releaseQueue,
  services,
  summaryMetrics,
} from "@/data/demo";

const METRICS: Array<Metric> = summaryMetrics.map((metric) => ({
  label: metric.label,
  value: metric.value,
  hint: metric.hint,
}));

function OverviewPage() {
  const navigate = useNavigate();
  return (
    <Page>
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
      <PageBand>
        <MetricStrip metrics={METRICS} />
      </PageBand>
      <PageContent
        rail={
          <HealthRail
            services={services.slice(0, 6)}
            totalServices={services.length}
            releaseQueue={releaseQueue}
            governance={governanceOverview}
            recovery={recoveryOverview}
            readyCount={services.filter((service) => service.state === "Ready").length}
            totalReady={services.length}
            onOpen={{
              services: () => navigate({ to: "/platform" }),
              releases: () => navigate({ to: "/releases" }),
              governance: () => navigate({ to: "/governance" }),
              recovery: () => navigate({ to: "/platform" }),
            }}
          />
        }
      >
        <AttentionList
          items={attentionItems}
          onOpen={(item) => navigate({ to: item.to })}
        />
        <ExecutionTable
          rows={activeExecution}
          onOpen={(row) => navigate({ to: row.to })}
        />
        <DataProductsTable
          rows={dataProducts}
          onOpen={(row) => navigate({ to: row.to })}
          onViewAll={() => navigate({ to: "/datasets/orders" })}
        />
      </PageContent>
    </Page>
  );
}

export const Route = createFileRoute("/")({ component: OverviewPage });
