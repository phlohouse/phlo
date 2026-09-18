/**
 * Dataset detail route.
 *
 * Reads the full Dataset read model in one request (the page is tabbed with
 * small per-tab payloads, so a single call avoids a first-paint waterfall) and
 * passes it to presentational sections.
 */
import { createFileRoute, useNavigate } from "@tanstack/react-router";
import { useQuery } from "@tanstack/react-query";

import type {DataColumn} from "@/components/data/data-table";
import type {Metric} from "@/components/data/metric-strip";
import type { LineageNode } from "@/api/types";
import { queries } from "@/api/mission-control";
import {  DataTable } from "@/components/data/data-table";
import {  MetricStrip } from "@/components/data/metric-strip";
import { PropertyList } from "@/components/data/property-list";
import { StatusPill } from "@/components/data/status-pill";
import { Banner } from "@/components/feedback/banner";
import { DetailSection } from "@/components/layout/detail-rail";
import { Page, PageBand, PageContent, PageStack, SplitRow } from "@/components/layout/page";
import { ExampleDataChip, PageHeader } from "@/components/layout/page-header";
import { PageTabs, TabsContent } from "@/components/layout/page-tabs";
import { InlineLink, Section } from "@/components/layout/section-header";
import { DatasetOwnershipRail } from "@/components/sections/dataset/dataset-ownership-rail";
import { DatasetRunsTable } from "@/components/sections/dataset/dataset-runs-table";
import { LineageChain } from "@/components/sections/dataset/lineage-chain";
import { SchemaTable } from "@/components/sections/dataset/schema-table";
import { Button } from "@/components/ui/button";

const DATASET_ID = "marts.orders";

const TABS = [
  { value: "overview", label: "Overview" },
  { value: "schema", label: "Schema · 8" },
  { value: "preview", label: "Preview" },
  { value: "quality", label: "Quality" },
  { value: "lineage", label: "Lineage" },
  { value: "runs", label: "Runs" },
  { value: "contract", label: "Contract & access" },
];

/**
 * Condense the full lineage graph into the 4-node chain the Overview shows:
 * upstream sources, this dataset, then a single consumer count node.
 */
function summariseChain(nodes: Array<LineageNode>): Array<LineageNode> {
  const currentIndex = nodes.findIndex((node) => node.current);
  if (currentIndex === -1) return nodes;
  const upstream = nodes.slice(0, currentIndex + 1);
  const downstream = nodes.slice(currentIndex + 1);
  if (downstream.length === 0) return upstream;
  const roles = Array.from(new Set(downstream.map((node) => node.role.split(" · ")[0])));
  return [
    ...upstream,
    { name: `${downstream.length} consumers`, role: roles.join(" · "), current: false },
  ];
}

const TONE_CLASS: Record<string, string | undefined> = {
  success: "text-success",
  warning: "text-warning",
  danger: "text-destructive",
  accent: "text-accent-foreground",
  muted: undefined,
};

