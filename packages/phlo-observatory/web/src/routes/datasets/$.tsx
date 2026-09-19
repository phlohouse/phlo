/**
 * Dataset detail route: `/datasets/$` resolves the provider's canonical id —
 * including multi-segment asset keys — straight from the URL splat.
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
import { PageHeader, ReadStateChip } from "@/components/layout/page-header";
import { PageTabs, TabsContent } from "@/components/layout/page-tabs";
import { InlineLink, Section } from "@/components/layout/section-header";
import { DatasetOwnershipRail } from "@/components/sections/dataset/dataset-ownership-rail";
import { DatasetRunsTable } from "@/components/sections/dataset/dataset-runs-table";
import { LineageChain } from "@/components/sections/dataset/lineage-chain";
import { MaterializeAction } from "@/components/sections/dataset/materialize-action";
import { SchemaTable } from "@/components/sections/dataset/schema-table";

const TABS = [
  { value: "overview", label: "Overview" },
  { value: "schema", label: "Schema" },
  { value: "preview", label: "Preview" },
  { value: "quality", label: "Quality" },
  { value: "lineage", label: "Lineage" },
  { value: "runs", label: "Runs" },
  { value: "contract", label: "Contract & access" },
];

/**
 * Condense the full lineage graph into the short chain the Overview shows:
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

/** Honest preview caption: live select = current state, never "snapshot". */
function previewCaption(preview: {
  state: string;
  detail: string | null;
  ref: string | null;
  pinned: boolean;
  rows: Array<Array<string>>;
}): string {
  if (preview.state !== "ready") {
    return preview.detail ?? "Preview unavailable";
  }
  const basis = preview.pinned
    ? "Snapshot-pinned preview"
    : `Current contents${preview.ref ? ` · ${preview.ref}` : ""} · not snapshot-pinned`;
  return `${basis} · ${preview.rows.length} rows`;
}

function DatasetDetailPage() {
  const navigate = useNavigate();
  // `_splat` carries the full canonical id — multi-segment keys included.
  const { _splat } = Route.useParams();
  const datasetId = _splat ?? "";
  const dataset = useQuery({ ...queries.datasetDetail(datasetId), enabled: datasetId !== "" });
  const openRun = (runId: string) =>
    navigate({ to: "/runs/$runId", params: { runId } });

  const data = dataset.data?.data ?? undefined;
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
      onViewContract={() =>
        navigate({ to: "/datasets/$", params: { _splat: data.id } })
      }
    />
  ) : null;

  const runsSection = data ? (
    <Section
      title="Recent runs"
      meta={data.runs_state === "ready" ? `${data.runs.length} recent` : "Unavailable"}
      action={<InlineLink onClick={() => navigate({ to: "/runs" })}>View all runs →</InlineLink>}
    >
      {data.runs_state !== "ready" ? (
        <p className="text-[11px] text-muted-foreground">
          The orchestrator could not return runs for this dataset.
        </p>
      ) : (
        <DatasetRunsTable
          rows={data.runs}
          onOpenRun={(row) => openRun(row.run_id)}
        />
      )}
    </Section>
  ) : null;

  return (
    <Page>
      <PageHeader
        title={data?.name ?? "Dataset"}
        titleAccessory={data ? <><StatusPill status={data.status} dot={false} /><ReadStateChip evidence={dataset.data?.evidence} /></> : <ReadStateChip evidence={dataset.data?.evidence} />}
        description={data?.summary}
        actions={
          data ? (
            <>
              <MaterializeAction
                assetId={data.id}
                disabledReason={
                  dataset.data?.evidence.status === "live" ||
                  dataset.data?.evidence.status === "stale"
                    ? null
                    : "Dataset evidence is unavailable — the materialization target cannot be confirmed."
                }
              />
            </>
          ) : undefined
        }
      />

      <PageBand>
        <MetricStrip metrics={metrics} />
      </PageBand>

      <PageTabs tabs={TABS} defaultValue="overview">
        <TabsContent value="overview">
          <PageContent rail={rail}>
            {data?.checks.some((check) => check.tone === "danger") ? (
              <Banner
                tone="danger"
                title="A quality check failed"
                detail={
                  data.checks.find((check) => check.tone === "danger")?.outcome ?? "Check failed"
                }
                action={
                  data.runs[0] ? (
                    <InlineLink onClick={() => openRun(data.runs[0]!.run_id)}>Inspect run</InlineLink>
                  ) : undefined
                }
              />
            ) : null}
            {data ? (
              <LineageChain
                nodes={summariseChain(data.lineage)}
                caption="Declared dependencies"
              />
            ) : null}
            {data ? (
              <Section title="Schema" meta={`${data.schema_fields.length} columns`}>
                <SchemaTable rows={data.schema_fields.slice(0, 6)} />
                <p className="text-[11px] leading-3.5 text-muted-foreground">
                  Showing {Math.min(6, data.schema_fields.length)} of {data.schema_fields.length} columns
                </p>
              </Section>
            ) : null}
            {runsSection}
          </PageContent>
        </TabsContent>

        <TabsContent value="schema">
          <PageContent rail={rail}>
            {data ? (
              <Section title="Schema" meta={`${data.schema_fields.length} columns`}>
                <SchemaTable rows={data.schema_fields} />
              </Section>
            ) : null}
          </PageContent>
        </TabsContent>

        <TabsContent value="preview">
          <PageContent rail={rail}>
            {data ? (
              <Section title="Preview" >
                {data.preview.state === "ready" ? (
                  <DataTable
                    columns={previewColumns}
                    rows={data.preview.rows}
                    rowKey={(row) => row[0] ?? ""}
                  />
                ) : null}
                <p className="text-[11px] text-muted-foreground">
                  {previewCaption(data.preview)}
                </p>
              </Section>
            ) : null}
          </PageContent>
        </TabsContent>

        <TabsContent value="quality">
          <PageContent rail={rail}>
            {data ? (
              <Section title="Quality" meta={`${data.checks.filter((check) => check.tone === "success").length} passed · ${data.checks.filter((check) => check.tone === "danger").length} failed`}>
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
              <Section title="Lineage" meta={`${data.lineage.length} nodes`}>
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
            {runsSection}
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
                  data.access.length ? (
                    <PropertyList
                      rows={data.access.map((grant) => ({
                        label: grant.principal,
                        value: grant.scope,
                      }))}
                    />
                  ) : (
                    <p className="text-[11px] text-muted-foreground">
                      No access grants are reported for this dataset.
                    </p>
                  )
                ) : null}
              </DetailSection>
            </PageStack>
          </PageContent>
        </TabsContent>
      </PageTabs>
    </Page>
  );
}

export const Route = createFileRoute("/datasets/$")({
  component: DatasetDetailPage,
});
