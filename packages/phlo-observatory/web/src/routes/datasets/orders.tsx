/**
 * Orders component.
 */
import { ArrowRight, ChevronRight } from "lucide-react";
import { useNavigate, createFileRoute } from "@tanstack/react-router";

import { PageHeader } from "@/components/page-header";
import { MetricStrip, type Metric } from "@/components/metric-strip";
import { PropertyList } from "@/components/property-list";
import { InlineLink, SectionHeader } from "@/components/section-header";
import { StatusPill } from "@/components/status-pill";
import { Button } from "@/components/ui/button";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
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
import { cn } from "@/lib/utils";

const METRICS: Metric[] = datasetMeta.metrics;

const LINEAGE_CHAIN = [
  { name: "postgres.orders", role: "Postgres · Source", current: false },
  { name: "stg_orders", role: "dbt · Model", current: false },
  { name: "marts.orders", role: "This dataset", current: true },
  { name: "3 consumers", role: "Analytics · API · dbt", current: false },
];

const PREVIEW_COLUMNS = ["order_id", "customer_id", "created_at", "status", "order_total"];

function outcomeTone(outcome: string) {
  if (/fail/i.test(outcome)) return "text-destructive";
  if (/succeed/i.test(outcome)) return "text-success";
  return "text-foreground";
}

/** Blocking-check banner linking through to the failing run. */
function BlockedBanner() {
  const navigate = useNavigate();
  return (
    <button
      type="button"
      onClick={() => navigate({ to: "/runs/orders-daily" })}
      className="flex cursor-pointer items-center gap-2.5 rounded-[7px] border border-border bg-destructive-soft px-3 py-2.5 text-left"
    >
      <span className="text-[15px] leading-none text-destructive">&#9432;</span>
      <span className="flex-1">
        <span className="block text-xs font-semibold">Next delivery blocked by a quality check</span>
        <span className="block text-[11px] leading-3.5 text-muted-foreground">
          Run r7e42b · 42 duplicate order IDs · Released data is unchanged.
        </span>
      </span>
      <span className="shrink-0 text-[11px] leading-3.5 text-accent-foreground">Inspect run</span>
    </button>
  );
}

/** Upstream/downstream chain with the current dataset highlighted. */
function LineageChain() {
  const navigate = useNavigate();
  return (
    <>
      <SectionHeader
        title="Lineage & consumers"
        action={<InlineLink onClick={() => navigate({ to: "/datasets/orders" })}>View full graph →</InlineLink>}
      />
      <div className="flex items-center gap-2.5">
        {LINEAGE_CHAIN.map((node, index) => (
          <div key={node.name} className="flex flex-1 items-center gap-2.5 last:flex-none">
            <div
              className={cn(
                "min-w-0 flex-1 rounded-[7px] border px-2.5 py-3",
                node.current ? "border-[#c6b8fa] bg-[#f4f1ff] dark:bg-secondary" : "border-border bg-subtle",
              )}
            >
              <div
                className={cn(
                  "truncate text-xs font-semibold",
                  node.current ? "text-accent-foreground" : "text-foreground",
                )}
              >
                {node.name}
              </div>
              <div className="truncate text-[11px] leading-3.5 text-muted-foreground">{node.role}</div>
            </div>
            {index < LINEAGE_CHAIN.length - 1 ? (
              <ArrowRight className="size-4.5 shrink-0 text-muted-foreground" />
            ) : null}
          </div>
        ))}
      </div>
      <p className="pt-2 text-[11px] leading-3.5 text-muted-foreground">
        Declared dependencies · Consumers read released snapshot 938105
      </p>
    </>
  );
}

