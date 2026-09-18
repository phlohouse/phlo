/**
 * Dataset detail route.
 *
 * Composes shared layout and data components only — the section implementations
 * live under `components/sections/dataset`.
 */
import { createFileRoute, useNavigate } from "@tanstack/react-router";

import type {DataColumn} from "@/components/data/data-table";
import type {Metric} from "@/components/data/metric-strip";
import {  DataTable } from "@/components/data/data-table";
import {  MetricStrip } from "@/components/data/metric-strip";
import { PropertyList } from "@/components/data/property-list";
import { StatusPill } from "@/components/data/status-pill";
import { Banner } from "@/components/feedback/banner";
import { DetailSection } from "@/components/layout/detail-rail";
import { Page, PageBand, PageContent, PageStack } from "@/components/layout/page";
import { ExampleDataChip, PageHeader } from "@/components/layout/page-header";
import { PageTabs, TabsContent } from "@/components/layout/page-tabs";
import { InlineLink, Section } from "@/components/layout/section-header";
import { DatasetOwnershipRail } from "@/components/sections/dataset/dataset-ownership-rail";
import { DatasetRunsTable } from "@/components/sections/dataset/dataset-runs-table";
import { LineageChain } from "@/components/sections/dataset/lineage-chain";
import { SchemaTable } from "@/components/sections/dataset/schema-table";
import { Button } from "@/components/ui/button";
import {
  datasetAccess,
  datasetChecks,
  datasetContract,
  datasetLineage,
  datasetMeta,
  datasetOwnership,
  datasetPreview,
  datasetRuns,
  datasetSchema,
} from "@/data/demo";

const METRICS: Array<Metric> = datasetMeta.metrics;

const TABS = [
  { value: "overview", label: "Overview" },
  { value: "schema", label: "Schema · 8" },
  { value: "preview", label: "Preview" },
  { value: "quality", label: "Quality" },
  { value: "lineage", label: "Lineage" },
  { value: "runs", label: "Runs" },
  { value: "contract", label: "Contract & access" },
];

const CONSUMERS = [
  { name: "Revenue dashboard", role: "Superset · Analytics" },
  { name: "Orders API", role: "PostgREST · Commerce" },
  { name: "Finance reconciliation", role: "dbt · Finance" },
];

const LINEAGE_NODES = [
  { name: "postgres.orders", role: "Postgres · Source" },
  { name: "stg_orders", role: "dbt · Model" },
  { name: "marts.orders", role: "This dataset", current: true },
  { name: "3 consumers", role: "Analytics · API · dbt" },
];

const PREVIEW_COLUMNS: Array<DataColumn<Array<string>>> = [
  { key: "order_id", header: "order_id", cell: (row) => row[0] ?? "" },
  { key: "customer_id", header: "customer_id", cell: (row) => row[1] ?? "" },
  { key: "created_at", header: "created_at", cell: (row) => row[2] ?? "" },
  { key: "status", header: "status", cell: (row) => row[3] ?? "" },
  { key: "order_total", header: "order_total", cell: (row) => row[4] ?? "" },
];