function DatasetDetailPage() {
  const navigate = useNavigate();
  const dataset = useQuery(queries.datasetDetail(DATASET_ID));
  const openRun = () => navigate({ to: "/runs/orders-daily" });

  const data = dataset.data;
  const metrics: Array<Metric> = (data?.metrics ?? []).map((metric) => ({
    label: metric.label,
    value: metric.value,
    hint: metric.hint,
    tone: TONE_CLASS[metric.tone],
  }));

  const previewColumns: Array<DataColumn<Array<string>>> = (data?.preview.columns ?? []).map(
    (column, index) => ({
      key: column,
      header: column,
      cell: (row) => row[index] ?? "",
    }),
  );

  const rail = data ? (
    <DatasetOwnershipRail
      ownership={data.ownership}
      access={data.access}
      onViewContract={() => navigate({ to: "/datasets/orders" })}
    />
  ) : null;

  return (
    <Page>
      <PageHeader
        title={data?.name ?? "Dataset"}
        titleAccessory={data ? <StatusPill status={data.status} dot={false} /> : null}
        description={data?.summary}
        actions={
          <>
            <Button variant="outline">Preview materialization</Button>
            <Button>Explore data</Button>
          </>
        }
      />

      <PageBand>
        <MetricStrip metrics={metrics} />
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
            {data ? (
              <LineageChain
                nodes={summariseChain(data.lineage)}
                caption="Declared dependencies · Consumers read released snapshot 938105"
                onViewAll={() => navigate({ to: "/datasets/orders" })}
              />
            ) : null}
            {data ? (
              <Section title="Schema" action={<InlineLink>8 columns · View schema →</InlineLink>}>
                <SchemaTable rows={data.schema_fields.slice(0, 6)} />
                <p className="text-[11px] leading-3.5 text-muted-foreground">
                  Showing 6 of {data.schema_fields.length} columns · Schema version 3 · No pending
                  changes
                </p>
              </Section>
            ) : null}
            {data ? (
              <Section title="Recent runs" action={<InlineLink>View all runs →</InlineLink>}>
                <DatasetRunsTable rows={data.runs} onOpenRun={openRun} />
              </Section>
            ) : null}
          </PageContent>
        </TabsContent>

        <TabsContent value="schema">
          <PageContent rail={rail}>
            {data ? (
              <Section title="Schema" meta={`${data.schema_fields.length} columns · version 3`}>
                <SchemaTable rows={data.schema_fields} />
              </Section>
            ) : null}
          </PageContent>
        </TabsContent>

        <TabsContent value="preview">
          <PageContent rail={rail}>
            {data ? (
              <Section title="Preview" meta="released snapshot 938105">
                <DataTable
                  columns={previewColumns}
                  rows={data.preview.rows}
                  rowKey={(row) => row[0] ?? ""}
                />
                <p className="text-[11px] text-muted-foreground">
                  Sample · {data.preview.rows.length} of 1,187,320 rows · snapshot 938105
                </p>
              </Section>
            ) : null}
          </PageContent>
        </TabsContent>

        <TabsContent value="quality">
          <PageContent rail={rail}>
            {data ? (
              <Section title="Quality" meta="11 passed · 1 failed">
                <div className="flex flex-col">
                  {data.checks.map((check, index) => (
                    <div
                      key={check.name}
                      className={`flex items-center justify-between border-t border-border py-2.5 text-xs ${index === 0 ? "border-t-0" : ""}`}
                    >
                      <span className="font-medium">{check.name}</span>
                      <span className={TONE_CLASS[check.tone]}>{check.outcome}</span>
                    </div>
                  ))}
                </div>
              </Section>
            ) : null}
          </PageContent>
        </TabsContent>

        <TabsContent value="lineage">
          <PageContent rail={rail}>
            {data ? (
              <Section title="Lineage" meta="depth 2">
                <div className="flex flex-col">
                  {data.lineage.map((node, index) => (
                    <div
                      key={node.name}
                      className={`flex items-center justify-between border-t border-border py-2.5 text-xs ${index === 0 ? "border-t-0" : ""}`}
                    >
                      <span className="font-semibold">{node.name}</span>
                      <span className="text-muted-foreground">{node.role}</span>
                      <span className="text-[11px] text-accent-foreground">
                        {node.current ? "Current" : "Related"}
                      </span>
                    </div>
                  ))}
                </div>
              </Section>
            ) : null}
          </PageContent>
        </TabsContent>

        <TabsContent value="runs">
          <PageContent rail={rail}>
            {data ? (
              <Section title="Runs" meta={`${data.runs.length} recent`}>
                <DatasetRunsTable rows={data.runs} onOpenRun={openRun} />
              </Section>
            ) : null}
          </PageContent>
        </TabsContent>

        <TabsContent value="contract">
          <PageContent rail={rail}>
            <PageStack>
              <SplitRow rail={rail}>
                {data ? (
                  <Section title="Contract" meta={data.ownership.contract_version ?? "—"}>
                    <PropertyList
                      rows={[
                        { label: "Owner", value: data.ownership.owner ?? "Unassigned" },
                        { label: "Domain", value: data.ownership.domain ?? "—" },
                        {
                          label: "Freshness target",
                          value: data.ownership.freshness_target ?? "—",
                        },
                        { label: "Retention", value: data.ownership.retention ?? "—" },
                        { label: "Classification", value: data.ownership.classification ?? "—" },
                      ]}
                    />
                  </Section>
                ) : null}
              </SplitRow>
              <DetailSection title="Access" divided>
                {data ? (
                  <PropertyList
                    rows={data.access.map((grant) => ({
                      label: grant.principal,
                      value: grant.scope,
                    }))}
                  />
                ) : null}
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
