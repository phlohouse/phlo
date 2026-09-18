/**
 * Platform route: service readiness, backup coverage and recovery evidence.
 * Section implementations live under `components/sections/platform`.
 */
import { createFileRoute } from "@tanstack/react-router";
import { useQuery } from "@tanstack/react-query";

import type {Metric} from "@/components/data/metric-strip";
import { queries } from "@/api/mission-control";

import {  MetricStrip } from "@/components/data/metric-strip";
import { PropertyList } from "@/components/data/property-list";
import { Banner } from "@/components/feedback/banner";
import { Page, PageBand, PageContent, SplitRow } from "@/components/layout/page";
import { ExampleDataChip, PageHeader } from "@/components/layout/page-header";
import { PageTabs, TabsContent } from "@/components/layout/page-tabs";
import { InlineLink, Section } from "@/components/layout/section-header";
import { ServiceDiagnosticRail } from "@/components/sections/platform/service-diagnostic-rail";
import { ServiceHealthTable } from "@/components/sections/platform/service-health-table";
import { EvidenceListPlain } from "@/components/data/evidence-list";
import { Button } from "@/components/ui/button";

const TABS = [
  { value: "services", label: "Services · 12" },
  { value: "packages", label: "Packages" },
  { value: "maintenance", label: "Maintenance" },
  { value: "backup", label: "Backup & restore" },
  { value: "readiness", label: "Readiness" },
];

const TONE_CLASS: Record<string, string | undefined> = {
  success: "text-success",
  warning: "text-warning",
  danger: "text-destructive",
  accent: "text-accent-foreground",
  muted: undefined,
};

function PlatformPage() {
  const summary = useQuery(queries.platformSummary());
  const services = useQuery(queries.platformServices());
  const diagnostics = useQuery(queries.serviceDiagnostics("loki"));
  const backup = useQuery(queries.backupCoverage());
  const maintenance = useQuery(queries.maintenance());

  const metrics: Array<Metric> = (summary.data ?? []).map((metric) => ({
    label: metric.label,
    value: metric.value,
    hint: metric.hint,
    tone: TONE_CLASS[metric.tone],
  }));

  const diag = diagnostics.data;
  const rail = diag ? (
    <ServiceDiagnosticRail
      serviceName={diag.name}
      state={diag.readiness_state}
      stateTone="text-warning"
      summary={diag.summary}
      facts={diag.facts}
      dependencies={diag.dependencies}
      dependencyNote={diag.dependency_note}
      capabilityChecks={diag.capability_checks}
      capabilities={diag.capabilities}
    />
  ) : null;

  return (
    <Page>
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

      <PageBand>
        <MetricStrip metrics={metrics} dividers={false} />
      </PageBand>

      <PageTabs tabs={TABS} defaultValue="services">
        <TabsContent value="services">
          <PageContent>
            <Banner
              tone="warning"
              title="Loki is running, but log queries are unavailable"
              detail="Readiness probe timed out at 09:35 UTC · Durable run evidence remains available."
              action={<InlineLink>Inspect dependency path</InlineLink>}
            />
            <SplitRow rail={rail}>
              <Section title="Enabled services" meta="12 services · Unready first · Last probe 09:35 UTC">
                {services.data ? <ServiceHealthTable rows={services.data} /> : null}
              </Section>
              <div className="flex gap-5.5">
                <Section title="Backup coverage" meta="03:00 UTC" className="flex-1">
                  <EvidenceListPlain
                    rows={(backup.data ?? []).map((row) => ({
                      label: row.name,
                      value: row.outcome,
                      tone: TONE_CLASS[row.tone],
                    }))}
                  />
                  <InlineLink>Inspect backup manifest</InlineLink>
                </Section>
                <Section title="Maintenance & recovery" className="flex-1">
                  <EvidenceListPlain
                    rows={(maintenance.data ?? []).map((row) => ({
                      label: row.name,
                      value: row.outcome,
                      tone: TONE_CLASS[row.tone],
                    }))}
                  />
                  <InlineLink>View recovery evidence</InlineLink>
                </Section>
              </div>
            </SplitRow>
          </PageContent>
        </TabsContent>

        <TabsContent value="packages">
          <PageContent rail={rail}>
            <Section title="Packages" meta="16 installed · 4 not enabled">
              <PropertyList
                rows={(services.data ?? []).map((service) => ({
                  label: service.name,
                  value: `${service.role} · ${service.readiness_state}`,
                }))}
              />
            </Section>
          </PageContent>
        </TabsContent>

        <TabsContent value="maintenance">
          <PageContent rail={rail}>
            <Section title="Maintenance & recovery" meta="Last run 03:00 UTC">
              <PropertyList
                rows={(maintenance.data ?? []).map((row) => ({
                  label: row.name,
                  value: row.outcome,
                }))}
              />
            </Section>
          </PageContent>
        </TabsContent>

        <TabsContent value="backup">
          <PageContent rail={rail}>
            <Section title="Backup coverage" meta="03:00 UTC · 4/4 contributors">
              <PropertyList
                rows={(backup.data ?? []).map((row) => ({ label: row.name, value: row.outcome }))}
              />
            </Section>
          </PageContent>
        </TabsContent>

        <TabsContent value="readiness">
          <PageContent rail={rail}>
            <Section title="Readiness" meta="11 of 12 ready">
              <PropertyList
                rows={(services.data ?? []).map((service) => ({
                  label: service.name,
                  value: `${service.readiness_state} · ${service.probe}`,
                }))}
              />
            </Section>
          </PageContent>
        </TabsContent>
      </PageTabs>
    </Page>
  );
}

export const Route = createFileRoute("/platform")({ component: PlatformPage });
