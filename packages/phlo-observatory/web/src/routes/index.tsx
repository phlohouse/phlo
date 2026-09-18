/**
 * Overview route.
 *
 * Reads the Overview read models from the API and passes them to presentational
 * sections. Reference implementation for wiring the remaining pages: the route
 * owns data fetching and routing, sections own layout only.
 */
import { createFileRoute, useNavigate } from "@tanstack/react-router";
import { useQuery } from "@tanstack/react-query";
import { Plus } from "lucide-react";

import type {Metric} from "@/components/data/metric-strip";
import { queries } from "@/api/mission-control";
import {  MetricStrip } from "@/components/data/metric-strip";
import { Page, PageBand, PageContent } from "@/components/layout/page";
import { ExampleDataChip, PageHeader } from "@/components/layout/page-header";
import { AttentionList } from "@/components/sections/overview/attention-list";
import { DataProductsTable } from "@/components/sections/overview/data-products-table";
import { ExecutionTable } from "@/components/sections/overview/execution-table";
import { HealthRail } from "@/components/sections/overview/health-rail";
import { Button } from "@/components/ui/button";

const TONE_CLASS: Record<string, string | undefined> = {
  success: "text-success",
  warning: "text-warning",
  danger: "text-destructive",
  accent: "text-accent-foreground",
  muted: undefined,
};

function OverviewPage() {
  const navigate = useNavigate();

  const summary = useQuery(queries.overviewSummary());
  const attention = useQuery(queries.attention());
  const execution = useQuery(queries.execution());
  const dataProducts = useQuery(queries.dataProducts());
  const rail = useQuery(queries.overviewRail());

  const metrics: Array<Metric> = (summary.data ?? []).map((metric) => ({
    label: metric.label,
    value: metric.value,
    hint: metric.hint,
    tone: TONE_CLASS[metric.tone],
  }));

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
        <MetricStrip metrics={metrics} />
      </PageBand>

      <PageContent
        rail={
          rail.data ? (
            <HealthRail
              rail={rail.data}
              onOpen={{
                services: () => navigate({ to: "/platform" }),
                releases: () => navigate({ to: "/releases" }),
                governance: () => navigate({ to: "/governance" }),
                recovery: () => navigate({ to: "/platform" }),
              }}
            />
          ) : null
        }
      >
        {attention.data ? (
          <AttentionList
            items={attention.data}
            onOpen={(item) => navigate({ to: item.target })}
          />
        ) : null}
        {execution.data ? (
          <ExecutionTable
            rows={execution.data}
            onOpen={() => navigate({ to: "/runs/orders-daily" })}
          />
        ) : null}
        {dataProducts.data ? (
          <DataProductsTable
            rows={dataProducts.data}
            onOpen={() => navigate({ to: "/datasets/orders" })}
            onViewAll={() => navigate({ to: "/datasets/orders" })}
          />
        ) : null}
      </PageContent>
    </Page>
  );
}

export const Route = createFileRoute("/")({ component: OverviewPage });