function SchemaTable({ rows }: { rows: typeof datasetSchema }) {
  return (
    <div className="overflow-clip rounded-[7px] border border-border">
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead className="w-47.5">Field</TableHead>
            <TableHead className="w-40">Type</TableHead>
            <TableHead className="w-22.5">Nullable</TableHead>
            <TableHead>Role / validation</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {rows.map((row) => (
            <TableRow key={row.field}>
              <TableCell className="font-medium">{row.field}</TableCell>
              <TableCell>{row.type}</TableCell>
              <TableCell className="text-muted-foreground">{row.nullable}</TableCell>
              <TableCell>{row.role}</TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </div>
  );
}

function RecentRunsTable() {
  const navigate = useNavigate();
  return (
    <div className="overflow-clip rounded-[7px] border border-border">
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead className="w-37.5">Run</TableHead>
            <TableHead className="w-37.5">Finished (UTC)</TableHead>
            <TableHead>Outcome</TableHead>
            <TableHead className="w-37.5">Release</TableHead>
            <TableHead className="w-17.5 text-right">Duration</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {datasetRuns.map((run) => (
            <TableRow key={run.run} clickable onClick={() => navigate({ to: "/runs/orders-daily" })}>
              <TableCell className="text-accent-foreground">{run.run}</TableCell>
              <TableCell>{run.finished}</TableCell>
              <TableCell className={outcomeTone(run.outcome)}>{run.outcome}</TableCell>
              <TableCell>{run.release}</TableCell>
              <TableCell className="text-right">{run.duration}</TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </div>
  );
}

function OwnershipRail() {
  const navigate = useNavigate();
  return (
    <aside className="flex w-75 shrink-0 flex-col gap-4.5 border-l border-border pl-4.5">
      <section className="flex flex-col gap-2.75">
        <h2 className="font-display text-[15px] leading-4.5 font-semibold">Ownership & contract</h2>
        <PropertyList rows={datasetOwnership} />
      </section>
      <section className="flex flex-col gap-2.5">
        <SectionHeader title="Consumers" meta="3 downstream" />
        {LINEAGE_CHAIN.slice(3).length === 0 ? null : null}
        {[
          { name: "Revenue dashboard", role: "Superset · Analytics" },
          { name: "Orders API", role: "PostgREST · Commerce" },
          { name: "Finance reconciliation", role: "dbt · Finance" },
        ].map((consumer) => (
          <div key={consumer.name} className="flex items-center gap-2">
            <span className="flex flex-1 flex-col gap-0.75">
              <span className="text-xs font-medium">{consumer.name}</span>
              <span className="text-[10px] leading-3 text-muted-foreground">{consumer.role}</span>
            </span>
            <ChevronRight className="size-4.5 text-muted-foreground" />
          </div>
        ))}
      </section>
      <section className="flex flex-col gap-2.5">
        <h2 className="font-display text-[15px] leading-4.5 font-semibold">Delivery & access</h2>
        <PropertyList rows={datasetAccess} divided={false} />
        <InlineLink onClick={() => navigate({ to: "/datasets/orders" })}>
          View contract & access →
        </InlineLink>
      </section>
    </aside>
  );
}

function DatasetDetailPage() {
  return (
    <div className="flex flex-col">
      <div className="px-6">
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
      </div>

      <div className="px-6 pb-4">
        <MetricStrip metrics={METRICS} />
      </div>

      <Tabs defaultValue="overview" className="gap-0">
        <div className="border-b border-border px-6">
          <TabsList className="border-b-0">
            <TabsTrigger value="overview">Overview</TabsTrigger>
            <TabsTrigger value="schema">Schema · 8</TabsTrigger>
            <TabsTrigger value="preview">Preview</TabsTrigger>
            <TabsTrigger value="quality">Quality</TabsTrigger>
            <TabsTrigger value="lineage">Lineage</TabsTrigger>
            <TabsTrigger value="runs">Runs</TabsTrigger>
            <TabsTrigger value="contract">Contract & access</TabsTrigger>
          </TabsList>
        </div>

        <TabsContent value="overview">
          <div className="flex gap-5.5 px-6 pt-4.5 pb-5">
            <div className="flex min-w-0 flex-1 flex-col gap-4.5">
              <BlockedBanner />
              <LineageChain />
              <section className="flex flex-col gap-2.5">
                <SectionHeader
                  title="Schema"
                  action={<InlineLink>8 columns · View schema →</InlineLink>}
                />
                <SchemaTable rows={datasetSchema.slice(0, 6)} />
                <p className="text-[11px] leading-3.5 text-muted-foreground">
                  Showing 6 of 8 columns · Schema version 3 · No pending changes
                </p>
              </section>
              <section className="flex flex-col gap-2.5">
                <SectionHeader
                  title="Recent runs"
                  action={<InlineLink>View all runs →</InlineLink>}
                />
                <RecentRunsTable />
              </section>
            </div>
            <OwnershipRail />
          </div>
        </TabsContent>

        <TabsContent value="schema">
          <div className="flex gap-5.5 px-6 pt-4.5 pb-5">
            <div className="flex min-w-0 flex-1 flex-col gap-2.5">
              <SectionHeader title="Schema" meta="8 columns · version 3" />
              <SchemaTable rows={datasetSchema} />
            </div>
            <OwnershipRail />
          </div>
        </TabsContent>

        <TabsContent value="preview">
          <div className="flex gap-5.5 px-6 pt-4.5 pb-5">
            <div className="flex min-w-0 flex-1 flex-col gap-2.5">
              <SectionHeader title="Preview" meta="released snapshot 938105" />
              <div className="overflow-clip rounded-[7px] border border-border">
                <Table>
                  <TableHeader>
                    <TableRow>
                      {PREVIEW_COLUMNS.map((column) => (
                        <TableHead key={column}>{column}</TableHead>
                      ))}
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {datasetPreview.map((row) => (
                      <TableRow key={row[0]}>
                        {row.map((cell, index) => (
                          <TableCell key={index}>{cell}</TableCell>
                        ))}
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </div>
              <p className="text-[11px] text-muted-foreground">
                Sample · 6 of 1,187,320 rows · snapshot 938105
              </p>
            </div>
            <OwnershipRail />
          </div>
        </TabsContent>

        <TabsContent value="quality">
          <div className="flex gap-5.5 px-6 pt-4.5 pb-5">
            <div className="flex min-w-0 flex-1 flex-col">
              <SectionHeader title="Quality" meta="11 passed · 1 failed" />
              {datasetChecks.map((check, index) => (
                <div
                  key={check.name}
                  className={cn(
                    "flex items-center justify-between border-t border-border py-2.5 text-xs",
                    index === 0 && "border-t-0",
                  )}
                >
                  <span className="font-medium">{check.name}</span>
                  <span className={check.tone}>{check.outcome}</span>
                </div>
              ))}
            </div>
            <OwnershipRail />
          </div>
        </TabsContent>

        <TabsContent value="lineage">
          <div className="flex gap-5.5 px-6 pt-4.5 pb-5">
            <div className="flex min-w-0 flex-1 flex-col">
              <SectionHeader title="Lineage" meta="depth 2" />
              {datasetLineage.map((node, index) => (
                <div
                  key={node.name}
                  className={cn(
                    "flex items-center justify-between border-t border-border py-2.5 text-xs",
                    index === 0 && "border-t-0",
                  )}
                >
                  <span className="font-semibold">{node.name}</span>
                  <span className="text-muted-foreground">{node.role}</span>
                  <span className="text-[11px] text-accent-foreground">{node.direction}</span>
                </div>
              ))}
            </div>
            <OwnershipRail />
          </div>
        </TabsContent>

        <TabsContent value="runs">
          <div className="flex gap-5.5 px-6 pt-4.5 pb-5">
            <div className="flex min-w-0 flex-1 flex-col gap-2.5">
              <SectionHeader title="Runs" meta="3 recent" />
              <RecentRunsTable />
            </div>
            <OwnershipRail />
          </div>
        </TabsContent>

        <TabsContent value="contract">
          <div className="flex gap-5.5 px-6 pt-4.5 pb-5">
            <div className="flex min-w-0 flex-1 flex-col gap-4.5">
              <section className="flex flex-col gap-2.5">
                <SectionHeader title="Contract" meta="v3 · Approved" />
                <PropertyList rows={datasetContract} />
              </section>
              <section className="flex flex-col gap-2.5">
                <SectionHeader title="Access" />
                <PropertyList rows={datasetAccess} />
              </section>
            </div>
            <OwnershipRail />
          </div>
        </TabsContent>
      </Tabs>
    </div>
  );
}

export const Route = createFileRoute("/datasets/orders")({
  component: DatasetDetailPage,
});
