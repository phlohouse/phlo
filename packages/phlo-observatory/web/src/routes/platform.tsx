/**
 * Platform route: service readiness, backup coverage and recovery evidence.
 * Section implementations live under `components/sections/platform`.
 */
import { createFileRoute } from "@tanstack/react-router";
import { useQuery } from "@tanstack/react-query";
import { parseAsString, useQueryState } from "nuqs";

import type {Metric} from "@/components/data/metric-strip";
import { queries } from "@/api/mission-control";

import {  MetricStrip } from "@/components/data/metric-strip";
import { PropertyList } from "@/components/data/property-list";
import { Banner } from "@/components/feedback/banner";
import { Page, PageBand, PageContent, SplitRow } from "@/components/layout/page";
import { PageHeader, ReadStateChip } from "@/components/layout/page-header";
import { PageTabs, TabsContent } from "@/components/layout/page-tabs";
import { Section } from "@/components/layout/section-header";
import { ServiceDiagnosticRail } from "@/components/sections/platform/service-diagnostic-rail";
import { ServiceHealthTable } from "@/components/sections/platform/service-health-table";
import { EvidenceListPlain } from "@/components/data/evidence-list";

const TABS = [
  { value: "services", label: "Services" },
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
  const [selectedParam, setSelectedParam] = useQueryState("service", parseAsString);
  const [tab, setTab] = useQueryState(
    "tab",
    parseAsString.withDefault("services").withOptions({ clearOnDefault: true }),
  );
  const summary = useQuery(queries.platformSummary());
  const services = useQuery(queries.platformServices());
  // The deep-linked service wins; otherwise follow whichever service actually
  // needs attention rather than a fixed name.
  const unreadyService = services.data?.data?.find((service) => service.attention);
  const selectedService =
    services.data?.data?.find((service) => service.name === selectedParam) ?? unreadyService;
  const selected = selectedService?.name ?? "";
  const diagnostics = useQuery({
    ...queries.serviceDiagnostics(selected),
    enabled: Boolean(selected),
  });
  const backup = useQuery(queries.backupCoverage());
  const maintenance = useQuery(queries.maintenance());

  const metrics: Array<Metric> = (summary.data?.data ?? []).map((metric) => ({
    label: metric.label,
    value: metric.value,
    hint: metric.hint,
    tone: TONE_CLASS[metric.tone],
  }));

  const diag = diagnostics.data?.data ?? undefined;
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
        titleAccessory={<ReadStateChip evidence={summary.data?.evidence} />}
        description="Service readiness, operational coverage, and recovery evidence in one place."
      />

      <PageBand>
        <MetricStrip metrics={metrics} dividers={false} />
      </PageBand>

      <PageTabs tabs={TABS} value={tab} onValueChange={(value) => void setTab(value)}>
        <TabsContent value="services">
          <PageContent>
            {unreadyService ? (
              <Banner
                tone="warning"
                title={`${unreadyService.name} is ${unreadyService.runtime_state.toLowerCase()}, but readiness is ${unreadyService.readiness_state.toLowerCase()}`}
                detail={
                  selectedService?.name === unreadyService.name
                    ? (diag?.summary ?? unreadyService.probe)
                    : unreadyService.probe
                }
              />
            ) : null}
            <SplitRow rail={rail}>
              <Section title="Enabled services" meta={`${services.data?.data?.length ?? 0} services · Unready first`}>
                {services.data?.data ? (
                  <ServiceHealthTable
                    rows={services.data.data}
                    selectedKey={selected || null}
                    onRowClick={(row) => void setSelectedParam(row.name)}
                  />
                ) : null}
              </Section>
              <div className="flex gap-5.5">
                <Section title="Backup coverage"  className="flex-1">
                  <EvidenceListPlain
                    rows={(backup.data?.data ?? []).map((row) => ({
                      label: row.name,
                      value: row.outcome,
                      tone: TONE_CLASS[row.tone],
                    }))}
                  />
                </Section>
                <Section title="Maintenance & recovery" className="flex-1">
                  <EvidenceListPlain
                    rows={(maintenance.data?.data ?? []).map((row) => ({
                      label: row.name,
                      value: row.outcome,
                      tone: TONE_CLASS[row.tone],
                    }))}
                  />
                </Section>
              </div>
            </SplitRow>
          </PageContent>
        </TabsContent>

        <TabsContent value="packages">
          <PageContent rail={rail}>
            <Section title="Packages" meta={`${services.data?.data?.length ?? 0} installed`}>
              <PropertyList
                rows={(services.data?.data ?? []).map((service) => ({
                  label: service.name,
                  value: `${service.role} · ${service.readiness_state}`,
                }))}
              />
            </Section>
          </PageContent>
        </TabsContent>

        <TabsContent value="maintenance">
          <PageContent rail={rail}>
            <Section title="Maintenance & recovery" >
              <PropertyList
                rows={(maintenance.data?.data ?? []).map((row) => ({
                  label: row.name,
                  value: row.outcome,
                }))}
              />
            </Section>
          </PageContent>
        </TabsContent>

        <TabsContent value="backup">
          <PageContent rail={rail}>
            <Section title="Backup coverage" >
              <PropertyList
                rows={(backup.data?.data ?? []).map((row) => ({ label: row.name, value: row.outcome }))}
              />
            </Section>
          </PageContent>
        </TabsContent>

        <TabsContent value="readiness">
          <PageContent rail={rail}>
            <Section title="Readiness" meta={`${services.data?.data?.filter((service) => service.readiness_state === "Ready").length ?? 0} of ${services.data?.data?.length ?? 0} ready`}>
              <PropertyList
                rows={(services.data?.data ?? []).map((service) => ({
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

export const Route = createFileRoute("/platform")({
  validateSearch: (search: Record<string, unknown>): { service?: string; tab?: string } => ({
    service: typeof search.service === "string" && search.service ? search.service : undefined,
    tab: typeof search.tab === "string" && search.tab ? search.tab : undefined,
  }),
  component: PlatformPage,
});
