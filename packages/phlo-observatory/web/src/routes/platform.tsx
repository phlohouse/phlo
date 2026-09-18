/**
 * Platform route: service readiness, backup coverage and recovery evidence.
 * Section implementations live under `components/sections/platform`.
 */
import { createFileRoute } from "@tanstack/react-router";

import type {Metric} from "@/components/data/metric-strip";
import {  MetricStrip } from "@/components/data/metric-strip";
import { PropertyList } from "@/components/data/property-list";
import { Banner } from "@/components/feedback/banner";
import { Page, PageBand, PageContent, PageStack } from "@/components/layout/page";
import { ExampleDataChip, PageHeader } from "@/components/layout/page-header";
import { PageTabs, TabsContent } from "@/components/layout/page-tabs";
import { InlineLink, Section } from "@/components/layout/section-header";
import { ServiceDiagnosticRail } from "@/components/sections/platform/service-diagnostic-rail";
import { ServiceHealthTable } from "@/components/sections/platform/service-health-table";
import { EvidenceListPlain } from "@/components/data/evidence-list";
import { Button } from "@/components/ui/button";
import {
  backupCoverage,
  dependencyPath,
  lokiDetail,
  maintenanceRows,
  platformMetrics,
  platformServices,
} from "@/data/demo";

const METRICS: Array<Metric> = platformMetrics;

const TABS = [
  { value: "services", label: "Services · 12" },
  { value: "packages", label: "Packages" },
  { value: "maintenance", label: "Maintenance" },
  { value: "backup", label: "Backup & restore" },
  { value: "readiness", label: "Readiness" },
];

const CAPABILITIES = [
  { label: "Support channel", value: "Alpha", tone: "text-warning" },
  { label: "Production readiness", value: "Not certified", tone: "text-warning" },
];

function PlatformPage() {
  const rail = (
    <ServiceDiagnosticRail
      serviceName="Loki"
      state="Not ready"
      stateTone="text-warning"
      summary="Container is running. /ready timed out after 5 seconds; log queries also failed."
      facts={lokiDetail}
      dependencies={dependencyPath}
      dependencyNote="Collector readiness does not confirm log delivery. Check exporter retries and backend ingestion."
      capabilityChecks="Compatibility checks: 16 / 16 passed"
      capabilities={CAPABILITIES}
    />
  );

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
        <MetricStrip metrics={METRICS} dividers={false} />
      </PageBand>

      <PageTabs tabs={TABS} defaultValue="services">
        <TabsContent value="services">
          <div className="px-6 pt-4.5 pb-5">
            <Banner
              tone="warning"
              title="Loki is running, but log queries are unavailable"
              detail="Readiness probe timed out at 09:35 UTC · Durable run evidence remains available."
              action={<InlineLink>Inspect dependency path</InlineLink>}
              className="mb-4.5"
            />
            <PageContent rail={rail}>
              <Section title="Enabled services" meta="12 services · Unready first · Last probe 09:35 UTC">
                <ServiceHealthTable rows={platformServices} />
              </Section>
              <div className="flex gap-5.5">
                <Section title="Backup coverage" meta="03:00 UTC" className="flex-1">
                  <EvidenceListPlain
                    rows={backupCoverage.map((row) => ({
                      label: row.name,
                      value: row.outcome,
                      tone: "text-success",
                    }))}
                  />
                  <InlineLink>Inspect backup manifest</InlineLink>
                </Section>
                <Section title="Maintenance & recovery" className="flex-1">
                  <EvidenceListPlain
                    rows={maintenanceRows.map((row) => ({
                      label: row.name,
                      value: row.outcome,
                    }))}
                  />
                  <InlineLink>View recovery evidence</InlineLink>
                </Section>
              </div>
            </PageContent>
          </div>
        </TabsContent>

        <TabsContent value="packages">
          <PageContent rail={rail}>
            <Section title="Packages" meta="16 installed · 4 not enabled">
              <PropertyList
                rows={platformServices.map((service) => ({
                  label: service.name,
                  value: `${service.role} · ${service.readiness}`,
                }))}
              />
            </Section>
          </PageContent>
        </TabsContent>

        <TabsContent value="maintenance">
          <PageContent rail={rail}>
            <Section title="Maintenance & recovery" meta="Last run 03:00 UTC">
              <PropertyList
                rows={maintenanceRows.map((row) => ({ label: row.name, value: row.outcome }))}
              />
            </Section>
          </PageContent>
        </TabsContent>

        <TabsContent value="backup">
          <PageContent rail={rail}>
            <Section title="Backup coverage" meta="03:00 UTC · 4/4 contributors">
              <PropertyList
                rows={backupCoverage.map((row) => ({ label: row.name, value: row.outcome }))}
              />
            </Section>
          </PageContent>
        </TabsContent>

        <TabsContent value="readiness">
          <PageContent rail={rail}>
            <Section title="Readiness" meta="11 of 12 ready">
              <PropertyList
                rows={platformServices.map((service) => ({
                  label: service.name,
                  value: `${service.readiness} · ${service.probe}`,
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
