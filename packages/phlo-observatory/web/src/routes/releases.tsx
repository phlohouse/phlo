/**
 * Releases route: promotion control, candidate review and release history.
 * Section implementations live under `components/sections/releases`.
 */
import { createFileRoute, useNavigate } from "@tanstack/react-router";
import { useQuery } from "@tanstack/react-query";

import type {Metric} from "@/components/data/metric-strip";
import { queries } from "@/api/mission-control";

import {  MetricStrip } from "@/components/data/metric-strip";
import { PropertyList } from "@/components/data/property-list";
import { Page, PageBand, PageContent } from "@/components/layout/page";
import { ExampleDataChip, PageHeader } from "@/components/layout/page-header";
import { PageTabs, TabsContent } from "@/components/layout/page-tabs";
import { Section } from "@/components/layout/section-header";
import { CandidateDetailPanel } from "@/components/sections/releases/candidate-detail-panel";
import { CompletedReleasesTable } from "@/components/sections/releases/completed-releases-table";
import { PendingCandidatesTable } from "@/components/sections/releases/pending-candidates-table";
import { Button } from "@/components/ui/button";

const TABS = [
  { value: "pending", label: "Pending · 2" },
  { value: "history", label: "History" },
  { value: "operations", label: "Operations" },
  { value: "providers", label: "Provider capabilities" },
];

const PROVIDER_ROWS = [
  { label: "Polaris", value: "Snapshot publication · audited" },
  { label: "Nessie", value: "Branch merge · revision checked" },
  { label: "Evidence gate", value: "Blocking" },
  { label: "Reconcile", value: "Manual" },
];

function ReleasesPage() {
  const navigate = useNavigate();
  const openDataset = () => navigate({ to: "/datasets/orders" });

  const summary = useQuery(queries.releaseSummary());
  const candidates = useQuery(queries.releaseCandidates());
  const completed = useQuery(queries.completedReleases());
  const selectedId = candidates.data?.[0]?.id ?? null;
  const detail = useQuery({
    ...queries.releaseCandidate(selectedId ?? "__none__"),
    enabled: selectedId !== null,
  });

  const metrics: Array<Metric> = (summary.data ?? []).map((metric) => ({
    label: metric.label,
    value: metric.value,
    hint: metric.hint,
  }));
  const selected = selectedId;

  return (
    <Page>
      <PageHeader
        title="Releases"
        titleAccessory={<ExampleDataChip />}
        description="Review candidate data, inspect evidence, and track what reaches consumers."
        actions={
          <>
            <Button variant="outline">Last 24 hours</Button>
            <Button>View release history</Button>
          </>
        }
      />

      <PageBand>
        <MetricStrip metrics={metrics} dividers={false} />
      </PageBand>

      <PageTabs tabs={TABS} defaultValue="pending">
        <TabsContent value="pending">
          <PageContent>
            <Section title="Pending candidates" meta="All providers · Sorted by newest">
              {candidates.data ? (
                <PendingCandidatesTable rows={candidates.data} selectedId={selected} />
              ) : null}
            </Section>
            {detail.data ? (
              <CandidateDetailPanel candidate={detail.data} onOpenDataset={openDataset} />
            ) : null}
            <Section title="Latest completed releases" meta="2 of 18 in the last 24 hours">
              {completed.data ? <CompletedReleasesTable rows={completed.data} /> : null}
            </Section>
          </PageContent>
        </TabsContent>

        <TabsContent value="history">
          <PageContent>
            <Section title="History" meta="18 in the last 24 hours">
              {completed.data ? <CompletedReleasesTable rows={completed.data} /> : null}
            </Section>
          </PageContent>
        </TabsContent>

        <TabsContent value="operations">
          <PageContent>
            <Section title="Operations" meta="18 in the last 24 hours">
              {completed.data ? <CompletedReleasesTable rows={completed.data} /> : null}
            </Section>
          </PageContent>
        </TabsContent>

        <TabsContent value="providers">
          <PageContent>
            <Section title="Provider capabilities" meta="Polaris · Nessie">
              <PropertyList rows={PROVIDER_ROWS} />
            </Section>
          </PageContent>
        </TabsContent>
      </PageTabs>
    </Page>
  );
}

export const Route = createFileRoute("/releases")({ component: ReleasesPage });
