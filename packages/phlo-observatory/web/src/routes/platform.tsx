/**
 * Platform route: service readiness, backup coverage, maintenance and provider diagnostics.
 */
import { createFileRoute } from "@tanstack/react-router";
import { AlertCircle } from "lucide-react";

import type {Metric} from "@/components/metric-strip";
import {  MetricStrip } from "@/components/metric-strip";
import { ExampleDataChip, PageHeader } from "@/components/page-header";
import { PropertyList } from "@/components/property-list";
import { InlineLink, SectionHeader } from "@/components/section-header";
import { Button } from "@/components/ui/button";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import {
  backupCoverage,
  dependencyPath,
  lokiDetail,
  maintenanceRows,
  platformMetrics,
  platformServices,
} from "@/data/demo";
import { cn } from "@/lib/utils";

const METRICS: Array<Metric> = platformMetrics;

const READINESS_TONE: Record<string, string> = {
  Ready: "text-success",
  "Not ready": "text-warning",
};

/** Row-level warning that telemetry is degraded but evidence is intact. */
function TelemetryWarning() {
  return (
    <div className="flex items-center gap-2.5 rounded-[7px] border border-warning/25 bg-warning-surface px-3 py-2.75">
      <AlertCircle className="size-4.5 shrink-0 text-warning" strokeWidth={1.6} />
      <div className="flex min-w-0 flex-1 flex-col gap-1">
        <span className="text-xs leading-4 font-semibold">
          Loki is running, but log queries are unavailable
        </span>
        <span className="text-[11px] leading-3.5 text-muted-foreground">
          Readiness probe timed out at 09:35 UTC · Durable run evidence remains available.
        </span>
      </div>
      <InlineLink className="shrink-0">Inspect dependency path</InlineLink>
    </div>
  );
}

