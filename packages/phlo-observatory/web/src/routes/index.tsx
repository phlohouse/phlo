/**
 * Overview route.
 *
 * Reads the Overview read models from the API and passes them to presentational
 * sections. Reference implementation for wiring the remaining pages: the route
 * owns data fetching and routing, sections own layout only.
 */
import { createFileRoute, useNavigate } from "@tanstack/react-router";
import { useInfiniteQuery, useQuery } from "@tanstack/react-query";

import type {Metric} from "@/components/data/metric-strip";
import { queries } from "@/api/mission-control";
import {  MetricStrip } from "@/components/data/metric-strip";
import { Page, PageBand, PageContent } from "@/components/layout/page";
import { PageHeader, ReadStateChip } from "@/components/layout/page-header";
import { AttentionList } from "@/components/sections/overview/attention-list";
import { DataProductsTable } from "@/components/sections/overview/data-products-table";
import { ExecutionTable } from "@/components/sections/overview/execution-table";
import { HealthRail } from "@/components/sections/overview/health-rail";

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
  const datasets = useInfiniteQuery(queries.datasetsList());

  const metrics: Array<Metric> = (summary.data?.data ?? []).map((metric) => ({
    label: metric.label,
    value: metric.value,
    hint: metric.hint,
    tone: TONE_CLASS[metric.tone],
  }));

  const executionRows = execution.data?.data ?? null;
  const activeStages = new Set(["Started", "Running", "Queued"]);
  const activeCount = (executionRows ?? []).filter((row) => activeStages.has(row.stage)).length;
  const recentCount = (executionRows?.length ?? 0) - activeCount;
  const executionMeta =
    executionRows === null
      ? undefined
      : [
          activeCount ? `${activeCount} active` : null,
          recentCount ? `${recentCount} recent` : null,
          executionRows.length === 0 ? "No runs recorded" : null,
        ]
          .filter(Boolean)
          .join(" · ");

  const datasetTotal = datasets.data?.pages.reduce(
    (count, page) => count + page.items.length,
    0,
  );

  return (
    <Page>
      <PageHeader
        title="Overview"
        titleAccessory={<ReadStateChip evidence={summary.data?.evidence} />}
      />

      <PageBand>
        <MetricStrip metrics={metrics} />
      </PageBand>

      <PageContent
        rail={
          rail.data?.data ? (
            <HealthRail
              rail={rail.data.data}
              onOpen={{
                services: () => navigate({ to: "/platform" }),
                service: (name) =>
                  navigate({ to: "/platform", search: { service: name } }),
                releases: () => navigate({ to: "/releases" }),
                governance: () => navigate({ to: "/governance" }),
                recovery: () => navigate({ to: "/platform" }),
              }}
            />
          ) : null
        }
      >
        {attention.data?.data ? (
          <AttentionList
            items={attention.data.data}
            onOpen={(item) => navigate({ to: item.target })}
          />
        ) : null}
        {executionRows ? (
          <ExecutionTable
            rows={executionRows}
            meta={executionMeta}
            onOpen={(row) => navigate({ to: "/runs/$runId", params: { runId: row.run_id } })}
          />
        ) : null}
        {dataProducts.data?.data ? (
          <DataProductsTable
            rows={dataProducts.data.data}
            total={datasetTotal}
            onOpen={(row) => navigate({ to: row.target })}
            onViewAll={() => navigate({ to: "/datasets" })}
          />
        ) : null}
      </PageContent>
    </Page>
  );
}

export const Route = createFileRoute("/")({ component: OverviewPage });
