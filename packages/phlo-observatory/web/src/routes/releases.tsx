/**
 * Releases route: promotion control, candidate review and release history.
 * Section implementations live under `components/sections/releases`.
 */
import { createFileRoute, useNavigate } from "@tanstack/react-router";
import { useQuery } from "@tanstack/react-query";
import { parseAsString, useQueryState } from "nuqs";

import type {Metric} from "@/components/data/metric-strip";
import { queries } from "@/api/mission-control";

import {  MetricStrip } from "@/components/data/metric-strip";
import { Page, PageBand, PageContent } from "@/components/layout/page";
import { PageHeader, ReadStateChip } from "@/components/layout/page-header";
import { PageTabs, TabsContent } from "@/components/layout/page-tabs";
import { Section } from "@/components/layout/section-header";
import { CandidateDetailPanel } from "@/components/sections/releases/candidate-detail-panel";
import { CompletedReleasesTable } from "@/components/sections/releases/completed-releases-table";
import { PendingCandidatesTable } from "@/components/sections/releases/pending-candidates-table";

const TABS = [
  { value: "pending", label: "Pending" },
  { value: "history", label: "History" },
];

function ReleasesPage() {
  const navigate = useNavigate();
  const openDataset = (datasetId: string | undefined) => {
    if (datasetId) navigate({ to: "/datasets/$", params: { _splat: datasetId } });
  };

  const summary = useQuery(queries.releaseSummary());
  const candidates = useQuery(queries.releaseCandidates());
  const completed = useQuery(queries.completedReleases());
  const [selectedId, setSelectedId] = useQueryState("candidate", parseAsString);
  const [tab, setTab] = useQueryState(
    "tab",
    parseAsString.withDefault("pending").withOptions({ clearOnDefault: true }),
  );
  const resolvedSelectedId =
    selectedId !== null && candidates.data?.data?.some((row) => row.id === selectedId)
      ? selectedId
      : (candidates.data?.data?.[0]?.id ?? null);
  const detail = useQuery({
    ...queries.releaseCandidate(resolvedSelectedId ?? "__none__"),
    enabled: resolvedSelectedId !== null,
  });

  const metrics: Array<Metric> = (summary.data?.data ?? []).map((metric) => ({
    label: metric.label,
    value: metric.value,
    hint: metric.hint,
  }));
  const selected = resolvedSelectedId;

  return (
    <Page>
      <PageHeader
        title="Releases"
        titleAccessory={<ReadStateChip evidence={summary.data?.evidence} />}
        description="Promote audited snapshots from a staging branch into the catalog consumers read."
      />

      <PageBand>
        <MetricStrip metrics={metrics} dividers={false} />
      </PageBand>

      <PageTabs tabs={TABS} value={tab} onValueChange={(value) => void setTab(value)}>
        <TabsContent value="pending">
          <PageContent>
            <Section
              title="Pending candidates"
              meta={`${candidates.data?.data?.length ?? 0} pending`}
            >
              {candidates.data?.data ? (
                <PendingCandidatesTable
                  rows={candidates.data.data}
                  selectedId={selected}
                  onSelect={(row) => void setSelectedId(row.id)}
                />
              ) : null}
            </Section>
            {(() => {
              const candidateDetail = detail.data?.data;
              return candidateDetail ? (
                <CandidateDetailPanel
                  key={candidateDetail.id}
                  candidate={candidateDetail}
                  onOpenDataset={() => openDataset(candidateDetail.candidate?.dataset)}
                />
              ) : null;
            })()}
          </PageContent>
        </TabsContent>

        <TabsContent value="history">
          <PageContent>
            <Section
              title="Completed releases"
              meta={`${completed.data?.data?.length ?? 0} recorded`}
            >
              {completed.data?.data ? <CompletedReleasesTable rows={completed.data.data} /> : null}
            </Section>
          </PageContent>
        </TabsContent>
      </PageTabs>
    </Page>
  );
}

export const Route = createFileRoute("/releases")({
  validateSearch: (
    search: Record<string, unknown>,
  ): { candidate?: string; tab?: string } => ({
    candidate:
      typeof search.candidate === "string" && search.candidate ? search.candidate : undefined,
    tab: typeof search.tab === "string" && search.tab ? search.tab : undefined,
  }),
  component: ReleasesPage,
});