function DatasetDetailPage() {
  const navigate = useNavigate();
  const openRun = () => navigate({ to: "/runs/orders-daily" });

  const rail = (
    <DatasetOwnershipRail
      ownership={datasetOwnership}
      consumers={CONSUMERS}
      access={datasetAccess}
      onViewContract={() => navigate({ to: "/datasets/orders" })}
    />
  );

  return (
    <Page>
      <PageHeader
        title={datasetMeta.name}
        titleAccessory={<StatusPill status={datasetMeta.status} dot={false} />}
        description={datasetMeta.summary}
        actions={
          <>
            <Button variant="outline">Preview materialization</Button>
            <Button>Explore data</Button>
          </>
        }
      />

      <PageBand>
        <MetricStrip metrics={METRICS} />
      </PageBand>

      <PageTabs tabs={TABS} defaultValue="overview">
        <TabsContent value="overview">
          <PageContent rail={rail}>
            <Banner
              tone="danger"
              title="Next delivery blocked by a quality check"
              detail="Run r7e42b · 42 duplicate order IDs · Released data is unchanged."
              action={<InlineLink onClick={openRun}>Inspect run</InlineLink>}
            />
            <LineageChain
              nodes={LINEAGE_NODES}
              caption="Declared dependencies · Consumers read released snapshot 938105"
              onViewAll={() => navigate({ to: "/datasets/orders" })}
            />
            <Section
              title="Schema"
              action={<InlineLink>8 columns · View schema →</InlineLink>}
            >
              <SchemaTable rows={datasetSchema.slice(0, 6)} />
              <p className="text-[11px] leading-3.5 text-muted-foreground">
                Showing 6 of 8 columns · Schema version 3 · No pending changes
              </p>
            </Section>
            <Section title="Recent runs" action={<InlineLink>View all runs →</InlineLink>}>
              <DatasetRunsTable rows={datasetRuns} onOpenRun={openRun} />
            </Section>
          </PageContent>
        </TabsContent>

        <TabsContent value="schema">
          <PageContent rail={rail}>
            <Section title="Schema" meta="8 columns · version 3">
              <SchemaTable rows={datasetSchema} />
            </Section>
          </PageContent>
        </TabsContent>

        <TabsContent value="preview">
          <PageContent rail={rail}>
            <Section title="Preview" meta="released snapshot 938105">
              <DataTable
                columns={PREVIEW_COLUMNS}
                rows={datasetPreview}
                rowKey={(row) => row[0] ?? ""}
              />
              <p className="text-[11px] text-muted-foreground">
                Sample · 6 of 1,187,320 rows · snapshot 938105
              </p>
            </Section>
          </PageContent>
        </TabsContent>

        <TabsContent value="quality">
          <PageContent rail={rail}>
            <Section title="Quality" meta="11 passed · 1 failed">
              <div className="flex flex-col">
                {datasetChecks.map((check, index) => (
                  <div
                    key={check.name}
                    className={`flex items-center justify-between border-t border-border py-2.5 text-xs ${index === 0 ? "border-t-0" : ""}`}
                  >
                    <span className="font-medium">{check.name}</span>
                    <span className={check.tone}>{check.outcome}</span>
                  </div>
                ))}
              </div>
            </Section>
          </PageContent>
        </TabsContent>

        <TabsContent value="lineage">
          <PageContent rail={rail}>
            <Section title="Lineage" meta="depth 2">
              <div className="flex flex-col">
                {datasetLineage.map((node, index) => (
                  <div
                    key={node.name}
                    className={`flex items-center justify-between border-t border-border py-2.5 text-xs ${index === 0 ? "border-t-0" : ""}`}
                  >
                    <span className="font-semibold">{node.name}</span>
                    <span className="text-muted-foreground">{node.role}</span>
                    <span className="text-[11px] text-accent-foreground">{node.direction}</span>
                  </div>
                ))}
              </div>
            </Section>
          </PageContent>
        </TabsContent>

        <TabsContent value="runs">
          <PageContent rail={rail}>
            <Section title="Runs" meta="3 recent">
              <DatasetRunsTable rows={datasetRuns} onOpenRun={openRun} />
            </Section>
          </PageContent>
        </TabsContent>

        <TabsContent value="contract">
          <PageContent rail={rail}>
            <PageStack>
              <DetailSection title="Contract" meta="v3 · Approved">
                <PropertyList rows={datasetContract} />
              </DetailSection>
              <DetailSection title="Access">
                <PropertyList rows={datasetAccess} />
              </DetailSection>
            </PageStack>
          </PageContent>
        </TabsContent>
      </PageTabs>
    </Page>
  );
}

export const Route = createFileRoute("/datasets/orders")({
  component: DatasetDetailPage,
});