/** Every enabled service with runtime, readiness and probe outcome. */
function EnabledServices() {
  return (
    <section className="flex flex-col gap-2.5">
      <SectionHeader
        title="Enabled services"
        meta="12 services · Unready first · Last probe 09:35 UTC"
      />
      <div className="overflow-clip rounded-[7px] border border-border">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead className="w-35">Service</TableHead>
              <TableHead>Role</TableHead>
              <TableHead className="w-25">Runtime</TableHead>
              <TableHead className="w-25">Readiness</TableHead>
              <TableHead className="w-28.75">Probe</TableHead>
              <TableHead className="w-13.75 text-right" />
            </TableRow>
          </TableHeader>
          <TableBody>
            {platformServices.map((service) => (
              <TableRow key={service.name} className={cn(service.attention && "bg-warning-surface")}>
                <TableCell className="font-medium">{service.name}</TableCell>
                <TableCell className="text-muted-foreground">{service.role}</TableCell>
                <TableCell>{service.runtime}</TableCell>
                <TableCell className={READINESS_TONE[service.readiness]}>
                  {service.readiness}
                </TableCell>
                <TableCell className="text-muted-foreground">{service.probe}</TableCell>
                <TableCell className="text-right text-accent-foreground">
                  {service.action}
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </div>
    </section>
  );
}

function CoverageColumn({
  title,
  meta,
  rows,
  action,
}: {
  title: string;
  meta: string;
  rows: Array<{ name: string; outcome: string }>;
  action: string;
}) {
  return (
    <div className="flex min-w-0 flex-1 flex-col gap-2.25">
      <SectionHeader title={title} meta={meta} />
      <div className="flex flex-col">
        {rows.map((row, index) => (
          <div
            key={row.name}
            className={cn(
              "flex h-6.25 items-center justify-between border-b border-border/60 text-[11px] leading-3.5",
              index === rows.length - 1 && "border-b-0",
            )}
          >
            <span>{row.name}</span>
            <span className="text-success">{row.outcome}</span>
          </div>
        ))}
      </div>
      <InlineLink>{action}</InlineLink>
    </div>
  );
}

/** Degraded-service detail rail for the selected service. */
function LokiRail() {
  return (
    <aside className="flex w-75 shrink-0 flex-col gap-5 border-l border-border pl-4.5">
      <section className="flex flex-col gap-2.5">
        <SectionHeader title="Loki" action={<span className="text-[11px] text-warning">Not ready</span>} />
        <p className="text-[11px] leading-4 text-muted-foreground">
          Container is running. /ready timed out after 5 seconds; log queries also failed.
        </p>
        <PropertyList rows={lokiDetail} divided={false} />
        <div className="flex gap-2">
          <Button variant="outline" size="sm">
            Inspect container logs
          </Button>
          <Button variant="outline" size="sm">
            Probe details
          </Button>
        </div>
      </section>

      <section className="flex flex-col gap-2.5">
        <h2 className="font-display text-[15px] leading-4.5 font-semibold">Dependency path</h2>
        <div className="flex flex-col gap-2">
          {dependencyPath.map((edge) => (
            <div key={edge.name} className="flex h-6.5 items-center justify-between text-[11px]">
              <span>{edge.name}</span>
              <span className={edge.tone}>{edge.outcome}</span>
            </div>
          ))}
        </div>
        <p className="text-[11px] leading-4 text-muted-foreground">
          Collector readiness does not confirm log delivery. Check exporter retries and backend
          ingestion.
        </p>
      </section>

      <section className="flex flex-col gap-2.25">
        <h2 className="font-display text-[15px] leading-4.5 font-semibold">Installed capabilities</h2>
        <p className="text-xs leading-4.5">Compatibility checks: 16 / 16 passed</p>
        <p className="text-[11px] leading-4 text-muted-foreground">
          Installed compatibility is separate from declared support and deployment readiness.
        </p>
        <div className="flex justify-between text-[11px]">
          <span>Support channel</span>
          <span className="text-warning">Alpha</span>
        </div>
        <div className="flex justify-between text-[11px]">
          <span>Production readiness</span>
          <span className="text-warning">Not certified</span>
        </div>
        <InlineLink>View readiness findings</InlineLink>
      </section>
    </aside>
  );
}

function PlatformPage() {
  return (
    <div className="flex flex-col">
      <div className="px-6">
        <PageHeader
          title="Platform"
          titleAccessory={<ExampleDataChip />}
          description="Service readiness, operational coverage, and recovery evidence in one place."
          actions={
            <>
              <Button variant="outline">View configuration</Button>
              <Button>Run diagnostics</Button>
            </>
          }
        />
      </div>

      <div className="px-6 pb-4">
        <MetricStrip metrics={METRICS} dividers={false} />
      </div>

      <Tabs defaultValue="services" className="gap-0">
        <div className="border-b border-border px-6">
          <TabsList className="border-b-0">
            <TabsTrigger value="services">Services · 12</TabsTrigger>
            <TabsTrigger value="packages">Packages</TabsTrigger>
            <TabsTrigger value="maintenance">Maintenance</TabsTrigger>
            <TabsTrigger value="backup">Backup &amp; restore</TabsTrigger>
            <TabsTrigger value="readiness">Readiness</TabsTrigger>
          </TabsList>
        </div>

        <TabsContent value="services">
          <div className="flex flex-col gap-4.5 px-6 pt-4.5 pb-5">
            <TelemetryWarning />
            <div className="flex gap-5.5">
              <div className="flex min-w-0 flex-1 flex-col gap-4.5">
                <EnabledServices />
                <div className="flex gap-5.5">
                  <CoverageColumn
                    title="Backup coverage"
                    meta="03:00 UTC"
                    rows={backupCoverage}
                    action="Inspect backup manifest"
                  />
                  <CoverageColumn
                    title="Maintenance & recovery"
                    meta=""
                    rows={maintenanceRows}
                    action="View recovery evidence"
                  />
                </div>
              </div>
              <LokiRail />
            </div>
          </div>
        </TabsContent>

        <TabsContent value="packages">
          <div className="flex gap-5.5 px-6 pt-4.5 pb-5">
            <div className="min-w-0 flex-1">
              <SectionHeader title="Packages" meta="16 installed · 4 not enabled" />
              <PropertyList
                rows={platformServices.map((service) => ({
                  label: service.name,
                  value: `${service.role} · ${service.readiness}`,
                }))}
              />
            </div>
            <LokiRail />
          </div>
        </TabsContent>

        <TabsContent value="maintenance">
          <div className="flex gap-5.5 px-6 pt-4.5 pb-5">
            <div className="min-w-0 flex-1">
              <SectionHeader title="Maintenance & recovery" meta="Last run 03:00 UTC" />
              <PropertyList rows={maintenanceRows.map((row) => ({ label: row.name, value: row.outcome }))} />
            </div>
            <LokiRail />
          </div>
        </TabsContent>

        <TabsContent value="backup">
          <div className="flex gap-5.5 px-6 pt-4.5 pb-5">
            <div className="min-w-0 flex-1">
              <SectionHeader title="Backup coverage" meta="03:00 UTC · 4/4 contributors" />
              <PropertyList rows={backupCoverage.map((row) => ({ label: row.name, value: row.outcome }))} />
            </div>
            <LokiRail />
          </div>
        </TabsContent>

        <TabsContent value="readiness">
          <div className="flex gap-5.5 px-6 pt-4.5 pb-5">
            <div className="min-w-0 flex-1">
              <SectionHeader title="Readiness" meta="11 of 12 ready" />
              <PropertyList
                rows={platformServices.map((service) => ({
                  label: service.name,
                  value: `${service.readiness} · ${service.probe}`,
                }))}
              />
            </div>
            <LokiRail />
          </div>
        </TabsContent>
      </Tabs>
    </div>
  );
}

export const Route = createFileRoute("/platform")({ component: PlatformPage });
